"""断崖营救：原创纵向设施、救援支路与地表村寨。"""
import json
from pathlib import Path
from collections import Counter
from map_authoring import validate

def build():
    w,h=112,144
    g=[['.']*w for _ in range(h)]; bg=[['.']*w for _ in range(h)]
    legend={'.':'Empty','#':'Earth','S':'Stone','W':'Wood','L':'Ladder','R':'CaveRock',
            'b':'BackgroundStone','f':'BackgroundFactory','c':'BackgroundEarth','t':'WoodBackground',
            'B':'Barrel','P':'PropaneBarrel','H':'ThatchRoof','F':'FallingBlock'}
    def fill(a,b,y,z,ch,layer=g):
        for r in range(y,z+1):
            for x in range(a,b+1): layer[r][x]=ch
    fill(0,111,0,5,'R')
    fill(0,3,6,83,'R');fill(108,111,6,95,'R')
    floors=[12,34,56,78,100,122]
    for i,y in enumerate(floors):
        fill(5,106,y-2,y,'#' if i in (0,3,4) else 'S')
        if i<3: fill(4,107,y+1,y+18,'c' if i==0 else ('f' if i==1 else 'b'),bg)
        # Distinct gaps with timber crossings, with a catch platform below.
        if i in (0,2,4):
            left,right=[(40,55),(26,45),(55,73)][[0,2,4].index(i)]
            fill(left,right,y-2,y,'.');fill(left,right,y,y,'W')
            fill(left-2,right+2,y-7,y-7,'S')
            for x in (left,right): fill(x,x,y-6,y-1,'L')
    # Alternate traversal direction each floor, with a protected midpoint landing.
    for i,y in enumerate(floors[:-1]):
        x=104 if i%2==0 else 7
        fill(x,x,y+1,y+23,'L')
        fill(max(4,x-4),min(107,x+4),y+11,y+11,'S')
        g[y+11][x]='L';g[y+22][x]='L'
    ds=[];occupied=set()
    def add(x,y,t,v=0):
        if (x,y) in occupied: return
        occupied.add((x,y));ds.append(dict(x=x,y=y,type=t,variation=v))
    def stand(x,r,t,v=0):
        g[r][x]='S' if r in (34,56,122) else '#';g[r+1][x]='.'
        add(x,r+1,t,v)
    stand(9,12,'SpawnPoint')
    for i,r in enumerate(floors):
        entry=15 if i%2==0 else 97
        stand(entry,r,'CheckPoint')
        stand(entry+3 if i%2==0 else entry-3,r,'AmmoCrate')
        for j,x in enumerate([24,32,61,69,82,91]):
            if i==0 and x<30: continue
            stand(x,r,'Mook',6 if i>=4 and j==4 else (4 if j%3==0 else 0))
        for x in [20,64,87]: stand(x,r,'Crate')
        # Side room above the main route, reachable by ladder; guarded rescue reward.
        a,b=(24,42) if i%2==0 else (65,84)
        sy=r+9
        fill(a,b,sy,sy,'W' if i==3 else 'S')
        fill(a-2,a-2,r+1,sy+1,'L')
        fill(a,b,r+1,sy-1,'t' if i==3 else 'b',bg)
        for x in (a+3,b-3):stand(x,sy,'Mook',0)
        stand(a+7,sy,'AmmoCrate')
        add(b-1,sy+2,'Cage')
        # Cage uses a two-row anchor; solid support under entire footprint.
        fill(b-1,b+1,sy,sy,'S')
        for x in range(b-1,b+2): fill(x,x,sy+1,sy+3,'.')
        for x in (49,76):
            if g[r+1][x]=='.' and (x,r+1) not in occupied: g[r+1][x]='B'
        if i<3:
            for x in range(10,104,13):add(x,r+5,'CaveParallax1',0)
            for x in range(12,105,19):add(x,r+3,'ScaffoldingBackground',3)
            for x in [28,38,67,80]:add(x,r+2,'IndoorDoodad',0)
    # Distinct surface village: two houses with eaves and timber interiors.
    for a,b,r in [(48,63,78),(45,60,100),(77,96,100)]:
        fill(a,b,r+1,r+8,'t',bg)
        fill(a-1,b+1,r+9,r+9,'H')
        for x in [a,b]:fill(x,x,r+5,r+8,'W')
        add(a+3,r+2,'IndoorDoodad',0);add(b-3,r+2,'IndoorDoodad',1)
    # Top fortified roofline and outward-looking sentry balconies.
    for a,b in [(12,22),(44,55)]:
        fill(a,b,132,133,'S');fill(a-1,a-1,123,134,'L')
        stand(a+4,133,'Mook',4);stand(a+7,133,'AmmoCrate')
        fill(a,b,123,131,'b',bg)
    for x,y in [(13,80),(44,80),(92,80),(37,102),(75,102),(28,124)]:add(x,y,'TreeBackground',0)
    for x,y in [(46,80),(54,102),(95,124)]:add(x,y,'TreeBushForeground',0)
    stand(98,122,'PureEvil')
    # A brittle barricade opens a lower shortcut when approaching the armoury exit.
    fill(51,51,35,38,'W')
    triggers=[dict(type=0,blx=53,bly=35,urx=58,ury=39,action='Collapse:军械库爆破捷径',target_x=51,target_y=35,damage=100)]
    p=dict(schema=1,name='断崖营救',width=w,height=h,legend=legend,
        description='2026-11-15；原创关卡第七版《断崖营救》；从地下矿井穿越军械库、牢房、山腰村寨至山顶要塞；救援支路、木桥、爆破捷径；山顶右端撤离，桥接加载跳过片尾。',
        tiles=[''.join(r) for r in reversed(g)],background=[''.join(r) for r in reversed(bg)],doodads=ds,
        goal=dict(x=103,y=123),triggers=triggers,
        metadata={'生成器':'生成断崖营救.py','区域':['地下矿井','军械仓库','囚禁设施','山腰村寨','断崖木桥','山顶要塞'],'主路高度':floors,'版本':'0.7'})
    validate(p)
    return p

if __name__=='__main__':
    p=build();root=Path(__file__).resolve().parents[1]
    (root/'maps/断崖营救_草稿.json').write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(Counter(d['type'] for d in p['doodads'])),ensure_ascii=True))


