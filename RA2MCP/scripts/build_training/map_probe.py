"""无敌人地图可行性实测探针（任务第二步：方案 A / B / C）。

对独立的游戏副本写入指定 spawn.ini + spawnmap.ini 变体，真实启动尤里的复仇，
通过 ra2yrcpp 桥接读取状态并逐项验证：

  1. 游戏能否启动、地图能否加载、玩家能否进入
  2. 是否存在敌方 AI house / 敌方单位建筑（无敌人）
  3. 初始建筑与经济条件（MCV、资金、电力）
  4. 能否打开建筑菜单（301 动作掩码中可生产项 / 可放置项）
  5. 能否成功建造并放置建筑（DEPLOY -> PRODUCE -> 等待完成 -> PLACE -> 状态确认）
  6. 地图/对局能否重置（杀进程后重新启动，再次进入初始状态）

输出：<out>/<run-name>.json（含 VERIFIED / BLOCKED / UNKNOWN 判定与证据）

用法（使用项目 RL 环境的干净副本 .venv-build，避免低完整性沙箱限制）：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\map_probe.py \
      --game-dir test-game-build --port 14531 --variant A1_no_enemy \
      --build-test --out reports/build_training/map_probe
"""
import argparse
import asyncio
import json
import pathlib
import shutil
import sys
import time
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT / 'vendor' / 'pyra2yr') not in sys.path:
    sys.path.insert(0, str(ROOT / 'vendor' / 'pyra2yr'))

from google.protobuf.json_format import MessageToDict  # noqa: E402
from pyra2yr.network import DualClient  # noqa: E402
from ra2yrproto import commands_game, commands_yr, ra2yr  # noqa: E402

from dsh_episode import obs as obs_mod  # noqa: E402
from dsh_episode import runner as R  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402

CELL = 256


# ---------------------------------------------------------------- 变体配置 ----
BASE_SPAWN = """[Settings]
Name=DSH-BuildProbe
Side=0
Color=2
IsSpectator=No
Scenario=spawnmap.ini
UIMapName={ui_map}
PlayerCount={player_count}
AIPlayers={ai_players}
AIDifficulty=2
GameMode=1
Bases=Yes
Credits={credits}
UnitCount={unit_count}
GameSpeed=1
TechLevel=10
ShortGame={short_game}
Superweapons=No
BuildOffAlly=Yes
Crates=No
BridgeDestroy=Yes
MultiEngineer=No
AlliesAllowed=No
HarvesterTruce=No
FogOfWar=No
MCVRedeploy=Yes
Seed=20261028
Protocol=0
FrameSendRate=2
ReconnectTimeout=1400
SidebarHack=Yes
SpecialHouseIsAlly=No
DefeatedBecomesObserver=Yes
AINamesByDifficulty=Yes
Observer.ShowAIOnSidebar=Yes
SkipScoreScreen=Yes
RunAutoSS=No
AutoSaveCount=0
AutoSaveInterval=0
DisableChat=Yes

[HouseColors]
Multi1=1

[HouseCountries]
Multi1=0

[HouseHandicaps]
Multi1=0
"""


