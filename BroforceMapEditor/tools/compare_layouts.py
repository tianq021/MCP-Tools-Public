import json
from pathlib import Path
from html import escape
root=Path(__file__).resolve().parents[1]
lines=(root/'evidence/ai_real_map.txt').read_text(encoding='utf-8').splitlines()
w,h=map(int,lines[0].split(','))
rows=[list(map(int,r.split(','))) for r in lines[1:h+1]]
ds=[r.split(',') for r in lines[h+2:]]
p=json.loads((root/'maps/emerald_operation_06.json').read_text(encoding='utf-8'))
catalog=json.loads((root/'research/catalog.json').read_text(encoding='utf-8'))['TerrainType']
ours=[[catalog[p['legend'][c]] for c in row] for row in p['tiles']]
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1640" height="1020" viewBox="0 0 1640 1020"><rect width="100%" height="100%" fill="#14202b"/>']
class Drawing:
    def rectangle(self, box, fill):
        x,y,r,b=box
        parts.append(f'<rect x="{x}" y="{y}" width="{r-x+1}" height="{b-y+1}" fill="{fill}"/>')
    def ellipse(self, box, fill):
        x,y,r,b=box
        parts.append(f'<circle cx="{(x+r)/2}" cy="{(y+b)/2}" r="2" fill="{fill}"/>')
    def text(self, pos, text, fill):
        parts.append(f'<text x="{pos[0]}" y="{pos[1]+15}" fill="{fill}" font-family="sans-serif" font-size="16">{escape(text)}</text>')
d=Drawing()
colors={0:'#14202b',1:'#655338',2:'#929995',18:'#b48350',6:'#e2bd62',27:'#d3ba80',21:'#4d5b69',16:'#e65340',30:'#967451',32:'#b59850',3:'#627b80',5:'#bd9d70'}
def draw(rows,ds,ox,oy,s):
    height=len(rows)
    for y,row in enumerate(rows):
        for x,v in enumerate(row):
            if v: d.rectangle((ox+x*s,oy+y*s,ox+(x+1)*s-1,oy+(y+1)*s-1),fill=colors.get(v,'#718b7d'))
    for x,y,t,*_ in ds:
        c={'Mook':'#ff6666','Cage':'#f1dc78','CheckPoint':'#69e1e8','SpawnPoint':'#ffffff','PureEvil':'#f268d9'}.get(t)
        if c:
            x,y=int(x),int(y)
            d.ellipse((ox+x*s-2,oy+(height-1-y)*s-2,ox+x*s+2,oy+(height-1-y)*s+2),fill=c)
draw(rows,ds,20,60,6)
draw(ours,[(v['x'],v['y'],v['type']) for v in p['doodads']],450,60,3)
d.text((20,20),'AI REAL MAP 01 | 64 x 150',fill='white')
d.text((450,20),'EMERALD 06 | 384 x 72',fill='white')
d.text((450,310),'Red: enemies / Yellow: rescue / Cyan: checkpoint',fill='white')
d.text((450,340),'Reference: vertical rooms, bridges, caves, structures.',fill='white')
d.text((450,365),'Previous: repeated hills over a continuous foundation.',fill='white')
parts.append('</svg>')
(root/'evidence/layout_comparison.svg').write_text(''.join(parts),encoding='utf-8')
