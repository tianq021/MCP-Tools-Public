import json
from pathlib import Path
import map_authoring as a

root=Path(__file__).resolve().parents[1]
note='2026-11-15；AI REAL MAP 01 参考图改编；保留洞穴、村寨、升降机、滑索和坍塌捷径；重排守卫类型、增加弹药补给；跳过片尾（需本机桥接运行）。'
name='断崖营救_场景重构版'
a.bridge.wait_ready()
out=[]
out.append(a.command({'op':'inspect_level','path':r'C:\Users\hp\AppData\Local\BroforceMapEditor\maps\ai_real_01.bfc'}))
out.append(a.command({'op':'remix_reference','name':name,'description':note}))
# Keep original finale and add an explicit extraction region on the summit.
out.append(a.command({'op':'add_trigger','type':0,'blx':58,'bly':135,'urx':63,'ury':141,'action':'LevelEvent:山顶撤离'}))
target=root/'maps'/f'{name}.bfc'
if target.exists():raise FileExistsError(target)
out.append(a.command({'op':'save_bfg','path':str(target),'copy_to_levels':'1'}))
(root/'evidence/场景重构回执.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=True))
