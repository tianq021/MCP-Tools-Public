"""Read-only native probe. Requires local bridge configured singleStep=false."""
import asyncio,json,sys,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'vendor'/'pyra2yr'))
async def probe():
 from pyra2yr.network import DualClient
 from ra2yrproto import commands_yr
 from google.protobuf.json_format import MessageToDict
 c=DualClient('127.0.0.1',14521,timeout=2)
 c.connect()
 try:
  cmd=commands_yr.GetGameState()
  result=await asyncio.wait_for(c.exec_command(cmd,timeout=3),5)
  if not result.result.Unpack(cmd): raise RuntimeError('Unexpected native response')
  state=MessageToDict(cmd.state)
  if not state: raise RuntimeError('Empty state; match readiness not established')
  return {'native_state_received':True,'state':state,'write_enabled':False}
 finally:
  tasks=[t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
  for t in tasks:t.cancel()
  await asyncio.gather(*tasks,return_exceptions=True)
if __name__=='__main__':
 try: result=asyncio.run(probe())
 except Exception as e: result={'native_state_received':False,'error':str(e) or type(e).__name__,'write_enabled':False}
 print(json.dumps(result,ensure_ascii=False,indent=2))
 sys.exit(0 if result['native_state_received'] else 1)
