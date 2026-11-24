"""Freeze an ongoing native gzip stream at the observer's explicit cutoff.

Copies only complete length-delimited protobuf records and creates a new gzip
footer. The live game and original recording remain untouched.
"""
from pathlib import Path
import datetime,gzip,hashlib,json,shutil,sys,time,zlib
from inspect_recording import chunks,varint

def save(run,out):
    out.mkdir(parents=True,exist_ok=False)
    result=json.loads((run/'RESULT.json').read_text());cutoff=result['last_frame']
    manifest=dict(status='SAVING',source=str(run),cutoff_frame=cutoff,created=datetime.datetime.now().astimezone().isoformat(),files=[])
    for source in run.iterdir():
        if source.is_file() and source.name not in {'NATIVE_STATES.pb.gz','NATIVE_TRAFFIC.pb.gz','PROGRESS.json','MESSAGE_OVERLAY_STATUS.json'}:
            shutil.copy2(source,out/source.name)
    actions=run/'EXECUTED_NATIVE_EVENTS'
    assert json.loads((actions/'STATUS.json').read_text())['status']=='STOPPED'
    shutil.copytree(actions,out/actions.name)
    (out/'SNAPSHOT_STATUS.json').write_text(json.dumps(manifest,indent=2))
    buf=b'';count=0;first=None;last=None;gaps=[];finished=False;started=time.monotonic()
    with gzip.open(out/'NATIVE_STATES.pb.gz','wb',compresslevel=1) as writer:
        for chunk in chunks(run/'NATIVE_STATES.pb.gz'):
            buf+=chunk;pos=0
            while pos<len(buf):
                try:length,begin=varint(buf,pos)
                except EOFError:break
                end=begin+length
                if end>len(buf):break
                tag,p=varint(buf,begin)
                frame=varint(buf,p)[0] if tag==8 else 0
                if frame>cutoff:finished=True;break
                if last is not None and frame!=last+1:gaps.append([last,frame])
                if first is None:first=frame
                count+=1;last=frame;writer.write(buf[pos:end]);pos=end
                if count%25000==0:print(json.dumps(dict(saved_records=count,frame=frame,seconds=round(time.monotonic()-started,1))),flush=True)
            if finished:break
            buf=buf[pos:]
    assert first==0 and last==cutoff and not gaps,(first,last,cutoff,gaps)
    # Read through the closed gzip footer (CRC and length verification).
    decompressed=0
    with gzip.open(out/'NATIVE_STATES.pb.gz','rb') as f:
        while block:=f.read(4*1024*1024):decompressed+=len(block)
    for p in out.rglob('*'):
        if not p.is_file() or p.name=='SNAPSHOT_STATUS.json':continue
        h=hashlib.sha256()
        with p.open('rb') as f:
            while block:=f.read(4*1024*1024):h.update(block)
        manifest['files'].append(dict(path=str(p.relative_to(out)),bytes=p.stat().st_size,sha256=h.hexdigest()))
    manifest.update(status='VERIFIED',native_records=count,first_frame=first,last_frame=last,frame_gaps=gaps,gzip_crc_verified=True,uncompressed_native_bytes=decompressed,elapsed_seconds=round(time.monotonic()-started,1),traffic='No tunnel traffic was emitted by this local skirmish; empty file not treated as human input history.')
    (out/'SNAPSHOT_STATUS.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:v for k,v in manifest.items() if k!='files'},ensure_ascii=False),flush=True)

if __name__=='__main__':save(Path(sys.argv[1]),Path(sys.argv[2]))
