"""Read-only observations for human lessons and native wave probes. No orders."""
from pathlib import Path
import asyncio,collections,gzip,json,subprocess,sys,time,traceback,threading,ctypes,ctypes.wintypes as wt
ROOT=Path(__file__).resolve().parents[2]
for p in [ROOT,ROOT/'scripts',ROOT/'vendor/pyra2yr']:sys.path.insert(0,str(p))
sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'))
from observer import collect_private as read
from observer import private_runtime as runtime
from classroom import GAME,PORT,dump

def hide_owned_default_windows(proc,out):
    # Some DirectDraw windows migrate to Default despite lpDesktop. Check the
    # actual input desktop too, using the owned PID, without switching desktops.
    import winapi
    while proc.poll() is None:
        windows=[w for w in winapi.list_windows() if w['pid']==proc.pid]
        for w in windows:winapi.hide_window(w['hwnd'])
        if windows:dump(out/'DEFAULT_DESKTOP_WINDOWS.json',dict(pid=proc.pid,hidden_windows=len(windows)))
        time.sleep(.1)

class MessageReader:
    """Read only the native 14-slot MessageList buffers, no process writes."""
    def __init__(self,pid):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];self.k.OpenProcess.restype=wt.HANDLE
        self.k.ReadProcessMemory.argtypes=[wt.HANDLE,wt.LPCVOID,wt.LPVOID,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        self.k.CloseHandle.argtypes=[wt.HANDLE];self.h=self.k.OpenProcess(0x1010,False,pid)
    def read(self):
        b=ctypes.create_string_buffer(0x149c);n=ctypes.c_size_t()
        if not self.h or not self.k.ReadProcessMemory(self.h,0xA8BC60,b,len(b),ctypes.byref(n)):return []
        return [t for i in range(14) if (t:=b.raw[712+i*324:712+(i+1)*324].decode('utf-16le',errors='replace').split('\0')[0])]
    def close(self):
        if self.h:self.k.CloseHandle(self.h);self.h=None

async def collect(out,proc,smoke,visual=False):
    from pyra2yr.network import DualClient
    client=DualClient('127.0.0.1',PORT,timeout=3);client.connect()
    read._patch_aiohttp_message_size();start=time.monotonic();names={};types={};n=0;first=None;last=None;peak={};seen=collections.defaultdict(set);positions={};errors=[];controller=None
    plan=json.loads((out/'PLAN.json').read_text(encoding='utf-8'));endframe=(plan['waves'][-1]['at_seconds']+130)*15;messages={};ui=MessageReader(proc.pid);last_event=-1;event_frames=0;event_gaps=[]
    try:
        if smoke and not visual:controller=runtime.enable_headless(proc,out)
        with gzip.open(out/'OBSERVATIONS.jsonl.gz','wt',encoding='utf-8') as f,gzip.open(out/'EVENTS.jsonl.gz','wt',encoding='utf-8',compresslevel=1) as ef:
            while proc.poll() is None and time.monotonic()-start<(420 if smoke else 12*3600):
                crash=GAME/'EXCEPT_CNCNET.TXT'
                if crash.exists() and crash.stat().st_mtime>time.time()-(time.monotonic()-start):
                    import shutil
                    shutil.copy2(crash,out/'EXCEPT_CNCNET.TXT');errors.append('native_internal_error');break
                if (out/'STOP').exists():errors.append('local_stop_file');break
                state,error=await read.get_state(client)
                if error:
                    errors.append(error)
                    if len(errors)>20:break
                    await asyncio.sleep(.5);continue
                if int(state.current_frame)<1:await asyncio.sleep(.2);continue
                if not names:
                    initial=await read.read_initial(client)
                    if initial is None:continue
                    from google.protobuf.json_format import MessageToDict
                    dump(out/'CATALOG.json',MessageToDict(initial,preserving_proto_field_name=True))
                    (out/'INITIAL_STATE.pb').write_bytes(initial.SerializeToString())
                    names={int(t.pointer_self):str(t.name) for t in initial.object_types};types={int(t.pointer_self):dict(cost=int(t.cost),strength=int(t.strength)) for t in initial.object_types}
                    if not smoke:
                        try:
                            soa=await read.read_map_soa(client)
                            if soa is not None:
                                with gzip.open(out/'INITIAL_MAP_SOA.pb.gz','wb',compresslevel=1) as mf:mf.write(soa.SerializeToString())
                                dump(out/'MAP_CAPTURE.json',dict(width=int(soa.map_width),height=int(soa.map_height),frame=int(state.current_frame),fields=[field.name for field,_ in soa.ListFields()]))
                        except Exception as exc:errors.append('initial_map: '+repr(exc))
                sample=read._state_sample(state,names)
                if visual and smoke and first is not None:
                    own=[o for o in sample['objects'] if o[0]==0 and o[2]>0 and o[5] and not o[6]]
                    initial=[o for o in first['objects'] if o[0]==0 and o[2]>0 and o[5] and not o[6]]
                    if {o[8] for o in own}!={o[8] for o in initial} or sample['houses'][0][2]<first['houses'][0][2]:
                        smoke=False
                        dump(out/'HUMAN_TAKEOVER.json',dict(frame=sample['frame'],automatic_cleanup_cancelled=True,reason='Visible read-only test detected changed player assets or spending'))
                if n%4==0:
                    for message in ui.read():messages.setdefault(message,sample['frame'])
                sample['house_flags']=[dict(index=int(h.array_index),human=bool(h.is_human_player),current=bool(h.current_player),name=str(h.name),faction=str(h.faction),type_index=int(h.type_array_index)) for h in state.houses]
                f.write(json.dumps(sample,ensure_ascii=False)+'\n');f.flush();n+=1;last=sample
                if not smoke:
                    try:
                        buffer=await read.read_event_buffer(client)
                        if buffer is not None:
                            for frame,event in zip(buffer.frame,buffer.lists):
                                frame=int(frame)
                                if frame<=last_event:continue
                                if last_event>=0 and frame>last_event+1:event_gaps.append([last_event+1,frame-1])
                                ef.write(json.dumps(dict(frame=frame,events=MessageToDict(event,preserving_proto_field_name=True)))+'\n');last_event=frame;event_frames+=1
                            ef.flush()
                    except Exception as exc:errors.append('event_buffer: '+repr(exc))
                if first is None:first=sample;dump(out/'FIRST.json',first)
                counts=collections.Counter()
                for owner,code,hp,x,y,onmap,limbo,mission,addr,pointer in sample['objects']:
                    if hp<=0 or not onmap or limbo:continue
                    key=f'{owner}:{code}';counts[key]+=1;seen[key].add(addr)
                    if key not in positions:positions[key]=dict(first_frame=sample['frame'],first_cell=[x,y],min_x=x,max_y=y,missions=[])
                    pos=positions[key];pos['min_x']=min(pos['min_x'],x);pos['max_y']=max(pos['max_y'],y)
                    if mission not in pos['missions']:pos['missions'].append(mission)
                for k,v in counts.items():peak[k]=max(peak.get(k,0),v)
                if n%10==0:
                    dump(out/'PROGRESS.json',dict(samples=n,frame=sample['frame'],wall_seconds=round(time.monotonic()-start,1),counts=counts,event_frames=event_frames,event_gaps=event_gaps,native_state_bytes=(out/'NATIVE_STATES.pb.gz').stat().st_size if (out/'NATIVE_STATES.pb.gz').exists() else 0))
                    dump(out/'NATIVE_MESSAGES.json',messages)
                if smoke and sample['frame']>=endframe:break
                await asyncio.sleep(.25 if smoke else 1)
    finally:
        try:await asyncio.wait_for(client.stop(),5)
        except BaseException:pass
        if smoke:cleanup=runtime.cleanup_owned(proc,controller)
        else:cleanup={'game_exited':proc.poll() is not None,'user_game_left_running':proc.poll() is None}
        dump(out/'RESULT.json',dict(status='RECORDED' if n else 'FAILED',samples=n,first_frame=first['frame'] if first else None,last_frame=last['frame'] if last else None,peak=peak,seen_counts={k:len(v) for k,v in seen.items()},positions=positions,errors=errors,cleanup=cleanup,labels='observations only; human intent and causal damage attribution are not inferred'))
        if last:dump(out/'LAST.json',last)
        ui.close();dump(out/'NATIVE_MESSAGES.json',messages);dump(out/'EVENT_COVERAGE.json',dict(event_frames=event_frames,last_event_frame=last_event,gaps=event_gaps))

def play(out,smoke=False,visual=False):
    out=Path(out)
    if smoke and not visual:
        proc=runtime.launch_hidden(GAME,out)
        threading.Thread(target=hide_owned_default_windows,args=(proc,out),daemon=True).start()
    else:
        with (out/'game-stdout.log').open('wb') as stdout,(out/'game-stderr.log').open('wb') as stderr:
            proc=subprocess.Popen([str(GAME/'gamemd-spawn-ra2yrcpp.exe'),'-SPAWN','-CD'],cwd=GAME,stdout=stdout,stderr=stderr)
        dump(out/'OWNED_GAME.json',dict(pid=proc.pid,path=str(GAME/'gamemd-spawn-ra2yrcpp.exe')))
    if not smoke:
        # The bridge samples pending queues after execution and can therefore
        # return empty lists for a human skirmish. Preserve consumed DoList slots
        # through a separate read-only process from the beginning of each game.
        with (out/'native-events-stdout.log').open('wb') as so,(out/'native-events-stderr.log').open('wb') as se:
            helper=subprocess.Popen([sys.executable,'-B','-X','utf8',str(ROOT/'scripts/wave_defense/native_events.py'),str(out)],cwd=ROOT,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW)
        dump(out/'EVENT_COLLECTOR_PROCESS.json',dict(pid=helper.pid,path=str(ROOT/'scripts/wave_defense/native_events.py')))
    try:asyncio.run(collect(out,proc,smoke,visual))
    except BaseException as exc:
        dump(out/'ERROR.json',dict(error=repr(exc),traceback=traceback.format_exc()))
        if smoke and proc.poll() is None:runtime.cleanup_owned(proc,None)
        raise

if __name__=='__main__':play(Path(sys.argv[1]),'--smoke' in sys.argv)
