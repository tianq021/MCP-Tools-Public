"""Authenticated local DSH RPC client; credentials stay in memory."""
import http.cookiejar
import json
from pathlib import Path
import re
import sys
import urllib.request
import uuid

info = json.loads(Path(r'F:\deepseekA\logs\server.json').read_text(encoding='utf-8-sig'))
log = Path(info['stdout']).read_text(encoding='utf-8-sig')
url = re.search(r'http://127\.0\.0\.1:3080/\?token=\S+', log).group(0)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
with opener.open(url, timeout=15) as response:
    response.read()
method = sys.argv[1]
payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig')) if len(sys.argv) > 2 else {'_request': {}}
request = urllib.request.Request('http://127.0.0.1:3080/api/' + method,
    data=json.dumps({'type':'client-request','rpcId':str(uuid.uuid4()),'method':method,'payload':{'args':payload}}).encode(),
    headers={'Content-Type':'application/json'})
with opener.open(request, timeout=45) as response:
    result = json.load(response)
if method == 'session/list' and result.get('result',{}).get('ok'):
    result['result']['value']['items'] = [x for x in result['result']['value']['items'] if x.get('cwd') == r'E:\Civ6']
    for x in result['result']['value']['items']:
        p=x.get('projections',{})
        x['projections']={'asOfSeq':p.get('asOfSeq'),'values':{k:v for k,v in p.get('values',{}).items() if k in ('title','inbox','turnOutline','userQuestions')}}
if method == 'session/page' and result.get('result',{}).get('ok'):
    for record in result['result']['value'].get('records',[]):
        data=record.get('event',{}).get('data',{})
        data.pop('stream',None)
        msg=data.get('message',{})
        msg.pop('source',None)
        if 'content' in msg:
            msg['content']=[p for p in msg['content'] if p.get('type') != 'reasoning']
Path(r'E:\Civ6\dsh_last_response.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if method == 'session/list' and result.get('result',{}).get('ok'):
    for x in result['result']['value']['items']:
        values=x.get('projections',{}).get('values',{})
        outline=values.get('turnOutline',[])
        x['projections']['values']={k:v for k,v in values.items() if k != 'turnOutline'}
        x['lastTurn']=outline[-1] if outline else None
print(json.dumps(result,ensure_ascii=False))
