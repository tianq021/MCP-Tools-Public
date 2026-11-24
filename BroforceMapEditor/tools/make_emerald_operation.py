"""Six-sector authored level; deterministic geometry and supported placements."""
import json
from collections import Counter
from pathlib import Path
from map_authoring import validate

def build():
    w,h=384,72
    grid=[['.']*w for _ in range(h)]
    legend={'.':'Empty','#':'Earth','S':'Stone','W':'Wood','L':'Ladder',
            'b':'BackgroundStone','f':'BackgroundFactory','t':'WoodBackground',
            'B':'Barrel','P':'PropaneBarrel'}
    tops=[8]*w
    # Individually shaped terrain; every main-route step changes at most one row.
    for left,right,peak in [(24,48,12),(78,111,14),(144,180,18),(202,236,11),(259,299,20),(320,356,16)]:
        for x in range(left,right+1):
            tops[x]=8+min(peak-8,(x-left)//2,(right-x)//2)
    for x,top in enumerate(tops):
        for y in range(top+1): grid[y][x]='S' if y<3 else '#'
    ds=[]; occupied=set(); shelves=[]
    def add(x,y,kind,var=0):
        if (x,y) in occupied: raise ValueError(('overlap',x,y,kind))
        occupied.add((x,y)); ds.append(dict(x=x,y=y,type=kind,variation=var))
    def ground(x,kind,var=0): add(x,tops[x]+1,kind,var)
    ground(5,'SpawnPoint')
    for x in [20,59,75,123,139,187,201,247,259,309,323,362]: ground(x,'CheckPoint')
    # Suspended roofs make optional high routes; main route retains >=6 cells of headroom.
    for i,(a,b) in enumerate([(30,45),(84,105),(146,177),(211,233),(267,294),(328,353)]):
        y=max(tops[a:b+1])+8
        mat='W' if i in (0,2) else 'S'
        for x in range(a,b+1):
            grid[y][x]=mat
            if i in (1,3,5):
                for r in range(tops[x]+1,y): grid[r][x]='f' if i==3 else 'b'
        # Open ladder shafts at both ends; ladders never replace an entity support tile.
        for x in [a-1,b+1]:
            for r in range(tops[x]+1,y+2): grid[r][x]='L'
        shelves.append((a,b,y))
        for x in [a+4,b-4]: add(x,y+1,'Mook',4 if i>=2 else 0)
        add(a+8,y+1,'AmmoCrate')
        add(b-1,y+1,'Crate')
    # Clear grounded cells (background tiles belong to backdrop, not collision headroom).
    enemy_x=[27,39,51,67,83,95,108,116,133,147,160,173,181,197,210,222,234,241,255,270,283,297,303,317,330,342,354,369]
    for i,x in enumerate(enemy_x):
        grid[tops[x]+1][x]='.'
        ground(x,'Mook',6 if i>16 and i%4==0 else (4 if i%5==0 else 0))
    for x in [12,54,70,119,134,183,196,242,253,304,316,358,373]:
        grid[tops[x]+1][x]='.'; ground(x,'AmmoCrate')
    for x in [15,32,46,62,88,102,126,152,166,190,215,228,250,274,289,312,335,349,365]:
        grid[tops[x]+1][x]='.'; ground(x,'Crate')
    # Cage anchors occupy rows y and y-1; reserve a clear 3x3 footprint over solid ground.
    for x in [17,56,72,121,136,185,244,306,360]:
        y=max(tops[x:x+3])+2
        for c in range(x,x+3):
            for r in range(tops[c]+1,y+2): grid[r][c]='.'
            for r in range(tops[c]+1,y-1): grid[r][c]='#'
        add(x,y,'Cage')
    # Explosive props off the player/checkpoint/cage lanes; no initial overlap with enemies.
    barrels=[]
    for x in [35,91,156,219,278,339]:
        y=tops[x]+1
        if (x,y) not in occupied: grid[y][x]='B'; barrels.append((x,y))
    for a,b,y in shelves:
        grid[y+1][a+1]='P'; barrels.append((a+1,y+1))
    # Background vegetation in open sections only, with conservative known variation 0.
    for x in [8,22,61,76,128,140,192,204,252,263,314,325,363,377]:
        add(x,tops[x]+2,'Parallax1')
    ground(378,'PureEvil')
    p=dict(schema=1,name='翠幕行动 · 六区远征',width=w,height=h,
        description='2026-11-15；MCP V0.6；384x72 六区长关卡：登陆、山村、峡谷、仓库、监牢、要塞；双层路线、救援囚笼和密集补给；抵达最右端撤离，桥接模式跳过片尾。',
        legend=legend,tiles=[''.join(r) for r in reversed(grid)],doodads=ds,goal=dict(x=380,y=9),
        metadata=dict(generator='make_emerald_operation.py',version='0.6',seed=0,
            sectors=['林地登陆','山村补给站','峡谷双层通道','军械仓库','山地监牢','撤离要塞'],
            main_surface=tops,upper_routes=shelves,explosives=barrels))
    validate(p)
    # Terrain-only traversal check: no abrupt cliffs or blocked main route.
    assert max(abs(a-b) for a,b in zip(tops,tops[1:]))<=1
    for x in range(w):
        assert all(grid[y][x] not in '#SW' for y in range(tops[x]+1,tops[x]+5)),x
    return p

if __name__=='__main__':
    p=build()
    path=Path(__file__).resolve().parents[1]/'maps/emerald_operation_06.draft.json'
    path.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'draft':str(path),'counts':dict(Counter(d['type'] for d in p['doodads']))},ensure_ascii=True))
