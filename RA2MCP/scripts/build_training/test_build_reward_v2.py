"""build_reward_v2 快速离线自检（新 V2 账本逻辑；不重复 V1 已完成的测试）。"""
import copy
import hashlib
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_reward_v2 as BR  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('PASS ', name)
    else:
        FAIL += 1
        print('FAIL ', name, detail)


def obj(kind, addr, name, cell=(10, 10), owner=777, on_map=True, in_limbo=False,
        health=500):
    abstract = 'ABSTRACT_TYPE_BUILDING' if kind == 'building' else (
        'ABSTRACT_TYPE_INFANTRY' if 'GI' in name else 'ABSTRACT_TYPE_UNIT')
    return {'addr': addr, 'type_name': name, 'cell': list(cell), 'owner_ptr': owner,
            'on_map': on_map, 'in_limbo': in_limbo, 'health': health,
            'abstract': abstract}


def main():
    cfg = BR.load_config()
    check('config loads', cfg['schema_version'] == 'build_reward_v2')
    check('both rules present', set(cfg['rules']) == {'building_created', 'unit_created'})
    check('building reward == 1.0', cfg['rules']['building_created']['score'] == 1.0)
    check('unit reward == 0.2', cfg['rules']['unit_created']['score'] == 0.2)
    check('unit per-type cap == 5',
          cfg['rules']['unit_created']['per_type_episode_cap'] == 5)
    check('building per-type cap == 2',
          cfg['rules']['building_created']['per_type_episode_cap'] == 2)
    check('power deficit bonus == 1.0',
          cfg['rules']['building_created'].get('deficit_bonus_score') == 1.0)

    with tempfile.TemporaryDirectory() as td:
        log = pathlib.Path(td) / 'events.jsonl'
        ledger = BR.BuildRewardLedgerV2(cfg)
        ledger.set_episode('ep1', log)
        cy = obj('building', 100, 'Allied Construction Yard')
        pp = obj('building', 101, 'Allied Power Plant', cell=(12, 12))
        tank = obj('unit', 200, 'Grizzly Battle Tank')
        gi = obj('unit', 201, 'GI')
        ledger.prime([cy, pp, tank, gi], hero_ptr=777)
        check('prime marks 4 preexisting', len(ledger._preexisting) == 4)
        e = ledger.observe([cy, pp, tank, gi], hero_ptr=777, step=1, frame=10, t=1.0)
        check('preexisting entities never rewarded', e == [])

        # 新建筑：单帧不发奖，第 2 帧发 +1.0
        bar = obj('building', 102, 'Allied Barracks', cell=(14, 14))
        e = ledger.observe([cy, pp, tank, gi, bar], 777, 2, 20, 2.0)
        check('new building single frame no award', e == [])
        e = ledger.observe([cy, pp, tank, gi, bar], 777, 3, 30, 3.0)
        check('new building +1.0 after 2 frames',
              len(e) == 1 and e[0]['reward'] == 1.0 and e[0]['kind'] == 'building', e)
        e = ledger.observe([cy, pp, tank, gi, bar], 777, 4, 40, 4.0)
        check('same building not awarded twice', e == [])
        check('building total == 1.0', ledger.totals['building_created'] == 1.0)

        # 新单位：+0.2
        new_tank = obj('unit', 202, 'Grizzly Battle Tank', cell=(20, 20))
        ledger.observe([cy, pp, tank, gi, bar, new_tank], 777, 5, 50, 5.0)
        e = ledger.observe([cy, pp, tank, gi, bar, new_tank], 777, 6, 60, 6.0)
        check('new unit +0.2 after 2 frames',
              len(e) == 1 and e[0]['kind'] == 'unit' and abs(e[0]['reward'] - 0.2) < 1e-9, e)
        ledger.observe([cy, pp, tank, gi, bar, new_tank], 777, 7, 70, 7.0)
        check('same unit not awarded twice', ledger.counts['unit_created'] == 1)

        # 地址复用：单位消失后同地址再出现 = 新实体（generation+1）
        addr = 300
        u1 = obj('unit', addr, 'GI', cell=(30, 30))
        ledger.observe([u1], 777, 8, 80, 8.0)
        e = ledger.observe([u1], 777, 9, 90, 9.0)
        check('unit first lifetime awarded', len(e) == 1 and e[0]['generation'] == 1, e)
        ledger.observe([], 777, 10, 100, 10.0)   # 消失
        u2 = obj('unit', addr, 'GI', cell=(31, 30))   # 地址复用
        ledger.observe([u2], 777, 11, 110, 11.0)
        e = ledger.observe([u2], 777, 12, 120, 12.0)
        check('address reuse counted as new entity (generation 2)',
              len(e) == 1 and e[0]['generation'] == 2 and e[0]['reward'] > 0, e)
        check('unit total == 0.6', abs(ledger.totals['unit_created'] - 0.6) < 1e-6,
              ledger.totals)

        # 未放置/limbo 不计
        limbo = obj('building', 400, 'Allied Ore Refinery', on_map=False, in_limbo=True)
        ledger.observe([limbo], 777, 13, 130, 13.0)
        ledger.observe([limbo], 777, 14, 140, 14.0)
        check('limbo building ignored', ledger.counts['building_created'] == 1)

        # 事件日志
        lines = [json.loads(x) for x in log.read_text(encoding='utf-8').splitlines() if x]
        check('event log lines == awarded entities',
              len(lines) == ledger.counts['building_created'] + ledger.counts['unit_created'],
              (len(lines), ledger.counts))
        check('events include kind/addr/generation/reward',
              all({'kind', 'addr', 'generation', 'reward', 'frame', 'episode_id'} <= set(x)
                  for x in lines))

        # 单位类型上限
        cfg_cap = copy.deepcopy(cfg)
        cfg_cap['rules']['unit_created']['per_type_episode_cap'] = 2
        l2 = BR.BuildRewardLedgerV2(cfg_cap)
        l2.set_episode('ep-cap')
        units = [obj('unit', 500 + i, 'GI', cell=(40 + i, 40)) for i in range(4)]
        l2.observe(units, 777, 1, 1, 1.0)
        e = l2.observe(units, 777, 2, 2, 2.0)
        check('per-type cap: only 2 GI rewarded',
              l2.counts['unit_created'] == 2 and l2.counts['unit_capped'] >= 2,
              (l2.counts, len(e)))

        # 建筑类型上限（防止反复造同一种便宜建筑刷分）
        l3 = BR.BuildRewardLedgerV2(cfg)
        l3.set_episode('ep-bcap')
        for i in range(4):
            b = obj('building', 600 + i, 'Allied Barracks', cell=(50 + i, 50))
            l3.observe([b], 777, i * 2 + 1, i * 2 + 1, 1.0)
            e = l3.observe([b], 777, i * 2 + 2, i * 2 + 2, 2.0)
        check('building per-type cap: only 2 barracks rewarded',
              l3.counts['building_created'] == 2
              and l3.counts['building_capped'] >= 2, l3.counts)

        # 缺电时新建电厂：额外 +1.0（总 +2.0）
        l4 = BR.BuildRewardLedgerV2(cfg)
        l4.set_episode('ep-power')
        pp2 = obj('building', 700, 'Allied Power Plant', cell=(60, 60))
        l4.observe([pp2], 777, 1, 1, 1.0, power_output=100, power_drain=300)
        e = l4.observe([pp2], 777, 2, 2, 2.0, power_output=100, power_drain=300)
        check('power plant under deficit: +1.0 base +1.0 bonus',
              len(e) == 1 and abs(e[0]['reward'] - 2.0) < 1e-9
              and abs(e[0]['deficit_bonus'] - 1.0) < 1e-9,
              e)
        check('power deficit bonus counted',
              l4.counts['power_plant_deficit_bonuses'] == 1)
        # 非缺电时电厂不额外奖励
        l5 = BR.BuildRewardLedgerV2(cfg)
        l5.set_episode('ep-power2')
        pp3 = obj('building', 701, 'Allied Power Plant', cell=(61, 60))
        l5.observe([pp3], 777, 1, 1, 1.0, power_output=500, power_drain=100)
        e = l5.observe([pp3], 777, 2, 2, 2.0, power_output=500, power_drain=100)
        check('power plant without deficit: base +1.0 only',
              len(e) == 1 and abs(e[0]['reward'] - 1.0) < 1e-9, e)

    # 配置校验
    bad = copy.deepcopy(cfg)
    bad['rules']['kill_reward'] = dict(bad['rules']['unit_created'])
    try:
        BR.validate_config(bad)
        check('config rejects combat rule', False)
    except BR.BuildRewardV2ConfigError:
        check('config rejects combat rule', True)
    bad2 = copy.deepcopy(cfg)
    bad2['rules']['building_created']['confirm_frames'] = 1
    try:
        BR.validate_config(bad2)
        check('config rejects 1-frame building confirm', False)
    except BR.BuildRewardV2ConfigError:
        check('config rejects 1-frame building confirm', True)

    # 原始 V4 / 战斗 / V1 奖励配置未变
    protected = cfg['protected_originals']
    for key in ('combat_reward_config', 'reward_v4_config'):
        p = ROOT / protected[key]
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        check('%s unchanged' % key, actual == protected[key + '_sha256'], actual)
    v1 = ROOT / protected['reward_build_v1_config']
    check('reward_build_v1.json still exists', v1.exists())

    print('\nRESULT: %d passed, %d failed' % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
