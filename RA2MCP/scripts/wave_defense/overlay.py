"""Format80 overlay chunks. Decode all native commands; encode bounded RLE only.
Format reference: OpenRA.Mods.Cnc/FileFormats/LCWCompression.cs (OpenRA).
"""
import base64,struct,itertools

def unpack(section):
    raw=base64.b64decode(''.join(section.values()));pos=0;out=bytearray()
    while pos<len(raw):
        size,want=struct.unpack_from('<HH',raw,pos);pos+=4;b=raw[pos:pos+size];pos+=size;d=bytearray();i=0
        def word():
            nonlocal i
            n=int.from_bytes(b[i:i+2],'little');i+=2;return n
        while i<len(b):
            c=b[i];i+=1
            if c==128:break
            if c<128:
                n=(c>>4)+3;src=len(d)-(((c&15)<<8)+b[i]);i+=1
            elif c<192:
                n=c&63;d+=b[i:i+n];i+=n;continue
            elif c==254:
                n=word();d+=bytes([b[i]])*n;i+=1;continue
            else:n=word() if c==255 else (c&63)+3;src=word()
            for _ in range(n):
                assert 0<=src<len(d);d.append(d[src]);src+=1
        assert len(d)==want;out+=d
    assert len(out)==512*512;return out

def pack(data):
    out=bytearray()
    for offset in range(0,len(data),8192):
        chunk=data[offset:offset+8192];encoded=bytearray()
        for value,values in itertools.groupby(chunk):
            n=sum(1 for _ in values);encoded+=b'\xfe'+struct.pack('<H',n)+bytes([value])
        encoded+=b'\x80';out+=struct.pack('<HH',len(encoded),len(chunk))+encoded
    b=base64.b64encode(out).decode();result={str(i//70+1):b[i:i+70] for i in range(0,len(b),70)}
    assert unpack(result)==data;return result

def cage(map_ini,x,y):
    overlays=unpack(map_ini['OverlayPack']);frames=unpack(map_ini['OverlayDataPack']);changed=[]
    for dx in range(-1,2):
        for dy in range(-1,2):
            if not dx and not dy:continue
            index=(y+dy)*512+x+dx;changed.append([x+dx,y+dy,int(overlays[index]),int(frames[index])]);overlays[index]=3;frames[index]=0
    map_ini['OverlayPack']=pack(overlays);map_ini['OverlayDataPack']=pack(frames);return changed
