"""V2 建设训练地图准备 + 真机验证。

按任务书第二节：创建独立地图副本（不修改原始地图），初始条件：
PlayerCount=1 / AIPlayers=0 / Allied / 初始 Construction Yard / 初始 Power Plant /
充足资金 / 足够电力 / 无敌方单位建筑 AI。

做法：复制原版 2peaks.map 为独立副本，在 [Structures] 段追加属于 Americans 玩家的
GACNST（Allied Construction Yard）与 GAPOWR（Allied Power Plant），然后真机启动验证：
玩家是否拥有这两座建筑、电力是否正常、能否立即生产建筑（动作掩码非空）、无需 MCV 展开。

输出：
  experiments/build_training_v2/map/build_v2_predeployed.map（独立地图副本）
  experiments/build_training_v2/map/build_v2_spawn.ini
  reports/build_training/build_v2_gamestate_probe.json（真机验证证据）

用法：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\prepare_build_v2.py [--verify]
"""
import argparse
import asyncio
import hashlib
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
THIS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'vendor' / 'pyra2yr'))

from map_probe import Session, spec_and_masks, house_rows, object_owner_summary, \
    assess_no_enemy, obj_type_name, state_hero, CELL  # noqa: E402
from dsh_episode import runner as R  # noqa: E402
from ra2yrproto import ra2yr  # noqa: E402

BASE_MAP = (ROOT / 'test-game' / 'Maps' / "Yuri's Revenge" / '2peaks.map')
MAP_DIR = ROOT / 'experiments' / 'build_training_v2' / 'map'
MAP_COPY = MAP_DIR / 'build_v2_predeployed.map'
SPAWN_COPY = MAP_DIR / 'build_v2_spawn.ini'
GAME_DIR = ROOT / 'test-game-build'

# 预置建筑：坐标来自 V1 pilot 真实成功建造位置（原生 PlaceQuery 验证过可放置）
PRESET_STRUCTURES = [
    # (唯一ID, 行内容) —— owner=Americans（Side=0 玩家国家）
    (9000001, 'Americans,GACNST,256,86,63,0,None,0,0,1,0,0,None,None,None,0,0'),
    (9000002, 'Americans,GAPOWR,256,96,60,0,None,0,0,1,0,0,None,None,None,0,0'),
]

