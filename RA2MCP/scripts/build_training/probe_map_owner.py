"""快速实验：地图 [Structures] 的 owner 名称在 1 人遭遇战中是否能归属玩家。

对若干候选 owner 名称分别生成地图副本并真机启动，读取是否存在对应建筑及其归属，
以决定 V2「预置于地图的初始基地」是否可行（不可行则回退脚本化部署）。
"""
import asyncio
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'vendor' / 'pyra2yr'))

from map_probe import Session  # noqa: E402
from dsh_episode import runner as R  # noqa: E402
from ra2yrproto import ra2yr  # noqa: E402

MAP_DIR = ROOT / 'experiments' / 'build_training_v2' / 'map'
GAME_DIR = ROOT / 'test-game-build'
BASE = MAP_DIR / 'base_2peaks.map'
SPAWN = MAP_DIR / 'build_v2_spawn.ini'

OWNERS = ['Americans', 'GDI', 'Player', '<human player>', 'Neutral', 'Alliance']


def make_map(owner, path):
    text = BASE.read_text(encoding='latin-1')
    lines = text.split('\n')
    out, in_sec = [], False
    for line in lines:
        if line.strip() == '[Structures]':
            in_sec = True
            out.append(line)
            continue
        if in_sec and line.startswith('['):
            out.append('9900001=%s,GACNST,256,86,63,0,None,0,0,1,0,0,None,None,None,0,0' % owner)
            out.append('9900002=%s,GAPOWR,256,96,60,0,None,0,0,1,0,0,None,None,None,0,0' % owner)
            in_sec = False
        out.append(line)
    path.write_bytes('\n'.join(out).encode('latin-1'))


async def test_owner(owner, type_names):
    outcome = {'owner': owner}
    session = Session(GAME_DIR, 14531, launch_timeout=120)
    try:
        await session.launch()
        st = await session.connect()
        initial = await R.read_initial(session.client)
        names = {int(t.pointer_self): t.name for t in initial.object_types}
        cy, pp = [], []
        for o in st.objects:
            if int(o.health) <= 0:
                continue
            tn = names.get(int(getattr(o, 'pointer_technotypeclass', 0)), '?')
            if 'Construction Yard' in tn and str(tn).startswith('Allied'):
                cy.append(o)
            if 'Power Plant' in tn and str(tn).startswith('Allied'):
                pp.append(o)
        outcome['construction_yard'] = [{'owner': int(o.pointer_house)} for o in cy]
        outcome['power_plant'] = [{'owner': int(o.pointer_house)} for o in pp]
        outcome['total_objects'] = len(st.objects)
        outcome['ok'] = bool(cy and pp)
        return outcome
    finally:
        session.close()


async def main():
    type_names = {}
    for i, owner in enumerate(OWNERS):
        path = MAP_DIR / ('probe_owner_%d.map' % i)
        make_map(owner, path)
        import shutil
        shutil.copyfile(path, GAME_DIR / 'spawnmap.ini')
        shutil.copyfile(SPAWN, GAME_DIR / 'spawn.ini')
        t0 = time.time()
        try:
            result = await test_owner(owner, type_names)
        except Exception as exc:  # noqa: BLE001
            result = {'owner': owner, 'error': repr(exc)[:300]}
        result['elapsed_s'] = round(time.time() - t0, 1)
        print('[probe]', result, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