def variant_spec(name):
    """返回变体字典：spawn.ini 参数、地图源文件、说明。"""
    peak_map = ROOT / "test-game" / "Maps" / "Yuri's Revenge" / "2peaks.map"
    divide_map = ROOT / "test-game" / "Maps" / "Yuri's Revenge" / "4_divide_and_conquer.map"
    variants = {
        # 方案 A：单人遭遇战（原版地图 2peaks，只保留 1 个人类玩家，0 个 AI）
        'A1_no_enemy': dict(
            description='方案A：原版遭遇战地图 2peaks，PlayerCount=1 + AIPlayers=0，无其他玩家节点',
            map_source=str(peak_map), ui_map='2peaks', player_count=1, ai_players=0,
            credits=20000, unit_count=8, short_game='No'),
        # 方案 A2：同上但 ShortGame=Yes（观察无敌人时游戏是否立即判胜/结束）
        'A2_no_enemy_shortgame_yes': dict(
            description='方案A2：同 A1，但 ShortGame=Yes（检查无敌人时是否立即结束）',
            map_source=str(peak_map), ui_map='2peaks', player_count=1, ai_players=0,
            credits=20000, unit_count=8, short_game='Yes'),
        # 方案 A3：4 人地图上只放 1 个玩家（排除 2peaks 地图自身限制）
        'A3_divide_no_enemy': dict(
            description='方案A3：4 人地图 4_divide_and_conquer，PlayerCount=1 + AIPlayers=0',
            map_source=str(divide_map), ui_map='4_divide_and_conquer', player_count=1,
            ai_players=0, credits=20000, unit_count=8, short_game='No'),
        # 对照：正常 1 人类 + 1 AI 的遭遇战（确认探针本身能识别敌人）
        'CTRL_1v1_ai': dict(
            description='对照组：1 人类 + 1 AI（应检测到敌方 house/单位）',
            map_source=str(peak_map), ui_map='2peaks', player_count=2, ai_players=1,
            credits=20000, unit_count=8, short_game='Yes', extra_ai_sections=True),
    }
    if name not in variants:
        raise SystemExit('unknown variant: %s (available: %s)' % (name, ', '.join(variants)))
    return dict(name=name, **variants[name])


def write_variant_files(game_dir, spec):
    """按变体写 spawn.ini / spawnmap.ini，并返回写入文件的 sha256。"""
    import hashlib
    game_dir = pathlib.Path(game_dir)
    spawn = BASE_SPAWN.format(
        ui_map=spec['ui_map'], player_count=spec['player_count'],
        ai_players=spec['ai_players'], credits=spec['credits'],
        unit_count=spec['unit_count'], short_game=spec['short_game'])
    if spec.get('extra_ai_sections'):
        spawn += """
[Other1]
Name=DSH-Probe-AI
Side=8
Color=1
Team=0
"""
        spawn += """
[HouseColors]
Multi1=2
Multi2=1

[HouseCountries]
Multi1=0
Multi2=8

[HouseHandicaps]
Multi1=0
Multi2=2
"""
    (game_dir / 'spawn.ini').write_text(spawn, encoding='ascii')
    source = pathlib.Path(spec['map_source'])
    shutil.copyfile(source, game_dir / 'spawnmap.ini')
    return {
        'spawn_ini_sha256': hashlib.sha256((game_dir / 'spawn.ini').read_bytes()).hexdigest(),
        'spawnmap_sha256': hashlib.sha256((game_dir / 'spawnmap.ini').read_bytes()).hexdigest(),
        'spawn.ini': spawn,
    }


# ---------------------------------------------------------------- 游戏会话 ----
class Session:
    """最小化的启动/连接/读取封装（复用 dsh_episode.runner 的底层函数）。"""

    def __init__(self, game_dir, port, launch_timeout=120):
        self.game_dir = pathlib.Path(game_dir)
        self.port = port
        self.launch_timeout = launch_timeout
        self.proc = None
        self.client = None
        self.events = []

    def event(self, name, **data):
        rec = {'t': time.strftime('%H:%M:%S'), 'event': name, **data}
        self.events.append(rec)
        print('[probe] %s %s' % (name, json.dumps(data, ensure_ascii=False)), flush=True)

    async def launch(self):
        R.kill_games()
        time.sleep(1.0)
        self.proc = R.launch_game(self.game_dir)
        self.event('game_launched', pid=self.proc.pid)

    async def connect(self):
        deadline = time.monotonic() + self.launch_timeout
        client = None
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            if self.proc is not None and self.proc.poll() is not None:
                yield_rc = self.proc.returncode
                raise RuntimeError('game exited during launch rc=%s (attempts=%d)'
                                   % (yield_rc, attempts))
            if client is None:
                client = DualClient('127.0.0.1', self.port, timeout=2)
                try:
                    client.connect()
                except Exception as exc:  # noqa: BLE001
                    self.event('bridge_connect_retry', error=repr(exc)[:200])
                    await asyncio.sleep(2.0)
                    continue
                await asyncio.sleep(2.0)
            st = await R.read_state(client)
            if st is not None and st.stage == 2 and len(st.objects) > 0:
                self.client = client
                self.event('ingame', frame=int(st.current_frame),
                           objects=len(st.objects), attempts=attempts)
                return st
            await asyncio.sleep(2.0)
        raise RuntimeError('bridge/INGAME timeout after %.0fs' % self.launch_timeout)

    async def read(self):
        if self.client is None:
            raise RuntimeError('not connected')
        return await R.read_state(self.client, strict=True)

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
            self.client = None
        if self.proc is not None and self.proc.poll() is None:
            R.kill_game(self.proc.pid)
            try:
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self.event('game_killed', pid=self.proc.pid)
        self.proc = None


