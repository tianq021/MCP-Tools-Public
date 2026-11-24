// Local DSH delegation using the installed session API; never print credentials.
import fs from 'node:fs';
const [command, sessionId, file] = process.argv.slice(2);
const info = JSON.parse(fs.readFileSync('F:/deepseekA/logs/server.json', 'utf8').replace(/^\uFEFF/, ''));
const log = fs.readFileSync(info.stdout, 'utf8');
const url = log.match(/http:\/\/127\.0\.0\.1:3080\/\?token=\S+/)?.[0];
if (!url) throw Error('Local DSH launch URL unavailable');
const auth = await fetch(url, {redirect:'manual', signal:AbortSignal.timeout(10000)});
const cookie = auth.headers.getSetCookie().map(v => v.split(';')[0]).join('; ');
if (!cookie) throw Error('Local DSH authentication unavailable');
let method, args;
if (command === 'list') { method='session/list'; args={_request:{}}; }
else if ((command === 'prompt' || command === 'steer') && sessionId && file) {
  method='session/prompt';
  args={request:{requestId:crypto.randomUUID(),sessionId,mode:command==='steer'?'steer':'queue',clientTimeZone:'Asia/Shanghai',content:[{type:'text',text:fs.readFileSync(file,'utf8')}]}};
} else if (command === 'page' && sessionId && file) {
  method='session/page'; args={request:{address:{kind:'session',sessionId},throughSeq:Number(file),maxMessages:3}};
} else if (command === 'steer-queued' && sessionId && file) {
  method='session/updateQueue'; args={request:{sessionId,itemId:file,action:{kind:'steer'}}};
} else throw Error('Usage: dsh_delegate.mjs list | page SESSION | prompt SESSION FILE');
const res=await fetch('http://127.0.0.1:3080/api/'+method, {method:'POST',headers:{'content-type':'application/json',cookie,origin:'http://127.0.0.1:3080'},body:JSON.stringify({type:'client-request',rpcId:crypto.randomUUID(),method,payload:{args}}),signal:AbortSignal.timeout(20000)});
if (!res.ok) throw Error(`Local DSH API HTTP ${res.status}`);
const payload=await res.json();
if (command === 'list' && payload.result?.ok) {
  console.log(JSON.stringify(payload.result.value.items.filter(s => s.cwd?.toLowerCase() === 'e:\\ra2mcp' && !s.parentSessionId && (!sessionId || s.sessionId===sessionId)).slice(0,5).map(s=>({sessionId:s.sessionId,running:s.running,available:s.agentAvailable,updatedAt:s.updatedAt,seq:s.projections?.asOfSeq,title:s.projections?.values?.title,lastTurns:s.projections?.values?.turnOutline?.slice(-1),pending:Object.fromEntries(Object.entries(s.projections?.values?.inbox??{}).map(([k,v])=>[k,v.map(x=>({id:x.id,itemId:x.itemId,messageId:x.message?.id}))]))})),null,2));
} else console.log(JSON.stringify(payload));
