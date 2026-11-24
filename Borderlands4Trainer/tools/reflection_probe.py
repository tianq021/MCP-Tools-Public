"""Read-only research, pinned to the recorded executable hash and verified signatures.

Layout/signature research references: bl-sdk/unrealsdk game/bl4/{offsets.h,
gobjects.cpp,fname.cpp} and unreal/{offsets_generic.h,structs/gnames.h}.
This is an experimental inspector, not a writable runtime backend.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from process_reader import Reader, modules, game_pids


from reflection import Reflection


def main():
    config = json.loads((ROOT / 'trainer_config.json').read_text())
    baseline = json.loads((ROOT / 'research/local_baseline.json').read_text(encoding='utf-8-sig'))
    expected = config['expected_executable']
    digest = hashlib.sha256()
    with open(expected, 'rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            digest.update(block)
    if digest.hexdigest().upper() != baseline['executables'][1]['sha256']:
        raise ValueError('Executable changed; research offsets invalid')
    pids = game_pids()
    if len(pids) != 1:
        raise ValueError('Expected exactly one game process')
    signatures = json.loads((ROOT / 'research/reflection_signatures.local.json').read_text())
    if len(signatures['objects']) != 1 or len(signatures['names_nonpgo']) != 1:
        raise ValueError('Static signatures must be unique')
    with Reader(pids[0], expected) as reader:
        target = [m for m in modules(reader.pid) if m['path'].casefold() == expected.casefold()]
        if len(target) != 1:
            raise ValueError('Main module not uniquely identified')
        r = Reflection(reader, target[0]['base'], signatures)
        print('Validated globals; reading object names', flush=True)
        result, failures = [], 0
        for index, address in r.objects_iter():
            try:
                header = reader.read(address, 40)
                if struct.unpack_from('<i', header, 12)[0] != index:
                    continue
                name = r.name(struct.unpack_from('<I', header, 24)[0])
                if any(word in name.lower() for word in ('playercontroller', 'inventory', 'experience', 'skilltree', 'currency', 'backpack')):
                    cls = struct.unpack_from('<Q', header, 16)[0]
                    item = dict(address=address, index=index, name=name, class_name=r.objname(cls), class_address=cls,
                                outer=struct.unpack_from('<Q',header,32)[0], flags=struct.unpack_from('<I',header,8)[0])
                    if 'playercontroller' in name.lower() or ('Default__' not in name and 'Component' in name):
                        item['properties'] = list(r.props(cls))
                    result.append(item)
            except (OSError, ValueError, UnicodeError):
                failures += 1
        output = ROOT / 'research/reflection_objects.local.json'
        output.write_text(json.dumps(dict(process=reader.identity(), objects=result, failures=failures), ensure_ascii=False, indent=2),encoding='utf-8')
        print(json.dumps(dict(matches=len(result),failed_reads=failures,output=str(output))))


if __name__ == '__main__':
    main()
