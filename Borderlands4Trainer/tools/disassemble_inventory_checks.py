"""Read current process code only; bound disassembly by PE unwind entries."""
import sys,json,struct,bisect
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'research/python_deps'),str(ROOT/'src')]
import pefile
from capstone import Cs,CS_ARCH_X86,CS_MODE_64
from process_reader import Reader
snap=json.loads((ROOT/'research/current_inventory_runtime.json').read_text(encoding='utf-8'))
pe=pefile.PE(snap['process']['path'],fast_load=True)
directory=pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]
table=pe.get_data(directory.VirtualAddress,directory.Size)
bounds=[struct.unpack_from('<III',table,i)[:2] for i in range(0,len(table)-11,12)]
starts=[x[0] for x in bounds]
targets={'identity_from_serial':0x88e2a4,'slot_compatibility':0x3db2884,'transaction_validate':0x5e09814}
targets.update(serial_stage_cd3144=0xcd3144,identity_stage_cd37d6=0xcd37d6)
md=Cs(CS_ARCH_X86,CS_MODE_64)
results=[]
with Reader(snap['process']['pid'],snap['process']['path']) as reader:
 assert reader.identity()==snap['process'],'Process restarted'
 for name,rva in targets.items():
  index=bisect.bisect_right(starts,rva)-1;begin,end=bounds[index]
  bounded=begin<=rva<end
  if not bounded:
   begin,end=rva,min(rva+0x500,starts[index+1])
  assert 0<end-begin<65536
  data=reader.read(snap['base']+begin,end-begin)
  lines=[f'{i.address:08x}  {i.mnemonic:8} {i.op_str}' for i in md.disasm(data,begin)]
  results.append(dict(name=name,rva=hex(rva),begin=hex(begin),end=hex(end),unwind_bounded=bounded,lines=lines))
dest=ROOT/'research/inventory_checks_disassembly.json'
dest.write_text(json.dumps(results,indent=2),encoding='utf-8')
for row in results:
 print(row['name'],row['begin'],row['end'])
 if row['name'].startswith(('serial_stage','identity_stage')):
  print('\n'.join(row['lines']))
