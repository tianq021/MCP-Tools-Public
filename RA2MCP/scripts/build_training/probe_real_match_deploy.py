"""对照实验（修正版）：真实 1v1 环境下玩家 MCV 部署机制，按归属过滤。

情形 A：开局直接 STOP+DEPLOY（无先发移动指令）
情形 B：先给 MCV 发 MOVE 指令，再 STOP+DEPLOY
情形 C：连续给 MCV 发多次 MOVE 后立即 STOP+DEPLOY
仅统计“属于玩家自己的 Construction Yard”，避免误把 AI 的建造厂当成成功。
"""
import asyncio
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'vendor' / 'pyra2yr'))

from map_probe import Session  # noqa: E402
from dsh_episode import runner as R  # noqa: E402
from ra2yrproto import commands_game, ra2yr  # noqa: E402

GAME_DIR = ROOT / 'test-game-build'
CELL = 256
MATCH_SPAWN = ROOT / 'config' / 'spawn.ini'
MAP = ROOT / 'experiments' / 'build_training_v2' / 'map' / 'base_2peaks.map'


def prepare():
    shutil.copyfile(MATCH_SPAWN, GAME_DIR / 'spawn.ini')
    shutil.copyfile(MAP, GAME_DIR / 'spawnmap.ini')


def hero_ptr(state):
    for h in state.houses:
        if bool(getattr(h, 'current_player', False)) and bool(getattr(h, 'is_human_player', False)):
            return int(h.self)
    return 0


def find_own(types, state, needle, owner):
    for o in state.objects:
        if int(o.health) <= 0:
            continue
        if int(getattr(o, 'pointer_house', 0)) != owner:
            continue
        tn = types.get(int(getattr(o, 'pointer_technotypeclass', 0)), '?')
        if needle in tn:
            return o, tn
    return None, None


async def scenario(name, mode):
    session = Session(GAME_DIR, 14531, launch_timeout=120)
    result = {'scenario': name, 'mode': mode}
    try:
        await session.launch()
        st = await session.connect()
        initial = await R.read_initial(session.client)
        types = {int(t.pointer_self): t.name for t in initial.object_types}
        st = await session.read()
        owner = hero_ptr(st)
        result['hero_ptr'] = owner
        mcv, _ = find_own(types, st, 'Construction Vehicle', owner)
        if mcv is None:
            result['error'] = 'no own MCV'
            return result
        result['initial_mcv'] = {'addr': int(mcv.pointer_self),
                                'cell': [int(mcv.coordinates.x) // CELL,
                                         int(mcv.coordinates.y) // CELL]}
        if mode in ('B', 'C'):
            repeats = 1 if mode == 'B' else 3
            moves = []
            for i in range(repeats):
                st = await session.read()
                owner = hero_ptr(st)
                mcv, _ = find_own(types, st, 'Construction Vehicle', owner)
                if mcv is None:
                    break
                order = commands_game.UnitOrder(
                    object_addresses=[int(mcv.pointer_self)],
                    action=ra2yr.UNIT_ACTION_MOVE)
                cx = int(mcv.coordinates.x) // CELL + 3 + i
                cy = int(mcv.coordinates.y) // CELL + 2 + i
                order.coordinates.x = cx * CELL + 128
                order.coordinates.y = cy * CELL + 128
                await R.send_cmd(session.client, order)
                await asyncio.sleep(1.0)
                st = await session.read()
                owner = hero_ptr(st)
                m2, _ = find_own(types, st, 'Construction Vehicle', owner)
                moves.append({'cell': ([int(m2.coordinates.x) // CELL,
                                        int(m2.coordinates.y) // CELL] if m2 else None),
                              'mission': (ra2yr.Mission.Name(m2.current_mission)
                                          if m2 else None)})
            result['moves'] = moves
        attempts = []
        ok = False
        t0 = time.time()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            st = await session.read()
            owner = hero_ptr(st)
            cy, _ = find_own(types, st, 'Construction Yard', owner)
            if cy is not None:
                ok = True
                result['cy_cell'] = [int(cy.coordinates.x) // CELL,
                                     int(cy.coordinates.y) // CELL]
                break
            mcv, _ = find_own(types, st, 'Construction Vehicle', owner)
            if mcv is None:
                attempts.append({'frame': int(st.current_frame), 'error': 'mcv gone'})
                break
            await R.send_cmd(session.client,
                             commands_game.UnitOrder(action=ra2yr.UNIT_ACTION_STOP,
                                                     object_addresses=[int(mcv.pointer_self)]))
            await asyncio.sleep(0.3)
            await R.send_cmd(session.client,
                             commands_game.UnitOrder(action=ra2yr.UNIT_ACTION_DEPLOY,
                                                     object_addresses=[int(mcv.pointer_self)]))
            await asyncio.sleep(1.2)
            st = await session.read()
            owner = hero_ptr(st)
            m2, _ = find_own(types, st, 'Construction Vehicle', owner)
            cy2, _ = find_own(types, st, 'Construction Yard', owner)
            attempts.append({
                'frame': int(st.current_frame),
                'mcv_cell': ([int(m2.coordinates.x) // CELL,
                              int(m2.coordinates.y) // CELL] if m2 else None),
                'mcv_mission': (ra2yr.Mission.Name(m2.current_mission) if m2 else None),
                'own_cy': cy2 is not None})
            if cy2 is not None:
                ok = True
                result['cy_cell'] = [int(cy2.coordinates.x) // CELL,
                                     int(cy2.coordinates.y) // CELL]
                break
        result.update({'deploy_success': ok, 'attempts': attempts,
                       'elapsed_s': round(time.time() - t0, 1)})
        return result
    finally:
        session.close()


async def main():
    prepare()
    out = []
    for name, mode in (('A_direct_deploy', 'A'), ('B_move_then_deploy', 'B'),
                       ('C_multimove_then_deploy', 'C')):
        r = await scenario(name, mode)
        out.append(r)
        print('[probe]', json.dumps(r, ensure_ascii=False), flush=True)
    path = ROOT / 'reports' / 'build_training' / 'real_match_v1' / 'deploy_mechanism_probe.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[probe] saved', path)


if __name__ == '__main__':
    asyncio.run(main())
