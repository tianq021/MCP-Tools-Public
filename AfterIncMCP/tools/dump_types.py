"""Read-only IL2CPP v31 metadata dumper for selected type-name patterns.

Prints every matching type's fields and methods with resolved type names.
No game launch, no native code, no writes to the game directory.
"""
import argparse
import json
import re
import struct
from pathlib import Path


def load_tables(root):
    data = root / 'After Inc. Revival_Data'
    metadata = data / 'il2cpp_data/Metadata/global-metadata.dat'
    blob = metadata.read_bytes()
    magic, version = struct.unpack_from('<II', blob)
    if (magic, version) != (0xFAB11BAF, 31):
        raise ValueError('Only metadata v31 layout is supported')

    def table(index, stride=1):
        offset, size = struct.unpack_from('<II', blob, 8 + index * 8)
        if offset + size > len(blob) or size % stride:
            raise ValueError(f'Invalid table {index}')
        return memoryview(blob)[offset:offset + size]

    strings = bytes(table(2))

    def string(index):
        end = strings.index(b'\0', index)
        return strings[index:end].decode('utf-8')

    types = list(struct.iter_unpack('<16i8H2I', table(19, 88)))
    methods = list(struct.iter_unpack('<7i4H', table(5, 36)))
    fields = list(struct.iter_unpack('<3i', table(11, 12)))
    parameters = list(struct.iter_unpack('<3i', table(10, 12)))
    images = list(struct.iter_unpack('<10i', table(20, 40)))
    image_by_type = {}
    for row in images:
        for i in range(row[2], row[2] + row[3]):
            image_by_type[i] = string(row[0])

    names = []
    for i, row in enumerate(types):
        names.append(string(row[1]) + '.' + string(row[0]))
    return types, methods, fields, parameters, image_by_type, names, string


def describe(root, pattern):
    types, methods, fields, parameters, image_by_type, names, string = load_tables(root)
    wanted = re.compile(pattern)
    result = []
    for i, row in enumerate(types):
        full = names[i]
        if not wanted.search(full):
            continue
        field_start, method_start = row[8:10]
        method_count, _, field_count = row[16:19]
        members = []
        for method in methods[method_start:method_start + method_count]:
            count, start = method[10], method[4]
            params = []
            for p in parameters[start:start + count]:
                t = p[2]
                params.append({'name': string(p[0]),
                               'type': names[t] if 0 <= t < len(names) else str(t)})
            ret = method[2]
            members.append({
                'name': string(method[0]),
                'returns': names[ret] if 0 <= ret < len(names) else str(ret),
                'static': bool(method[7] & 0x10),
                'params': params,
            })
        result.append({
            'type': full,
            'assembly': image_by_type.get(i),
            'fields': [{'name': string(f[0]),
                        'type': names[f[1]] if 0 <= f[1] < len(names) else str(f[1])}
                       for f in fields[field_start:field_start + field_count]],
            'methods': members,
        })
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game_root', type=Path)
    parser.add_argument('pattern', help='regex matched against namespace.TypeName')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = describe(args.game_root, args.pattern)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    for t in data:
        print(t['type'], '| fields:', len(t['fields']), '| methods:', len(t['methods']))
    print('total types:', len(data))
