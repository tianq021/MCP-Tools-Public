"""独立建设奖励 v1 的离线单元测试（不启动游戏）。

覆盖任务书第四步的关键要求：
- 每座新的己方已放置建筑 +1.0
- 单帧误判防护（需连续 2 次观测确认）
- 同一建筑实体不重复领奖
- 非玩家/未放置/limbo 建筑不计分
- 只启用 building_created，战斗奖励全部禁用
- 原始 V4 / 战斗奖励配置未被修改（SHA256 校验）
- 事件记录 JSONL 落盘且可由外部审计

运行：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\test_build_reward.py
"""
import copy
import hashlib
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_reward as BR  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print('PASS  %s' % name)
    else:
        FAIL += 1
        print('FAIL  %s  %s' % (name, detail))


def building(addr, name='Allied Power Plant', cell=(10, 20), owner=777,
             on_map=True, in_limbo=False, health=750, abstract='ABSTRACT_TYPE_BUILDING'):
    return {'addr': addr, 'type_name': name, 'cell': list(cell), 'owner_ptr': owner,
            'on_map': on_map, 'in_limbo': in_limbo, 'health': health,
            'abstract': abstract}


def main():
    cfg = BR.load_config()
    check('config schema loads', cfg['schema_version'] == 'build_reward_v1')
    check('only building_created rule exists', set(cfg['rules']) == {'building_created'})
    check('building_created score == 1.0', cfg['rules']['building_created']['score'] == 1.0)
    check('confirmation >= 2 frames',
          cfg['event_confirmation']['required_consecutive_observations'] >= 2)
    rev1 = BR.config_revision(cfg)
    rev2 = BR.config_revision(BR.load_config())
    check('config revision deterministic', rev1 == rev2)

    # --- 基本计分 + 单帧防护 + 去重 -----------------------------------------
    with tempfile.TemporaryDirectory() as td:
        log = pathlib.Path(td) / 'build-events.jsonl'
        ledger = BR.BuildRewardLedger(cfg)
        ledger.set_episode('ep-test', log)
        ev1 = ledger.observe([building(1001)], hero_ptr=777, step=1, frame=10, t=1.0)
        check('single frame does not award (anti-misjudgment)', ev1 == [], ev1)
        ev2 = ledger.observe([building(1001)], hero_ptr=777, step=2, frame=20, t=2.0)
        check('second consecutive frame awards +1.0', len(ev2) == 1 and ev2[0]['reward'] == 1.0,
              ev2)
        check('event via=production', (ev2[0]['via'] if ev2 else None) == 'production')
        ev3 = ledger.observe([building(1001)], hero_ptr=777, step=3, frame=30, t=3.0)
        check('same entity never awarded twice', ev3 == [])
        check('totals only counted once', ledger.totals['building_created'] == 1.0)
        check('awarded entity removed from candidates',
              all(ident[0] != 1001 for ident in ledger.candidates))

        # --- 非玩家建筑 / 未放置 / limbo / 死亡 不计分 ----------------------
        other = building(2001, owner=888)
        ledger.observe([other], hero_ptr=777, step=4, frame=40, t=4.0)
        ledger.observe([other], hero_ptr=777, step=5, frame=50, t=5.0)
        check('owner mismatch never awarded', ledger.counts['building_created'] == 1)
        limbo = building(3001, on_map=False, in_limbo=True)
        ledger.observe([limbo], hero_ptr=777, step=6, frame=60, t=6.0)
        ledger.observe([limbo], hero_ptr=777, step=7, frame=70, t=7.0)
        check('limbo/off-map building never awarded', ledger.counts['building_created'] == 1)
        dead = building(4001, health=0)
        ledger.observe([dead], hero_ptr=777, step=8, frame=80, t=8.0)
        ledger.observe([dead], hero_ptr=777, step=9, frame=90, t=9.0)
        check('dead building never awarded', ledger.counts['building_created'] == 1)

        # --- 消失后重新出现需要重新连续确认 --------------------------------
        a = building(5001)
        ledger.observe([a], hero_ptr=777, step=10, frame=100, t=10.0)
        ledger.observe([], hero_ptr=777, step=11, frame=110, t=11.0)  # 消失
        e = ledger.observe([a], hero_ptr=777, step=12, frame=120, t=12.0)
        check('reappearance needs consecutive confirmations again (1st)', e == [])
        e = ledger.observe([a], hero_ptr=777, step=13, frame=130, t=13.0)
        check('reappearance confirmed after 2 consecutive frames',
              len(e) == 1 and ledger.totals['building_created'] == 2.0, e)

        # --- 多建筑同帧各自计分 --------------------------------------------
        before = ledger.counts['building_created']
        b1, b2 = building(6001, cell=(1, 2)), building(6002, cell=(3, 4))
        ledger.observe([b1, b2], hero_ptr=777, step=14, frame=140, t=14.0)
        e = ledger.observe([b1, b2], hero_ptr=777, step=15, frame=150, t=15.0)
        check('two new buildings in one step each award',
              len(e) == 2 and ledger.counts['building_created'] == before + 2, e)

        # --- MCV 展开建造厂标记 via=mcv_deploy -----------------------------
        cy = building(7001, name='Allied Construction Yard', cell=(9, 9))
        ledger.observe([cy], hero_ptr=777, step=16, frame=160, t=16.0)
        e = ledger.observe([cy], hero_ptr=777, step=17, frame=170, t=17.0)
        check('construction yard tagged via=mcv_deploy',
              len(e) == 1 and e[0]['via'] == 'mcv_deploy', e)
        check('mcv_deploy counter incremented', ledger.counts['mcv_deploy'] == 1)

        # --- 事件日志可审计 ------------------------------------------------
        lines = [json.loads(x) for x in log.read_text(encoding='utf-8').splitlines() if x]
        check('event log lines == awarded buildings',
              len(lines) == ledger.counts['building_created'],
              (len(lines), ledger.counts['building_created']))
        check('event log has required fields',
              all({'ts', 'event', 'addr', 'type_name', 'cell', 'reward', 'episode_id',
                   'frame', 'reward_version'} <= set(x) for x in lines))
        distinct = {(x['addr'], x['type_name'], tuple(x['cell'])) for x in lines}
        check('no duplicate entity in event log', len(distinct) == len(lines))

        # --- 同地址不同类型/位置视为不同实体 ------------------------------
        l2 = BR.BuildRewardLedger(cfg)
        l2.set_episode('ep-2')
        o1 = building(8001, name='Allied Power Plant', cell=(1, 1))
        o2 = building(8001, name='Allied Ore Refinery', cell=(1, 1))
        l2.observe([o1], hero_ptr=777, step=1, frame=1, t=1.0)
        l2.observe([o1], hero_ptr=777, step=2, frame=2, t=2.0)
        l2.observe([o2], hero_ptr=777, step=3, frame=3, t=3.0)
        l2.observe([o2], hero_ptr=777, step=4, frame=4, t=4.0)
        check('same addr but different type = different entity',
              l2.counts['building_created'] == 2)

        # --- 配置可关闭建造厂奖励 ------------------------------------------
        cfg_nocy = copy.deepcopy(cfg)
        cfg_nocy['include_construction_yard_from_mcv'] = False
        l3 = BR.BuildRewardLedger(cfg_nocy)
        l3.set_episode('ep-3')
        l3.observe([cy], hero_ptr=777, step=1, frame=1, t=1.0)
        l3.observe([cy], hero_ptr=777, step=2, frame=2, t=2.0)
        check('construction yard can be disabled by config',
              l3.counts['building_created'] == 0
              and l3.counts['skipped_construction_yard_disabled'] >= 1)

    # --- 配置校验：战斗奖励项不得混入 --------------------------------------
    bad = copy.deepcopy(cfg)
    bad['rules']['opponent_hp'] = {'enabled': True, 'score': 1.0, 'unit': 1.0,
                                   'step_cap': None, 'episode_cap': None,
                                   'max_events': None, 'description': 'x'}
    try:
        BR.validate_config(bad)
        check('config rejects combat reward items', False)
    except BR.BuildRewardConfigError:
        check('config rejects combat reward items', True)
    bad2 = copy.deepcopy(cfg)
    bad2['event_confirmation']['required_consecutive_observations'] = 1
    try:
        BR.validate_config(bad2)
        check('config rejects single-frame confirmation', False)
    except BR.BuildRewardConfigError:
        check('config rejects single-frame confirmation', True)

    # --- 原始 V4/战斗奖励配置未被修改 -------------------------------------
    protected = cfg['protected_originals']
    for name, path_key in (('combat', 'combat_reward_config'),
                           ('v4', 'reward_v4_config')):
        path = ROOT / protected[path_key]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = protected[path_key + '_sha256']
        check('original %s reward config unchanged' % name, actual == expected,
              (actual, expected))
    for name in ('config/rewards.json', 'config/rewards-combat.json'):
        text = (ROOT / name).read_text(encoding='utf-8-sig')
        check('%s has no building_created rule' % name, 'building_created' not in text)

    print('\nRESULT: %d passed, %d failed' % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