def house_rows(state, hero):
    rows = []
    for h in state.houses:
        rows.append({
            'self': int(h.self), 'name': h.name, 'faction': getattr(h, 'faction', ''),
            'array_index': int(h.array_index),
            'current_player': bool(getattr(h, 'current_player', False)),
            'is_human': bool(getattr(h, 'is_human_player', False)),
            'money': int(h.money),
            'power_output': int(getattr(h, 'power_output', 0)),
            'power_drain': int(getattr(h, 'power_drain', 0)),
            'defeated': bool(h.defeated),
            'is_loser': bool(getattr(h, 'is_loser', False)),
            'is_winner': bool(getattr(h, 'is_winner', False)),
            'is_game_over': bool(getattr(h, 'is_game_over', False)),
            'is_hero': int(h.self) == int(hero.self),
        })
    return rows


NEUTRAL_NAMES = {'neutral', 'special', 'civilian', '<human player>', '?'}


def object_owner_summary(state, houses_by_ptr, hero_self):
    summary = {}
    for o in state.objects:
        if int(o.health) <= 0:
            continue
        owner = int(getattr(o, 'pointer_house', 0))
        rec = summary.setdefault(owner, {
            'owner_ptr': owner,
            'owner_name': houses_by_ptr.get(owner, '?'),
            'objects': 0, 'buildings': 0, 'units': 0, 'in_limbo': 0, 'on_map': 0,
            'is_hero': owner == hero_self})
        rec['objects'] += 1
        abstract = ra2yr.AbstractType.Name(getattr(o, 'object_type', 0))
        if abstract == 'ABSTRACT_TYPE_BUILDING':
            rec['buildings'] += 1
        else:
            rec['units'] += 1
        if bool(getattr(o, 'in_limbo', False)):
            rec['in_limbo'] += 1
        if bool(getattr(o, 'on_map', False)):
            rec['on_map'] += 1
    return {str(k): v for k, v in summary.items()}


def assess_no_enemy(house_rows_, owner_summary, hero_self):
    """判定是否存在敌方 AI house / 敌方战斗对象。"""
    enemy_houses = []
    for h in house_rows_:
        if h['is_hero'] or h['name'].casefold() in NEUTRAL_NAMES:
            continue
        if h['defeated'] or h['is_loser']:
            continue
        enemy_houses.append(h)
    enemy_objects = []
    for key, rec in owner_summary.items():
        name = str(rec['owner_name']).casefold()
        if rec['is_hero'] or name in NEUTRAL_NAMES:
            continue
        if rec['on_map'] > 0 or rec['units'] > 0 or rec['buildings'] > 0:
            enemy_objects.append(rec)
    return {
        'enemy_houses': enemy_houses,
        'enemy_objects': enemy_objects,
        'no_enemy': not enemy_houses and not enemy_objects,
    }


def build_row(state, client, initial, type_names):
    hero = next(h for h in state.houses if h.current_player and h.is_human_player)
    houses = {int(h.self): h.name for h in state.houses}
    return obs_mod.capture_observation(state, int(hero.self), type_names, houses,
                                       getattr(build_row, 'map_soa', None), 'probe')