SPAWN_INI = """[Settings]
Name=DSH-BuildV2
Side=0
Color=2
IsSpectator=No
Scenario=spawnmap.ini
UIMapName=build_v2_predeployed
PlayerCount=1
AIPlayers=0
AIDifficulty=2
GameMode=1
Bases=Yes
Credits=50000
UnitCount=8
GameSpeed=1
TechLevel=10
ShortGame=No
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


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def build_map_copy():
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    text = BASE_MAP.read_bytes().decode('latin-1')
    # 在 [Structures] 段末尾插入玩家预置基地（原地图其余内容保持不变）
    lines = text.split('\n')
    out = []
    in_structures = False
    for line in lines:
        if line.strip() == '[Structures]':
            in_structures = True
            out.append(line)
            continue
        if in_structures and line.startswith('['):
            for ident, body in PRESET_STRUCTURES:
                out.append('%d=%s' % (ident, body))
            in_structures = False
        out.append(line)
    if in_structures:
        for ident, body in PRESET_STRUCTURES:
            out.append('%d=%s' % (ident, body))
    MAP_COPY.write_bytes('\n'.join(out).encode('latin-1'))
    SPAWN_COPY.write_text(SPAWN_INI, encoding='ascii')
    return {
        'base_map': str(BASE_MAP), 'base_map_sha256': sha256(BASE_MAP),
        'map_copy': str(MAP_COPY), 'map_copy_sha256': sha256(MAP_COPY),
        'spawn_copy': str(SPAWN_COPY), 'spawn_sha256': sha256(SPAWN_COPY),
        'added_structures': [{'id': i, 'line': b} for i, b in PRESET_STRUCTURES],
        'original_map_untouched': True,
    }


def install_to_game_dir():
    GAME_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAP_COPY, GAME_DIR / 'spawnmap.ini')
    shutil.copyfile(SPAWN_COPY, GAME_DIR / 'spawn.ini')
    return {
        'game_dir': str(GAME_DIR),
        'spawn.ini_sha256': sha256(GAME_DIR / 'spawn.ini'),
        'spawnmap.ini_sha256': sha256(GAME_DIR / 'spawnmap.ini'),
    }


async def verify():
    """真机启动一次，验证预置基地。"""
    session = Session(GAME_DIR, 14531, launch_timeout=120)
    evidence = {'time': time.strftime('%Y-%m-%dT%H:%M:%S')}
    try:
        await session.launch()
        st = await session.connect()
        initial = await R.read_initial(session.client)
        type_names = {int(t.pointer_self): t.name for t in initial.object_types}
        starts = R.public_start_cells(path=GAME_DIR / 'spawnmap.ini')
        hero = state_hero(st)
        houses_by_ptr = {int(h.self): h.name for h in st.houses}
        hrows = house_rows(st, hero)
        owner_summary = object_owner_summary(st, houses_by_ptr, int(hero.self))
        no_enemy = assess_no_enemy(hrows, owner_summary, int(hero.self))
        own_buildings = [o for o in st.objects
                         if int(o.health) > 0
                         and int(getattr(o, 'pointer_house', 0)) == int(hero.self)
                         and ra2yr.AbstractType.Name(getattr(o, 'object_type', 0))
                         == 'ABSTRACT_TYPE_BUILDING'
                         and bool(getattr(o, 'on_map', False))]
        own_units = [o for o in st.objects
                     if int(o.health) > 0
                     and int(getattr(o, 'pointer_house', 0)) == int(hero.self)
                     and bool(getattr(o, 'on_map', False))
                     and ra2yr.AbstractType.Name(getattr(o, 'object_type', 0))
                     != 'ABSTRACT_TYPE_BUILDING']
        buildings_info = [{
            'type': obj_type_name(type_names, o), 'addr': int(o.pointer_self),
            'cell': [int(o.coordinates.x) // CELL, int(o.coordinates.y) // CELL],
            'health': int(o.health),
        } for o in own_buildings]
        # 动作掩码：验证可立即生产建筑、且 BLOCK 掉战斗/移动（由 V2 env 实现，这里先看全掩码）
        spec, row, map_soa, mask_info = await spec_and_masks(
            st, session.client, initial, type_names, starts)
        power = {'output': int(getattr(hero, 'power_output', 0)),
                 'drain': int(getattr(hero, 'power_drain', 0))}
        has_cy = any('Construction Yard' in b['type'] for b in buildings_info)
        has_pp = any('Power Plant' in b['type'] for b in buildings_info)
        evidence.update({
            'stage': int(st.stage), 'frame': int(st.current_frame),
            'hero': {'name': hero.name, 'money': int(hero.money), 'power': power,
                     'is_human': bool(hero.is_human_player)},
            'starts': starts,
            'houses': hrows,
            'object_owner_summary': owner_summary,
            'no_enemy': no_enemy,
            'own_buildings': buildings_info,
            'own_unit_count': len(own_units),
            'own_unit_types': sorted({obj_type_name(type_names, o) for o in own_units}),
            'build_menu': {k: mask_info[k] for k in (
                'produce_available_count', 'place_actions_available',
                'unit_actions_available', 'deploy_available')},
            'first_available_buildings': mask_info['produce_available'][:20],
        })
        checks = {
            'game_started': True,
            'player_entered': bool(hero.current_player and hero.is_human_player),
            'no_enemy': no_enemy['no_enemy'],
            'has_construction_yard': has_cy,
            'has_power_plant': has_pp,
            'power_ok': power['output'] >= power['drain'] and power['output'] > 0,
            'money_ok': int(hero.money) >= 40000,
            'build_menu_nonempty': mask_info['produce_available_count'] > 0,
        }
        evidence['checks'] = checks
        evidence['verdict'] = {
            k: ('VERIFIED' if v else 'BLOCKED') for k, v in checks.items()}
        evidence['verdict']['overall'] = (
            'VERIFIED' if all(checks.values()) else 'BLOCKED')
        return evidence
    finally:
        session.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true', default=True)
    ap.add_argument('--no-verify', dest='verify', action='store_false')
    a = ap.parse_args()
    prep = {'map': build_map_copy(), 'install': install_to_game_dir()}
    prep_path = ROOT / 'reports' / 'build_training' / 'build_v2_map_prep.json'
    prep_path.parent.mkdir(parents=True, exist_ok=True)
    prep_path.write_text(json.dumps(prep, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[prepare] map:', prep['map']['map_copy'],
          'sha=', prep['map']['map_copy_sha256'][:16])
    print('[prepare] installed to', GAME_DIR)
    if not a.verify:
        return 0
    evidence = asyncio.run(verify())
    out = ROOT / 'reports' / 'build_training' / 'build_v2_gamestate_probe.json'
    out.write_text(json.dumps({'prep': prep, 'verify': evidence},
                              ensure_ascii=False, indent=2), encoding='utf-8')
    print('[prepare] VERDICT:', json.dumps(evidence['verdict'], ensure_ascii=False))
    print('[prepare] own buildings:', json.dumps(evidence['own_buildings'],
                                                ensure_ascii=False))
    print('[prepare] power:', json.dumps(evidence['hero']['power'], ensure_ascii=False))
    print('[prepare] build menu:', json.dumps(evidence['build_menu'], ensure_ascii=False))
    print('[prepare] evidence ->', out)
    return 0 if evidence['verdict']['overall'] == 'VERIFIED' else 2


if __name__ == '__main__':
    sys.exit(main())
