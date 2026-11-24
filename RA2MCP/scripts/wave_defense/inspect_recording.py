"""Bounded, read-only verification of an ongoing lesson's written data."""
from pathlib import Path
import collections, ctypes, datetime, json, os, struct, sys, time, zlib
import ctypes.wintypes as wt
ROOT=Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'))
from ra2yrproto.ra2yr_pb2 import GameState

def chunks(path):
    with path.open('rb') as f:
        size=f.seek(0,2);f.seek(0);remaining=size;d=zlib.decompressobj(31)
        while remaining:
            raw=f.read(min(65536,remaining))
            if not raw:break
            remaining-=len(raw);yield d.decompress(raw)

def varint(buf,pos):
    n=0
    for shift in range(0,70,7):
        if pos>=len(buf):raise EOFError
        b=buf[pos];pos+=1;n|=(b&127)<<shift
        if not b&128:return n,pos
    raise ValueError('oversize varint')

def native(path):
    buf=b'';count=0;first=None;last=None;gaps=[];duplicates=0;examples=[];started=time.monotonic()
    field=GameState.DESCRIPTOR.fields_by_name['current_frame'].number
    for chunk in chunks(path):
        buf+=chunk;pos=0
        while pos<len(buf):
            try:length,begin=varint(buf,pos)
            except EOFError:break
            end=begin+length
            if end>len(buf):break
            p=begin;frame=0
            while p<end:
                tag,p=varint(buf,p);wire=tag&7
                if wire==0:
                    value,p=varint(buf,p)
                    if tag>>3==field:frame=value;break
                elif wire==2:
                    size,p=varint(buf,p);p+=size
                elif wire==5:p+=4
                elif wire==1:p+=8
                else:raise ValueError(('wire type',wire))
            if last is not None:
                if frame>last+1:gaps.append([last+1,frame-1])
                if frame<=last:duplicates+=1
            if first is None:first=frame
            last=frame;count+=1
            if count==1 or count%25000==0:
                state=GameState.FromString(buf[begin:end]);assert int(state.current_frame)==frame
                examples.append(dict(frame=frame,objects=len(state.objects),houses=len(state.houses),fields=[f.name for f,_ in state.ListFields()]))
            pos=end
        buf=buf[pos:]
    return dict(records=count,first_frame=first,last_frame=last,gaps=gaps,duplicate_or_backward_frames=duplicates,protobuf_examples=examples,unfinished_tail_bytes=len(buf),seconds=round(time.monotonic()-started,2))

def jsonlines(path):
    buf=b'';count=0;first=None;last=None;gaps=[];nonempty=0;eventtypes=collections.Counter();last_record=None
    for chunk in chunks(path):
        lines=(buf+chunk).split(b'\n');buf=lines.pop()
        for line in lines:
            if not line:continue
            row=json.loads(line);frame=row['frame'];count+=1
            if first is None:first=frame
            if last is not None and frame>last+1 and path.name.startswith('EVENTS'):gaps.append([last+1,frame-1])
            last=frame;last_record=row
            events=row.get('events',{})
            if events:
                nonempty+=1
                for key,value in events.items():eventtypes[key]+=len(value) if isinstance(value,list) else 1
    return dict(records=count,first_frame=first,last_frame=last,gaps=gaps,nonempty_event_frames=nonempty,event_fields=dict(eventtypes),unfinished_tail_bytes=len(buf),last_object_count=len((last_record or {}).get('objects',[])))

def ui_snapshot(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];k.OpenProcess.restype=wt.HANDLE
    k.ReadProcessMemory.argtypes=[wt.HANDLE,wt.LPCVOID,wt.LPVOID,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
    k.CloseHandle.argtypes=[wt.HANDLE];h=k.OpenProcess(0x1010,False,pid)
    def read(addr,n):
        buf=ctypes.create_string_buffer(n);got=ctypes.c_size_t()
        if not h or not k.ReadProcessMemory(h,addr,buf,n,ctypes.byref(got)):raise OSError(ctypes.get_last_error())
        return buf.raw
    def u32(addr):return struct.unpack('<I',read(addr,4))[0]
    try:
        labels=[];ptr=u32(0xA8BC60);visited=set()
        for _ in range(14):
            if not ptr or ptr in visited:break
            visited.add(ptr);raw=read(ptr,0x4c)
            words=struct.unpack('<19I',raw);textptr=words[12]
            text=read(textptr,324).decode('utf-16le',errors='replace').split('\0')[0] if textptr else ''
            labels.append(dict(address=hex(ptr),style=hex(words[11]),color_scheme=words[13],text=text,x=words[3],y=words[4],height=words[6]))
            ptr=words[1]
        font=u32(0x89C4D0);fontdata=u32(font+4) if font else 0
        values=struct.unpack('<6I',read(fontdata,24)) if fontdata else None
        return dict(labels=labels,message_height=u32(0xA8BC60+20),bitmap_font=dict(zip(['width','stride','height','lines','count','symbol_data_size'],values)) if values else None)
    finally:
        if h:k.CloseHandle(h)

def main():
    import psutil
    run=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
    owner=json.loads((run/'OWNED_GAME.json').read_text());proc=psutil.Process(owner['pid'])
    if Path(proc.exe()).resolve()!=Path(owner['path']).resolve():raise RuntimeError('Owned game identity mismatch')
    result=dict(status='VERIFIED_SNAPSHOT',checked_at=datetime.datetime.now().astimezone().isoformat(),run=str(run),game=dict(pid=proc.pid,exe=proc.exe(),create_time=proc.create_time()),files={},live_tail_note='Active gzip streams have no final footer; only complete currently written records are checked.')
    try:result['native_ui']=ui_snapshot(proc.pid)
    except Exception as exc:result['native_ui_error']=repr(exc)
    for name in ['NATIVE_STATES.pb.gz','OBSERVATIONS.jsonl.gz','EVENTS.jsonl.gz','NATIVE_TRAFFIC.pb.gz']:
        path=run/name
        with path.open('rb') as f:size=f.seek(0,2)
        result['files'][name]=dict(bytes_at_check=size)
        if size:
            result['files'][name].update(native(path) if name.startswith('NATIVE_STATES') else jsonlines(path) if name in ['OBSERVATIONS.jsonl.gz','EVENTS.jsonl.gz'] else {})
        print(name,json.dumps(result['files'][name],ensure_ascii=False),flush=True)
    result['progress']=json.loads((run/'PROGRESS.json').read_text())
    (out/'RECORDING_CHECK.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('native_ui',json.dumps(result.get('native_ui'),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