async def spec_and_masks(state, client, initial, type_names, starts):
    types = [MessageToDict(t, preserving_proto_field_name=True,
                           including_default_value_fields=True)
             for t in initial.object_types]
    groups = MessageToDict(initial.prerequisite_groups, preserving_proto_field_name=True,
                           including_default_value_fields=True)
    hero = next(h for h in state.houses if h.current_player and h.is_human_player)
    spec = S.FullControlSpec(types, groups, int(hero.type_array_index),
                             int(initial.tech_level) if initial.tech_level > 0 else 10)
    map_soa = await R.read_map_soa(client)
    row = obs_mod.capture_observation(
        state, int(hero.self), type_names,
        {int(h.self): h.name for h in state.houses}, map_soa, 'probe')
    masks = spec.masks(row, starts, map_soa)
    produce = {}
    for slot in range(len(spec.types)):
        if masks[S.PRODUCE_START + slot]:
            produce[spec.types[slot]['name']] = slot
    place_slots = int(masks[S.PLACE_START:S.UNIT_START].sum())
    unit_actions = int(masks[S.UNIT_START:].sum())
    return spec, row, map_soa, {
        'action_n': int(masks.sum()),
        'produce_available': sorted(produce.keys()),
        'produce_available_count': len(produce),
        'place_actions_available': place_slots,
        'unit_actions_available': unit_actions,
        'noop_available': bool(masks[0]),
        'deploy_available': bool(masks[1]),
        'produce_slots': produce,
    }


async def find_object(state, predicate):
    for o in state.objects:
        if predicate(o):
            return o
    return None


async def wait_until(session, predicate, timeout, interval=0.5, desc=''):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        st = await session.read()
        last = st
        hit = predicate(st)
        if hit is not None and hit is not False:
            return st, hit
        await asyncio.sleep(interval)
    return last, None


def obj_type_name(type_names, o):
    return type_names.get(int(getattr(o, 'pointer_technotypeclass', 0)), '?')


