"""Explicit one-shot BL4 adjustments through validated reflected player fields.

Only cash, eridium and backpack capacity are implemented. No code patches.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from process_reader import Reader, game_pids, modules, bind, open_process, close
from reflection import Reflection

CURRENCY_IDS = {'cash': bytes.fromhex('5492b7bde81702000000000000000000'),
                'eridium': bytes.fromhex('cb244fcdd79c02000000000000000000')}


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest().upper()


def backup_saves(source, destination_root=None):
    files = sorted(source.rglob('*'))
    files = [p for p in files if p.is_file()]
    if not any(p.suffix.casefold() == '.sav' for p in files):
        raise RuntimeError('No save files found; refusing adjustment')
    before = {str(p.relative_to(source)): sha(p) for p in files}
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    destination = (destination_root or ROOT) / 'backups' / stamp
    destination.mkdir(parents=True, exist_ok=False)
    for relative in before:
        target = destination / 'SaveGames' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    after_files = [p for p in source.rglob('*') if p.is_file()]
    after = {str(p.relative_to(source)): sha(p) for p in after_files}
    copies = {relative: sha(destination / 'SaveGames' / relative) for relative in before}
    if before != after or before != copies:
        raise RuntimeError(f'Saves changed during backup; no write performed. Inspect {destination}')
    (destination / 'manifest.json').write_text(json.dumps(dict(source=str(source),sha256=before,
        game_running=True,consistency='Files unchanged during copy; not a guaranteed atomic in-game save snapshot'),indent=2),encoding='utf-8')
    return destination


def property_of(r, cls, name, kind):
    found = [p for p in r.props(cls) if p['name'] == name]
    if len(found) != 1 or found[0]['kind'] != kind or not 0 <= found[0]['offset'] < 0x100000:
        raise ValueError(f'Unrecognized property {name}')
    return found[0]


def identity(r, obj):
    index = r.i32(obj + 12)
    if not 0 <= index < r.i32(r.objects + 36):
        raise ValueError('Object index invalid')
    chunks = r.u64(r.objects + 16)
    chunk = r.u64(chunks + index // 65536 * 8)
    raw = r.p.read(chunk + index % 65536 * 24, 24)
    ptr, flags, cluster, serial = struct.unpack_from('<Qiii', raw)
    object_flags = r.i32(obj + 8)
    if ptr != obj or flags & 0x30000000 or object_flags & (0x10 | 0x8000 | 0x10000):
        raise ValueError('Object is default, stale or being destroyed')
    return obj, index, serial, r.u64(obj + 16)


def locate(r, include_metadata=False):
    controllers = []
    class_names, player_properties = {}, {}
    for index, obj, flags, cls in r.object_headers():
        try:
            if flags & (0x10 | 0x8000 | 0x10000):
                continue
            if cls not in class_names:
                class_names[cls] = r.objname(cls)
            if class_names[cls] != 'OakPlayerController':
                continue
            # Page snapshots are only discovery hints, never write authority.
            if identity(r, obj)[3] != cls:
                continue
            if cls not in player_properties:
                player_properties[cls] = property_of(r, cls, 'player', 'ObjectProperty')
            player_prop = player_properties[cls]
            player = r.u64(obj + player_prop['offset'])
            if player and r.objname(r.u64(player + 16)) == 'OakLocalPlayer':
                controllers.append(obj)
        except (OSError, ValueError, UnicodeError):
            continue
    if len(controllers) != 1:
        raise ValueError(f'Expected one local controller; found {len(controllers)}')
    pc = controllers[0]
    cls = r.u64(pc + 16)
    manager_off = property_of(r, cls, 'CurrencyManager', 'ObjectProperty')['offset']
    state_off = property_of(r, cls, 'PlayerState', 'ObjectProperty')['offset']
    manager, state = r.u64(pc + manager_off), r.u64(pc + state_off)
    currencies = property_of(r, r.u64(manager + 16), 'currencies', 'ArrayProperty')
    inner = r.u64(currencies['field'] + 0x78)
    if r.i32(inner + 0x34) != 24:
        raise ValueError('Currency element size changed')
    currency_struct = r.u64(inner + 0x70)
    amount_off = property_of(r, currency_struct, 'Amount', 'UInt64Property')['offset']
    type_off = property_of(r, currency_struct, 'type', 'StructProperty')['offset']
    data, count, capacity = struct.unpack('<Qii', r.p.read(manager + currencies['offset'], 16))
    if not 0 < count <= capacity <= 4096:
        raise ValueError('Invalid currency array')
    fields = []
    for name, type_id in CURRENCY_IDS.items():
        rows = [data + i * 24 for i in range(count) if r.p.read(data + i * 24 + type_off, 16) == type_id]
        if len(rows) != 1:
            raise ValueError(f'Currency {name} not uniquely identified')
        fields.append(dict(name=name,address=rows[0]+amount_off,format='<Q',value=r.u64(rows[0]+amount_off)))
    container = property_of(r, r.u64(state + 16), 'BackpackContainer', 'StructProperty')
    container_struct = r.u64(container['field'] + 0x70)
    maximum = property_of(r, container_struct, 'MaxSize', 'StructProperty')
    attribute_struct = r.u64(maximum['field'] + 0x70)
    value_off = property_of(r, attribute_struct, 'Value', 'IntProperty')['offset']
    address = state + container['offset'] + maximum['offset'] + value_off
    current = r.i32(address)
    if not 0 < current <= 10000:
        raise ValueError('Unexpected backpack capacity')
    fields.append(dict(name='backpack',address=address,format='<i',value=current))
    guards = [identity(r, obj) for obj in (pc,manager,state)]
    def verify():
        r.p.require_alive()
        if guards != [identity(r, obj) for obj in (pc,manager,state)]:
            raise RuntimeError('Player objects changed')
        if r.u64(pc + manager_off) != manager or r.u64(pc + state_off) != state:
            raise RuntimeError('Player links changed')
        if struct.unpack('<Qii',r.p.read(manager+currencies['offset'],16)) != (data,count,capacity):
            raise RuntimeError('Currency array changed')
        for name,type_id in CURRENCY_IDS.items():
            field = next(f for f in fields if f['name']==name)
            if r.p.read(field['address']-amount_off+type_off,16)!=type_id:
                raise RuntimeError('Currency identity changed')
    verify()
    if include_metadata:
        return fields, verify, dict(controller=pc, currency_manager=manager, player_state=state)
    return fields, verify


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cash-add',type=int,default=0)
    parser.add_argument('--eridium-add',type=int,default=0)
    parser.add_argument('--backpack-add',type=int,default=0)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--experimental-currency-cache',action='store_true',
                        help='allow known non-authoritative CurrencyManager cache writes for research only')
    args=parser.parse_args()
    increments=dict(cash=args.cash_add,eridium=args.eridium_add,backpack=args.backpack_add)
    if (args.cash_add or args.eridium_add) and not args.experimental_currency_cache:
        raise RuntimeError('Cash/eridium target is a synchronized cache and is not authoritative. '
                           'Use tools/feature_probe.py instead. For reproduction only, pass '
                           '--experimental-currency-cache explicitly.')
    if any(v<0 for v in increments.values()):
        raise ValueError('This first validation tool only supports nonnegative increments')
    config=json.loads((ROOT/'trainer_config.json').read_text())
    expected=config['expected_executable']
    baseline=json.loads((ROOT/'research/local_baseline.json').read_text(encoding='utf-8-sig'))
    if sha(expected)!=baseline['executables'][1]['sha256']:
        raise ValueError('Executable version changed')
    pids=game_pids()
    if len(pids)!=1:raise ValueError('Expected one game process')
    with Reader(pids[0],expected) as reader:
        matches=[m for m in modules(reader.pid) if m['path'].casefold()==expected.casefold()]
        if len(matches)!=1:raise ValueError('Main module ambiguous')
        signatures=json.loads((ROOT/'research/reflection_signatures.local.json').read_text())
        if len(signatures['objects'])!=1 or len(signatures['names_nonpgo'])!=1:
            raise ValueError('Signatures ambiguous')
        r=Reflection(reader,matches[0]['base'],signatures)
        fields,verify=locate(r)
        for field in fields:
            field['target']=field['value']+increments[field['name']]
            maximum=1000 if field['name']=='backpack' else 2000000000
            if increments[field['name']] and not 0<=field['target']<=maximum:
                raise ValueError('Target outside first-version validation limit')
        report=dict(process=reader.identity(),fields=fields,applied=False,
                    observed_at=datetime.now(timezone.utc).isoformat())
        if args.apply:
            if not any(increments.values()):raise ValueError('No changes requested')
            report['backup']=str(backup_saves(Path(config['save_root'])))
            verify()
            handle=open_process(0x100438,False,reader.pid)
            if not handle:raise C.WinError(C.get_last_error())
            write=bind('WriteProcessMemory',W.BOOL,[W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)])
            attempted=[]
            try:
                for field in fields:
                    if not increments[field['name']]:continue
                    verify()
                    old=struct.pack(field['format'],field['value']); new=struct.pack(field['format'],field['target'])
                    if reader.read(field['address'],len(old))!=old:raise RuntimeError('Value changed before write')
                    attempted.append((field,old,new))
                    count=C.c_size_t(); source=C.create_string_buffer(new)
                    if not write(handle,field['address'],source,len(new),C.byref(count)) or count.value!=len(new):
                        raise C.WinError(C.get_last_error())
                    if reader.read(field['address'],len(new))!=new:raise RuntimeError('Readback mismatch')
                verify()
                report['applied']=True
                report['readback']={f['name']:struct.unpack(f['format'],reader.read(f['address'],struct.calcsize(f['format'])))[0] for f in fields}
            except Exception as error:
                report['error']=str(error); report['rollback']=[]
                for field,old,new in reversed(attempted):
                    try:
                        verify()
                        if reader.read(field['address'],len(new))!=new:
                            raise RuntimeError('Changed externally or partial write; not overwriting')
                        count=C.c_size_t(); source=C.create_string_buffer(old)
                        if not write(handle,field['address'],source,len(old),C.byref(count)) or reader.read(field['address'],len(old))!=old:
                            raise RuntimeError('Rollback failed')
                        report['rollback'].append(dict(name=field['name'],restored=True))
                    except Exception as rollback_error:
                        report['rollback'].append(dict(name=field['name'],error=str(rollback_error)))
                raise
            finally:
                close(handle)
                (ROOT/'logs').mkdir(exist_ok=True)
                with (ROOT/'logs/adjustments.jsonl').open('a',encoding='utf-8') as log:
                    log.write(json.dumps(report,ensure_ascii=False)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
