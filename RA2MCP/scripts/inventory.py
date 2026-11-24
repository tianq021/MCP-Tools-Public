import hashlib,json,pathlib,struct,datetime
root=pathlib.Path(r'E:\hj')
rows=[]
for p in root.rglob('*'):
 if p.is_file() and p.suffix.lower() in ('.exe','.dll','.ini'):
  b=p.read_bytes(); machine=None
  if b[:2]==b'MZ':
   off=struct.unpack_from('<I',b,60)[0]
   if b[off:off+4]==b'PE\0\0': machine=hex(struct.unpack_from('<H',b,off+4)[0])
  rows.append(dict(path=str(p),size=len(b),sha256=hashlib.sha256(b).hexdigest(),machine=machine))
out=pathlib.Path(__file__).resolve().parents[1]/'reports'/'inventory.json'
out.write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':rows},indent=2),encoding='utf-8')
print(f'Inventoried {len(rows)} files: {out}')
