"""Resolve research signatures from the locally pinned BL4 executable, read-only.

References: https://github.com/bl-sdk/unrealsdk/tree/master/src/unrealsdk/game/bl4
Signature bytes are identification data; no SDK code is executed or installed.
"""
import hashlib
import json
import mmap
from pathlib import Path
import re
import struct

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
    'objects':('44 8B 05 ?? ?? ?? ?? 8B 15 ?? ?? ?? ?? 89 F9',3),
    'names_nonpgo':('48 89 CE 80 3D ?? ?? ?? ?? 00 75 ?? 48 8D 0D ?? ?? ?? ?? E8 ?? ?? ?? ?? C6 05 ?? ?? ?? ?? 01 83 FF 01',15),
}


def main():
    config=json.loads((ROOT/'trainer_config.json').read_text())
    baseline=json.loads((ROOT/'research/local_baseline.json').read_text(encoding='utf-8-sig'))
    with open(config['expected_executable'],'rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        if hashlib.sha256(image).hexdigest().upper()!=baseline['executables'][1]['sha256']:
            raise ValueError('Executable differs from verified research baseline')
        pe=struct.unpack_from('<I',image,60)[0]
        if image[pe:pe+4]!=b'PE\0\0':raise ValueError('Invalid PE image')
        count=struct.unpack_from('<H',image,pe+6)[0]
        optional_size=struct.unpack_from('<H',image,pe+20)[0]
        sections=[]
        for i in range(count):
            offset=pe+24+optional_size+40*i
            name=image[offset:offset+8].split(b'\0')[0].decode()
            virtual_size,rva,size,raw=struct.unpack_from('<IIII',image,offset+8)
            flags=struct.unpack_from('<I',image,offset+36)[0]
            if flags&0x20000000:sections.append((name,rva,size,raw))
        result={}
        for name,(pattern,displacement) in PATTERNS.items():
            regex=b''.join(b'.' if token=='??' else re.escape(bytes.fromhex(token)) for token in pattern.split())
            found=[]
            for section,rva,size,raw in sections:
                for match in re.finditer(regex,image[raw:raw+size],re.DOTALL):
                    pos=match.start(); target=rva+pos+displacement+4+struct.unpack_from('<i',image,raw+pos+displacement)[0]
                    found.append(dict(rva=rva+pos,target_rva=target,section=section))
            if len(found)!=1:raise ValueError(f'{name} signature not unique: {len(found)}')
            result[name]=found
        (ROOT/'research/reflection_signatures.local.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
