"""Read-only inspection of loaded loot pool metadata."""
import sys
import struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import memory_scan as m
from bl3_reflection import Reflection

with m.ProcessMemory(m.find_process('Borderlands3.exe')) as p:
    r=Reflection(p)
    for obj in r.objects_iter():
        try:
            name=r.objname(obj)
            if name in ('DataTable_ItemRarity','ItemPool_Guns_All','ItemPoolData','ItemPoolList_StandardEnemyGunsandGear'):
                cls=p.read_u64(obj+16)
                print('OBJECT',hex(obj),name,'CLASS',r.objname(cls),'OUTER',r.objname(p.read_u64(obj+32)),flush=True)
                if name=='ItemPool_Guns_All':
                    data=p.read_u64(obj+0x48)
                    for index in range(p.read_i32(obj+0x50)):
                        entry=data+index*112
                        pool=p.read_u64(entry)
                        attr=p.read_u64(entry+56+32)
                        print('ENTRY', index, r.objname(pool),'WEIGHT',p.read(entry+56,56).hex(),'ATTR',r.objname(attr),flush=True)
                        if attr:
                            print('ATTRPROPS',list(r.props(p.read_u64(attr+16))),flush=True)
                    break
                for n,o,k,a in r.props(cls):
                    print('  PROP',n,hex(o),k,'DATA',p.read_partial(obj+o,48).hex(),flush=True)
                    if k=='ArrayProperty':
                        inner=p.read_u64(a+0x70)
                        print('  INNER',hex(inner),'SIZE',p.read_i32(inner+0x34),'KIND',r.objname(p.read_u64(inner+16)),flush=True)
                        if r.objname(p.read_u64(inner+16))=='StructProperty':
                            st=p.read_u64(inner+0x70)
                            print('  STRUCT',r.objname(st), list(r.props(st)),flush=True)
                    if k=='StructProperty':
                        st=p.read_u64(a+0x70)
                        print('  STRUCT',r.objname(st),list(r.props(st)),flush=True)
        except m.TrainerError:
            continue
