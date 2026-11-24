"""Read executed native DoList slots, including recently consumed entries.

Only PROCESS_VM_READ is used. The bridge's post-loop Count=0 snapshots miss
these consumed slots. Never inject events or patch the engine.
"""
from pathlib import Path
import collections, ctypes, ctypes.wintypes as wt, datetime, gzip, json, struct, sys, time
import psutil
BASE=0x8B41F8; CAP=16384; SIZE=111
NAMES={1:'POWERON',2:'POWEROFF',3:'ALLY',4:'MEGAMISSION',5:'MEGAMISSION_F',6:'IDLE',7:'SCATTER',8:'DESTRUCT',9:'DEPLOY',10:'DETONATE',11:'PLACE',12:'OPTIONS',13:'GAMESPEED',14:'PRODUCE',15:'SUSPEND',16:'ABANDON',17:'PRIMARY',18:'SPECIAL_PLACE',19:'EXIT',20:'ANIMATION',21:'REPAIR',22:'SELL',23:'SELLCELL',24:'SPECIAL',25:'FRAMESYNC',26:'MESSAGE',27:'RESPONSE_TIME',28:'FRAMEINFO',29:'SAVEGAME',30:'ARCHIVE'}

class Reader:
    def __init__(self,pid):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];self.k.OpenProcess.restype=wt.HANDLE
        self.k.ReadProcessMemory.argtypes=[wt.HANDLE,wt.LPCVOID,wt.LPVOID,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        self.k.CloseHandle.argtypes=[wt.HANDLE];self.h=self.k.OpenProcess(0x1010,False,pid)
        if not self.h:raise OSError(ctypes.get_last_error())
    def read(self,addr,n):
        b=ctypes.create_string_buffer(n);got=ctypes.c_size_t()
        if not self.k.ReadProcessMemory(self.h,addr,b,n,ctypes.byref(got)) or got.value!=n:raise OSError(ctypes.get_last_error())
        return b.raw
    def close(self):
        if self.h:self.k.CloseHandle(self.h);self.h=None

def decode(raw):
    typ,executed,house,frame=struct.unpack_from('<BBbI',raw)
    d=raw[7:];row=dict(type=typ,name=NAMES.get(typ,str(typ)),executed=bool(executed),house=house,frame=frame,raw_hex=raw.hex())
    def target(offset):
        ident,rtti=struct.unpack_from('<iB',d,offset);return dict(id=ident,rtti=rtti)
    if typ==4:row['args']=dict(whom=target(0),mission=d[5],target=target(7),destination=target(12),follow=target(17),planning=bool(d[22]))
    elif typ in (9,22):row['args']=dict(whom=target(0))
    elif typ in (14,15,16):row['args']=dict(zip(['rtti','type_index','naval'],struct.unpack_from('<3i',d)))
    elif typ==11:
        rtti,ident,naval,x,y=struct.unpack_from('<3i2h',d);row['args']=dict(rtti=rtti,type_index=ident,naval=naval,cell=[x,y])
    return row

def collect(run):
    own=json.loads((run/'OWNED_GAME.json').read_text());proc=psutil.Process(own['pid'])
    if Path(proc.exe()).resolve()!=Path(own['path']).resolve():raise RuntimeError('Game identity mismatch')
    reader=Reader(proc.pid);dest=run/'EXECUTED_NATIVE_EVENTS';dest.mkdir(exist_ok=False)
    info=dict(status='RUNNING',pid=proc.pid,create_time=proc.create_time(),started=datetime.datetime.now().astimezone().isoformat(),source='native DoList consumed slots; read-only',coverage='Recovered retained entries; polling is not a lossless hook. See gap and wrap diagnostics.')
    count=0;first=None;last=None;types=collections.Counter();old={};loops=0;errors=[];wraps=0;last_tail=None;last_poll=time.monotonic();max_poll=0
    try:
        with gzip.open(dest/'events.jsonl.gz','wt',encoding='utf-8',compresslevel=1) as f:
            while proc.is_running() and not (dest/'STOP').exists():
                now=time.monotonic();max_poll=max(max_poll,now-last_poll);last_poll=now
                # A single 1.9 MB read every 0.2 s. Two equal headers reject a
                # snapshot crossing a queue append; no suspension of the game.
                before=reader.read(BASE,12);data=reader.read(BASE,12+115*CAP);after=reader.read(BASE,12)
                if before!=after or data[:12]!=after:time.sleep(.02);continue
                pending,head,tail=struct.unpack('<3i',after)
                if not (0<=pending<=CAP and 0<=head<CAP and 0<=tail<CAP):raise ValueError('Invalid ring header')
                if last_tail is not None and tail<last_tail:wraps+=1
                last_tail=tail
                if loops==0:
                    (dest/'INITIAL_RING.bin').write_bytes(data);info['initial_header']=[pending,head,tail]
                events=[]
                for slot in range(CAP):
                    raw=data[12+slot*SIZE:12+(slot+1)*SIZE]
                    if not raw[0] or not raw[1] or old.get(slot)==raw:continue
                    old[slot]=raw;row=decode(raw);row.update(slot=slot,observed_unix=time.time(),recovered_at_attach=loops==0);events.append(row)
                for row in sorted(events,key=lambda r:(r['frame'],(r['slot']-tail)%CAP)):
                    f.write(json.dumps(row,ensure_ascii=False)+'\n');count+=1;types[row['name']]+=1
                    first=row['frame'] if first is None else min(first,row['frame']);last=max(last or 0,row['frame'])
                f.flush();loops+=1
                if loops%5==1:
                    info.update(records=count,first_frame=first,last_frame=last,event_types=dict(types),polls=loops,wraps_observed=wraps,max_poll_interval_seconds=round(max_poll,3),latest_header=[pending,head,tail],updated_unix=time.time(),errors=errors)
                    (dest/'STATUS.json').write_text(json.dumps(info,indent=2,ensure_ascii=False),encoding='utf-8')
                time.sleep(.2)
    except (OSError,psutil.Error) as exc:errors.append(repr(exc))
    finally:
        reader.close();info.update(status='STOPPED',records=count,first_frame=first,last_frame=last,event_types=dict(types),errors=errors)
        (dest/'STATUS.json').write_text(json.dumps(info,indent=2,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__':collect(Path(sys.argv[1]))
