"""Resize the native Unicode bitmap font, preserving every codepoint mapping.

Format and bit order cross-checked against Phobos-developers/ra2fnt fnt.go and
pngset.go. Only font assets are changed; no DLL or executable patches.
"""
from pathlib import Path
import hashlib,json,math,struct,sys
import numpy as np

def parse(data):
    magic,ideograph,stride,rows,height,count,size=struct.unpack_from('<4s6I',data)
    assert magic==b'fonT' and size==1+stride*rows
    table=np.frombuffer(data[28:0x2001c],dtype='<u2');assert int(table.max())<=count
    blocks=np.frombuffer(data[0x2001c:0x2001c+count*size],dtype=np.uint8).reshape(count,size)
    assert int(blocks[:,0].max())<=stride*8
    return (ideograph,stride,rows,height,count,size),table,blocks

def resize(data,rows=20):
    (ideograph,stride,oldrows,height,count,size),table,blocks=parse(data);ratio=rows/oldrows
    newstride=math.ceil(stride*8*ratio/8);newsize=1+newstride*rows
    out=np.zeros((count,newsize),dtype=np.uint8);out[:,0]=np.ceil(blocks[:,0].astype(float)*ratio).astype(np.uint8)
    bits=np.unpackbits(blocks[:,1:].reshape(count,oldrows,stride),axis=2,bitorder='big')
    x=np.minimum((np.arange(newstride*8)/ratio).astype(int),stride*8-1);y=np.minimum((np.arange(rows)/ratio).astype(int),oldrows-1)
    scaled=bits[:,y,:][:,:,x]
    # Clear padding beyond the scaled source stride; mapped character width is
    # preserved separately, including all zero-width ideographic placeholders.
    scaled[:,:,math.ceil(stride*8*ratio):]=0
    out[:,1:]=np.packbits(scaled,axis=2,bitorder='big').reshape(count,newsize-1)
    result=struct.pack('<4s6I',b'fonT',math.ceil(ideograph*ratio),newstride,rows,rows+(height-oldrows),count,newsize)+data[28:0x2001c]+out.tobytes()+data[0x2001c+count*size:]
    h,t,b=parse(result);assert result[28:0x2001c]==data[28:0x2001c]
    assert np.array_equal(blocks[:,0]==0,b[:,0]==0)
    return result

def preview(data,text):
    from PIL import Image
    (_,stride,rows,height,count,size),table,blocks=parse(data)
    glyphs=[]
    for char in text:
        index=int(table[ord(char)])-1
        if index<0:continue
        block=blocks[index];width=int(block[0]);bits=np.unpackbits(block[1:].reshape(rows,stride),axis=1,bitorder='big')[:,:width]
        glyphs.append(bits)
    canvas=np.full((height,sum(g.shape[1]+1 for g in glyphs),3),255,dtype=np.uint8);x=0
    for g in glyphs:
        region=canvas[:rows,x:x+g.shape[1]];region[g.astype(bool)]=[0,0,0];x+=g.shape[1]+1
    return Image.fromarray(canvas)

def main():
    source=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=False)
    data=source.read_bytes();assert resize(data,16)==data,'identity roundtrip'
    target=resize(data,20);(out/'game-original-16px.fnt').write_bytes(data);(out/'game-20px.fnt').write_bytes(target)
    from PIL import Image
    text='第44波 火箭飞行兵 ×60：还有99游戏秒，请准备！'
    old,new=preview(data,text),preview(target,text);sheet=Image.new('RGB',(max(old.width,new.width)+40,100),'white');sheet.paste(old,(20,12));sheet.paste(new,(20,55));sheet.save(out/'FONT_COMPARISON.png')
    meta=dict(status='BUILT',old_glyph_height=16,new_glyph_height=20,scale=1.25,old_header=parse(data)[0],new_header=parse(target)[0],original_sha256=hashlib.sha256(data).hexdigest(),new_sha256=hashlib.sha256(target).hexdigest(),unicode_map_unchanged=True,identity_roundtrip=True,scope='shared native game.fnt; may also enlarge native tooltips and UI text',requires_game_restart=True)
    (out/'FONT_BUILD.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta))

if __name__=='__main__':main()