async def run_build_test(session, state, initial, type_names, starts, evidence):
    """DEPLOY -> PRODUCE 电厂 -> 等待完成 -> PLACE -> 状态确认。"""
    er = R.EpisodeRunner(SimpleNamespace(episode_id='probe'))
    er.client = session.client
    er.type_by_ptr = {int(t.pointer_self): t for t in initial.object_types}
    result = {'steps': [], 'success': False}

    def note(name, **data):
        rec = {'step': name, **data}
        result['steps'].append(rec)
        session.event('build_test_' + name, **data)

    # 1) 展开 MCV -> 建造厂
    mcv = await find_object(
        state, lambda o: int(o.health) > 0 and 'Construction Vehicle' in obj_type_name(type_names, o))
    if mcv is None:
        note('deploy', ok=False, error='no MCV')
        return result
    q = commands_game.UnitOrder(object_addresses=[int(mcv.pointer_self)],
                                action=ra2yr.UNIT_ACTION_DEPLOY)
    ms, err = await R.send_cmd(session.client, q)
    note('deploy_sent', ack_ms=ms, error=err, mcv=int(mcv.pointer_self))
    st, cy = await wait_until(
        session,
        lambda s: next((o for o in s.objects
                        if int(o.health) > 0 and 'Construction Yard' in obj_type_name(type_names, o)
                        and bool(getattr(o, 'on_map', False)) and not bool(getattr(o, 'in_limbo', False))), None),
        timeout=90, desc='construction yard')
    if cy is None:
        note('construction_yard', ok=False, error='timeout waiting CY')
        return result
    note('construction_yard', ok=True, addr=int(cy.pointer_self),
         cell=[int(cy.coordinates.x) // CELL, int(cy.coordinates.y) // CELL],
         frame=int(st.current_frame))
    result['construction_yard'] = {'addr': int(cy.pointer_self),
                                   'cell': [int(cy.coordinates.x) // CELL,
                                            int(cy.coordinates.y) // CELL]}

    # 2) 生产第一家建筑：优先 Allied Power Plant（无前置）
    spec, row, map_soa, mask_info = await spec_and_masks(
        st, session.client, initial, type_names, starts)
    result['menu_after_cy'] = dict(mask_info, frame=int(st.current_frame))
    note('build_menu_after_cy', produce_available_count=mask_info['produce_available_count'],
         place_actions_available=mask_info['place_actions_available'],
         first_available=mask_info['produce_available'][:12],
         frame=int(st.current_frame))
    candidates = [n for n in ('Allied Power Plant', 'Allied Ore Refinery',
                              'Allied Barracks', 'Allied War Factory')
                  if n in mask_info['produce_slots']]
    if not candidates:
        note('produce', ok=False, error='no buildable building in mask',
             produce_available=mask_info['produce_available'][:30])
        return result
    target_name = candidates[0]
    slot = mask_info['produce_slots'][target_name]
    type_ptr = int(spec.types[slot]['pointer_self'])
    tc = er.type_by_ptr[type_ptr]
    q = commands_game.ProduceOrder(object_type=tc, action=ra2yr.PRODUCE_ACTION_BEGIN)
    ms, err = await R.send_cmd(session.client, q)
    note('produce_sent', type=target_name, ack_ms=ms, error=err)
    if err:
        return result

    # 3) 等待工厂完成（building object 在 limbo 中等待放置）
    def completed_factory(s):
        for f in s.factories:
            if bool(getattr(f, 'completed', False)) and int(getattr(f, 'object', 0)) != 0:
                obj = next((o for o in s.objects if int(o.pointer_self) == int(f.object)), None)
                if obj is not None and 'Construction' not in obj_type_name(type_names, obj):
                    return {'factory': f, 'obj': obj}
        return None
    deadline = time.monotonic() + 120
    pending = None
    st2 = st
    while time.monotonic() < deadline:
        st2 = await session.read()
        hit = completed_factory(st2)
        if hit is not None:
            pending = hit
            break
        await asyncio.sleep(1.0)
    if pending is None:
        note('produce_complete', ok=False, error='timeout waiting completed factory')
        return result
    bptr = int(pending['obj'].pointer_self)
    note('produce_complete', ok=True, building_address=bptr,
         type=obj_type_name(type_names, pending['obj']),
         frame=int(st2.current_frame))

    # 4) 放置建筑（PlaceQuery 找合法格 + PlaceBuilding）
    base = next((o for o in st2.objects
                 if int(o.health) > 0 and 'Construction Yard' in obj_type_name(type_names, o)
                 and bool(getattr(o, 'on_map', False))), None)
    base_cell = [int(base.coordinates.x) // CELL, int(base.coordinates.y) // CELL]
    intent = {'production_type_ptr': type_ptr, 'production_type': target_name,
              'building_address': bptr, 'anchor': [base_cell[0] + 5, base_cell[1]]}
    rows = await er.execute_place(st2, hero=state_hero(st2), intent=intent)
    rec = rows[0] if rows else {}
    note('place_sent', error=rec.get('error'), ack_ms=rec.get('ack_ms'),
         coordinates=rec.get('coordinates'))
    if rec.get('error'):
        return result

    # 5) 状态确认：新建筑已真实存在于世界且属于玩家
    def placed_building(s):
        o = next((o for o in s.objects if int(o.pointer_self) == bptr), None)
        if o is not None and int(o.health) > 0 and bool(getattr(o, 'on_map', False)) \
                and not bool(getattr(o, 'in_limbo', False)):
            return o
        return None
    st3, placed = await wait_until(session, placed_building, timeout=90, desc='placed building')
    if placed is None:
        note('place_confirmed', ok=False, error='timeout: building not placed')
        return result
    owner = int(getattr(placed, 'pointer_house', 0))
    hero = next(h for h in st3.houses if h.current_player and h.is_human_player)
    note('place_confirmed', ok=True, building_address=bptr,
         type=obj_type_name(type_names, placed),
         cell=[int(placed.coordinates.x) // CELL, int(placed.coordinates.y) // CELL],
         health=int(placed.health), owner_ptr=owner, owner_is_hero=(owner == int(hero.self)),
         frame=int(st3.current_frame))
    result['placed_building'] = {
        'addr': bptr, 'type': obj_type_name(type_names, placed),
        'cell': [int(placed.coordinates.x) // CELL, int(placed.coordinates.y) // CELL],
        'health': int(placed.health), 'owner_is_hero': owner == int(hero.self)}
    # 6) 第二次独立读取（模拟 MCP 侧再次读取），确认状态可重复读取
    st4 = await session.read()
    again = next((o for o in st4.objects if int(o.pointer_self) == bptr), None)
    note('state_reread', ok=again is not None,
         on_map=bool(getattr(again, 'on_map', False)) if again is not None else None,
         money=int(next(h for h in st4.houses if h.current_player).money))
    result['success'] = bool(again is not None and bool(getattr(again, 'on_map', False)))
    result['money_after'] = int(next(h for h in st4.houses if h.current_player).money)
    return result


def state_hero(state):
    return next(h for h in state.houses if h.current_player and h.is_human_player)


async def probe(args, spec):
    game_dir = (ROOT / args.game_dir).resolve() if not pathlib.Path(args.game_dir).is_absolute() \
        else pathlib.Path(args.game_dir)
    evidence = {
        'probe_time': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'variant': spec['name'], 'description': spec['description'],
        'game_dir': str(game_dir), 'port': args.port,
        'verdict': {}, 'evidence': {},
    }
    evidence['written'] = write_variant_files(game_dir, spec)
    session = Session(game_dir, args.port, launch_timeout=args.launch_timeout)
    try:
        await session.launch()
        st = await session.connect()
        initial = await R.read_initial(session.client)
        type_names = {int(t.pointer_self): t.name for t in initial.object_types}
        starts = R.public_start_cells(path=game_dir / 'spawnmap.ini')
        hero = state_hero(st)
        houses_by_ptr = {int(h.self): h.name for h in st.houses}
        hrows = house_rows(st, hero)
        owner_summary = object_owner_summary(st, houses_by_ptr, int(hero.self))
        no_enemy = assess_no_enemy(hrows, owner_summary, int(hero.self))
        own_buildings = [o for o in st.objects
                         if int(o.health) > 0 and int(getattr(o, 'pointer_house', 0)) == int(hero.self)
                         and ra2yr.AbstractType.Name(getattr(o, 'object_type', 0)) == 'ABSTRACT_TYPE_BUILDING'
                         and bool(getattr(o, 'on_map', False))]
        own_units = [o for o in st.objects
                     if int(o.health) > 0 and int(getattr(o, 'pointer_house', 0)) == int(hero.self)
                     and bool(getattr(o, 'on_map', False))
                     and ra2yr.AbstractType.Name(getattr(o, 'object_type', 0)) != 'ABSTRACT_TYPE_BUILDING']
        # 地图/进入判定：stage==2 且帧号>0 且桥接持续可读
        frame1 = int(st.current_frame)
        await asyncio.sleep(2.0)
        st_b = await session.read()
        frame2 = int(st_b.current_frame)
        spec_obj, row, map_soa, mask_info = await spec_and_masks(
            st_b, session.client, initial, type_names, starts)
        enter = {
            'stage': int(st.stage), 'stage_name': ra2yr.LoadStage.Name(st.stage),
            'frame_first': frame1, 'frame_second': frame2, 'frame_advancing': frame2 > frame1,
            'hero': {'name': hero.name, 'self': int(hero.self),
                     'array_index': int(hero.array_index),
                     'is_human': bool(hero.is_human_player),
                     'current_player': bool(hero.current_player),
                     'money': int(hero.money),
                     'power_output': int(getattr(hero, 'power_output', 0)),
                     'power_drain': int(getattr(hero, 'power_drain', 0))},
            'starts': starts,
            'map': {'width': int(map_soa.map_width), 'height': int(map_soa.map_height)},
        }
        evidence['evidence'].update({
            'enter': enter, 'houses': hrows, 'object_owner_summary': owner_summary,
            'no_enemy': no_enemy, 'build_menu': mask_info,
            'own_buildings': [{'type': obj_type_name(type_names, o),
                               'addr': int(o.pointer_self),
                               'cell': [int(o.coordinates.x) // CELL,
                                        int(o.coordinates.y) // CELL]}
                              for o in own_buildings],
            'own_unit_count': len(own_units),
            'own_unit_types': sorted({obj_type_name(type_names, o) for o in own_units}),
        })
        # 判定：能否启动无敌人地图
        checks = {
            'game_started': True,
            'map_loaded': enter['stage'] == 2 and frame1 > 0,
            'player_entered': enter['hero']['current_player'] and enter['hero']['is_human'],
            'frame_advancing': enter['frame_advancing'],
            'no_enemy': no_enemy['no_enemy'],
            'initial_buildings': len(own_buildings) > 0 or any(
                'Construction Vehicle' in obj_type_name(type_names, o) for o in own_units),
            'money_ok': enter['hero']['money'] > 0,
            'build_menu_available': mask_info['produce_available_count'] > 0,
        }
        # 建造测试
        if args.build_test:
            build = await run_build_test(session, st_b, initial, type_names, starts, evidence)
            evidence['evidence']['build_test'] = build
            checks['build_and_place'] = bool(build.get('success'))
            checks['bridge_read_new_building'] = bool(
                (build.get('placed_building') or {}).get('addr'))
            menu_after_cy = build.get('menu_after_cy') or {}
            evidence['evidence']['build_menu_after_cy'] = menu_after_cy
            # 「可以打开建筑菜单」以展开基地后的真实掩码为准（展开前没有建造厂，
            # 建筑生产项必然为空，属于正常状态而非失败）。
            checks['build_menu_available'] = (
                int(menu_after_cy.get('produce_available_count') or 0) > 0)
        # 重置测试：关闭后重新启动，确认回到初始状态
        if args.reset_test:
            session.close()
            session2 = Session(game_dir, args.port, launch_timeout=args.launch_timeout)
            try:
                await session2.launch()
                st_r = await session2.connect()
                hero_r = state_hero(st_r)
                r_buildings = [o for o in st_r.objects
                               if int(o.health) > 0
                               and int(getattr(o, 'pointer_house', 0)) == int(hero_r.self)
                               and ra2yr.AbstractType.Name(getattr(o, 'object_type', 0)) == 'ABSTRACT_TYPE_BUILDING'
                               and bool(getattr(o, 'on_map', False))]
                evidence['evidence']['reset'] = {
                    'ok': True, 'hero_money': int(hero_r.money),
                    'own_building_count': len(r_buildings),
                    'frame': int(st_r.current_frame)}
                checks['reset_ok'] = True
            finally:
                session2.close()
        evidence['verdict'] = {
            k: ('VERIFIED' if v else 'BLOCKED') for k, v in checks.items()}
        evidence['verdict']['overall'] = (
            'VERIFIED' if all(checks.values()) else
            'PARTIAL' if (checks.get('game_started') and checks.get('player_entered')) else
            'BLOCKED')
        return evidence
    finally:
        session.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--game-dir', default='test-game-build')
    ap.add_argument('--port', type=int, default=14531)
    ap.add_argument('--variant', default='A1_no_enemy')
    ap.add_argument('--build-test', action='store_true', default=True)
    ap.add_argument('--no-build-test', dest='build_test', action='store_false')
    ap.add_argument('--reset-test', action='store_true', default=True)
    ap.add_argument('--no-reset-test', dest='reset_test', action='store_false')
    ap.add_argument('--launch-timeout', type=float, default=120)
    ap.add_argument('--out', default='reports/build_training/map_probe')
    a = ap.parse_args()
    spec = variant_spec(a.variant)
    out_dir = (ROOT / a.out).resolve() if not pathlib.Path(a.out).is_absolute() \
        else pathlib.Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence = asyncio.run(probe(a, spec))
    result_path = out_dir / (spec['name'] + '.json')
    result_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                           encoding='utf-8')
    print('[probe] verdict:', json.dumps(evidence['verdict'], ensure_ascii=False))
    print('[probe] saved:', result_path)
    print('[probe] RESULT_JSON %s' % json.dumps(
        {'variant': spec['name'], 'verdict': evidence['verdict']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
