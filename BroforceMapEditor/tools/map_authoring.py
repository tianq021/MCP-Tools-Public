"""Validated, reproducible map projects; coordinates are bottom-up grid cells."""
import json
import re
import threading
from pathlib import Path
import bridge_client as bridge

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / 'maps'
LOCK = threading.RLock()
CATALOG = json.loads((ROOT / 'research/catalog.json').read_text(encoding='utf-8'))
SOLID = {
    terrain for terrain, solid in json.loads(
        (ROOT / 'research/terrain_solid.json').read_text(encoding='utf-8')
    ).items() if solid
}

def project_path(name):
    if not re.fullmatch(r'[\w-]{1,64}', name):
        raise ValueError('Map id must contain only letters, digits, underscores or hyphens')
    return MAPS / (name + '.json')

def validate(project):
    for key in ('name', 'description'):
        if not isinstance(project.get(key), str) or any(c in project[key] for c in '\r\n\x00'):
            raise ValueError(f'{key} must be a single-line string')
    w, h = project['width'], project['height']
    if not (8 <= w <= 512 and 8 <= h <= 256):
        raise ValueError('Invalid dimensions')
    rows, legend = project['tiles'], project['legend']
    if len(rows) != h or any(len(row) != w for row in rows):
        raise ValueError('Tile dimensions do not match map')
    if any(len(k) != 1 or k in ',:=\r\n' or v not in CATALOG['TerrainType'] for k, v in legend.items()):
        raise ValueError('Invalid terrain legend')
    if any(c not in legend for row in rows for c in row):
        raise ValueError('Undefined tile character')
    def terrain(x, y):
        return legend[rows[h - 1 - y][x]]
    spawns = 0
    for d in project['doodads']:
        x, y = d['x'], d['y']
        if not (isinstance(x, int) and isinstance(y, int) and 0 <= x < w and 0 <= y < h):
            raise ValueError(f'Out of bounds: {d}')
        if d['type'] not in CATALOG['DoodadType']:
            raise ValueError(f'Unknown doodad: {d}')
        if d['type'] in {'Mook', 'SpawnPoint', 'CheckPoint', 'Crate', 'AmmoCrate', 'PureEvil'}:
            if y < 1 or terrain(x, y - 1) not in SOLID or terrain(x, y) != 'Empty':
                raise ValueError(f'Entity needs clear cell immediately above solid support: {d}')
        spawns += d['type'] == 'SpawnPoint'
    if spawns != 1:
        raise ValueError('Exactly one SpawnPoint required')
    goal = project.get('goal')
    if not goal or not (0 <= goal['x'] <= w-4 and 1 <= goal['y'] <= h-6):
        raise ValueError('A bounded extraction goal is required')
    if not project.get('description', '').strip():
        raise ValueError('Chinese map notes required')
    return {'valid': True, 'dimensions': f'{w}x{h}', 'doodads': len(project['doodads'])}

def save_project(map_id, project):
    result = validate(project)
    path = project_path(map_id)
    MAPS.mkdir(exist_ok=True)
    # Create only: never overwrite a previous map.
    with path.open('x', encoding='utf-8') as f:
        json.dump(project, f, ensure_ascii=False, indent=2)
    return {**result, 'path': str(path), 'map_id': map_id}

def read_project(map_id):
    return json.loads(project_path(map_id).read_text(encoding='utf-8'))

def command(payload):
    result = bridge.send_command(payload, timeout=30)
    if result.get('ok') != '1':
        raise bridge.BridgeError(str(result))
    return result

def export_map(map_id, load=False):
    with LOCK:
        p = read_project(map_id)
        validate(p)
        target = MAPS / (map_id + '.bfc')
        if target.exists():
            raise ValueError('Export already exists; create a new map id')
        bridge.ensure_ready()
        tiles = MAPS / (map_id + '.tiles.txt')
        doodads = MAPS / (map_id + '.doodads.txt')
        tiles.write_text('\n'.join(p['tiles']), encoding='utf-8')
        doodads.write_text('\n'.join(f"{d['x']},{d['y']},{d['type']},{d.get('variation', 0)}" for d in p['doodads']), encoding='utf-8')
        receipts = [command({'op':'new_map', 'width':p['width'], 'height':p['height'], 'name':p['name'],
            'author':'BROFORCEMAPBRIDGE', 'description':p['description'], 'theme':0, 'weather':0, 'spawn':4})]
        receipts.append(command({'op':'set_tiles', 'tiles':str(tiles), 'legend':','.join(f'{k}:{v}' for k,v in p['legend'].items())}))
        if p.get('background'):
            bg = MAPS / (map_id + '.背景.txt')
            bg.write_text('\n'.join(p['background']), encoding='utf-8')
            receipts.append(command({'op':'set_tiles', 'layer':'background', 'tiles':str(bg),
                'legend':','.join(f'{k}:{v}' for k,v in p['legend'].items())}))
        receipts.append(command({'op':'add_doodads', 'doodads':str(doodads)}))
        if int(receipts[-1]['added']) != len(p['doodads']):
            raise ValueError('Bridge dropped doodads')
        goal = p.get('goal')
        for trigger in p.get('triggers', []):
            receipts.append(command(dict(trigger, op='add_trigger')))
        if goal:
            receipts.append(command({'op':'add_trigger', 'type':0, 'blx':goal['x'], 'bly':goal['y'],
                'urx':goal['x']+3, 'ury':goal['y']+5, 'action':'LevelEvent:Extraction'}))
        receipts.append(command({'op':'save_bfg', 'path':str(target)}))
        result = {'path':str(target), 'receipts':receipts}
        (MAPS / (map_id + '.receipt.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if load:
            receipts.append(command({'op':'load'}))
            (MAPS / (map_id + '.receipt.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result

def new_expedition():
    w, h = 100, 32
    grid = [['.' for _ in range(w)] for _ in range(h)]
    # Continuous deep foundation, small stepped outposts; no accidental death pits.
    for y in range(7):
        for x in range(w):
            grid[y][x] = '#'
    tops = [6] * w
    for left, right in [(23, 34), (49, 60), (75, 86)]:
        for x in range(left, right + 1):
            top = 6 + min(3, x-left+1, right-x+1)
            tops[x] = top
            for y in range(7, top+1):
                grid[y][x] = '#'
    ds = []
    def place(x, kind):
        ds.append({'x':x, 'y':tops[x]+1, 'type':kind, 'variation':0})
    place(4, 'SpawnPoint')
    for x in [19, 44, 70]: place(x, 'CheckPoint')
    for x in [15, 28, 38, 54, 65, 80, 90]: place(x, 'Mook')
    for x in [11, 40, 68]: place(x, 'AmmoCrate')
    place(95, 'PureEvil')
    return {'schema':1, 'name':'AI 丛林前哨 05', 'width':w, 'height':h, 'goal':{'x':96, 'y':7},
        'description':'2026-11-15；地图 MCP V0.5；连续地基、三处阶梯前哨、七名守卫、三个检查点；地表上方一格出生，明确 SpawnPoint 出场；到达最右侧撤离区触发胜利。',
        'legend':{'.':'Empty', '#':'Earth'}, 'tiles':[''.join(row) for row in reversed(grid)], 'doodads':ds}
