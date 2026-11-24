"""实测 MCV 部署指令可靠性（V2 预部署基地 warmup 设计依据）。

对每次启动：重复 [STOP MCV -> DEPLOY]（必要时 MOVE 换位后重试），统计
Construction Yard 是否真实出现及耗时。输出 JSON 证据。
"""
import asyncio
import json
import pathlib
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
MAP_DIR = ROOT / 'experiments' / 'build_training_v2' / 'map'


def find(obj_list, type_names, needle):
    for o in obj_list:
        if int(o.health) <= 0:
            continue
        tn = type_names.get(int(getattr(o, 'pointer_technotypeclass', 0)), '?')
        if needle in tn:
            return o, tn
    return None, None


async def one_launch(index):
    session = Session(GAME_DIR, 14531, launch_timeout=120)
    result = {'launch': index, 'attempts': []}
    t0 = time.time()
    try:
        await session.launch()
        st = await session.connect()
        initial = await R.read_initial(session.client)
        type_names = {int(t.pointer_self): t.name for t in initial.object_types}
        deadline = time.monotonic() + 40.0
        try_count = 0
        while time.monotonic() < deadline:
            st = await session.read()
            cy, _ = find(st.objects, type_names, 'Construction Yard')
            if cy is not None and int(getattr(cy, 'pointer_house', 0)) != 0:
                result['ok'] = True
                result['cy_frame'] = int(st.current_frame)
                result['elapsed_s'] = round(time.time() - t0, 1)
                result['cy_cell'] = [int(cy.coordinates.x) // CELL,
                                     int(cy.coordinates.y) // CELL]
                return result
            mcv, _ = find(st.objects, type_names, 'Construction Vehicle')
            if mcv is None:
                result['ok'] = False
                result['error'] = 'no MCV'
                return result
            # STOP 再 DEPLOY（部分情况下 MCV 移动中部署会被引擎忽略）
            ms, err = await R.send_cmd(
                session.client,
                commands_game.UnitOrder(object_addresses=[int(mcv.pointer_self)],
                                        action=ra2yr.UNIT_ACTION_STOP))
            await asyncio.sleep(0.5)
            ms, err = await R.send_cmd(
                session.client,
                commands_game.UnitOrder(object_addresses=[int(mcv.pointer_self)],
                                        action=ra2yr.UNIT_ACTION_DEPLOY))
            st_after = await session.read()
            cy_after, _ = find(st_after.objects, type_names, 'Construction Yard')
            result['attempts'].append({
                'n': try_count, 'frame': int(st.current_frame),
                'ack_ms': ms, 'error': err,
                'cy_now': cy_after is not None,
                'mcv_cell': [int(mcv.coordinates.x) // CELL, int(mcv.coordinates.y) // CELL],
            })
            try_count += 1
            if cy_after is not None:
                result['ok'] = True
                result['cy_frame'] = int(st_after.current_frame)
                result['elapsed_s'] = round(time.time() - t0, 1)
                return result
            # 每 4 次尝试移动 MCV 到附近空地再重试
            if try_count % 4 == 0:
                cx, cy_c = (int(mcv.coordinates.x) // CELL, int(mcv.coordinates.y) // CELL)
                move = commands_game.UnitOrder(
                    object_addresses=[int(mcv.pointer_self)],
                    action=ra2yr.UNIT_ACTION_MOVE)
                move.coordinates.x = (cx + 4) * CELL + 128
                move.coordinates.y = (cy_c + 4) * CELL + 128
                await R.send_cmd(session.client, move)
                await asyncio.sleep(2.0)
        result['ok'] = False
        result['error'] = 'timeout'
        result['elapsed_s'] = round(time.time() - t0, 1)
        return result
    finally:
        session.close()
        result['total_elapsed_s'] = round(time.time() - t0, 1)


async def run():
    # 确保使用无敌人 A1 配置（不是 probe 地图）
    import shutil
    shutil.copyfile(MAP_DIR / 'base_2peaks.map', GAME_DIR / 'spawnmap.ini')
    spawn = (MAP_DIR / 'build_v2_spawn.ini').read_text(encoding='ascii')
    (GAME_DIR / 'spawn.ini').write_text(spawn, encoding='ascii')
    results = []
    for i in range(2):
        results.append(await one_launch(i))
        print('[deploy-probe]', json.dumps(results[-1], ensure_ascii=False), flush=True)
    out = ROOT / 'reports' / 'build_training' / 'build_v2_deploy_probe.json'
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[deploy-probe] saved', out)


if __name__ == '__main__':
    asyncio.run(run())
