"""Read-only inspection of this game's IL2CPP v31 metadata (no game launch).

Layout reference:
https://github.com/Perfare/Il2CppDumper/blob/master/Il2CppDumper/Il2Cpp/MetadataClass.cs
This deliberately does not reconstruct native code or resolve runtime objects.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


def inspect(root):
    data = root / 'After Inc. Revival_Data'
    metadata = data / 'il2cpp_data/Metadata/global-metadata.dat'
    blob = metadata.read_bytes()
    magic, version = struct.unpack_from('<II', blob)
    if (magic, version) != (0xFAB11BAF, 31):
        raise ValueError('Only the inspected metadata v31 layout is supported')

    def table(index, stride=1):
        offset, size = struct.unpack_from('<II', blob, 8 + index * 8)
        if offset + size > len(blob) or size % stride:
            raise ValueError(f'Invalid table {index}')
        return memoryview(blob)[offset:offset + size]

    strings = bytes(table(2))

    def string(index):
        if not 0 <= index < len(strings):
            raise ValueError(f'Invalid string index {index}')
        end = strings.index(b'\0', index)
        return strings[index:end].decode('utf-8')

    types = list(struct.iter_unpack('<16i8H2I', table(19, 88)))
    methods = list(struct.iter_unpack('<7i4H', table(5, 36)))
    fields = list(struct.iter_unpack('<3i', table(11, 12)))
    parameters = list(struct.iter_unpack('<3i', table(10, 12)))
    images = list(struct.iter_unpack('<10i', table(20, 40)))
    image_by_type = {}
    for row in images:
        name = string(row[0])
        for i in range(row[2], row[2] + row[3]):
            image_by_type[i] = name

    selected = []
    wanted = re.compile(
        r'^(GameController|World|Simulator|GlobalSimulation|LocalSimulation|'
        r'WorldSettlementSimulator|Zone|ZoneManager|ZoneBuildingManager|'
        r'BaseProduction|ProductionSystem|TechManager|TechBuildActionManager|'
        r'.*ZoneAction|.*DecisionOption|.*Lua.*|.*Claim.*|.*Intel.*|'
        r'.*ActionManager|.*BuildAction|.*SoldierAction|.*GameSpeed.*|'
        r'.*Proposal.*|.*ContextAction.*|.*Decision.*|.*ZoneActions.*)$')
    validated = 0
    for i, row in enumerate(types):
        name, namespace = string(row[0]), string(row[1])
        field_start, method_start = row[8:10]
        method_count, _, field_count = row[16:19]
        if method_count and not 0 <= method_start <= len(methods) - method_count:
            raise ValueError('Invalid method range')
        if field_count and not 0 <= field_start <= len(fields) - field_count:
            raise ValueError('Invalid field range')
        for method in methods[method_start:method_start + method_count]:
            if method[1] != i:
                raise ValueError('Method owner does not match type table')
            string(method[0])
            validated += 1
        if not namespace.startswith('com.ndemic') or not wanted.fullmatch(name):
            continue
        members = []
        for method in methods[method_start:method_start + method_count]:
            count, start = method[10], method[4]
            if count and not 0 <= start <= len(parameters) - count:
                raise ValueError('Invalid parameter range')
            members.append({
                'name': string(method[0]),
                'parameters': [{'name': string(p[0]), 'type_index': p[2]}
                               for p in parameters[start:start + count]],
                'return_type_index': method[2],
                'static': bool(method[7] & 0x10),
            })
        selected.append({
            'type': namespace + '.' + name,
            'assembly': image_by_type.get(i),
            'methods': members,
            'fields': [{'name': string(f[0]), 'type_index': f[1]}
                       for f in fields[field_start:field_start + field_count]],
        })

    assembly = (root / 'GameAssembly.dll').read_bytes()
    pe = struct.unpack_from('<I', assembly, 0x3c)[0]
    machine, sections = struct.unpack_from('<HH', assembly, pe + 4)
    opt_size = struct.unpack_from('<H', assembly, pe + 20)[0]
    optional = pe + 24
    if struct.unpack_from('<H', assembly, optional)[0] != 0x20B:
        raise ValueError('Expected PE32+ binary')
    section_start = optional + opt_size
    mappings = []
    for i in range(sections):
        virtual_size, rva, raw_size, raw = struct.unpack_from(
            '<4I', assembly, section_start + i * 40 + 8)
        mappings.append((rva, max(virtual_size, raw_size), raw))

    def file_offset(rva):
        for base, size, raw in mappings:
            if base <= rva < base + size:
                return raw + rva - base
        raise ValueError('RVA outside PE sections')

    export_rva = struct.unpack_from('<I', assembly, optional + 112)[0]
    export_offset = file_offset(export_rva)
    name_count, _, name_rva = struct.unpack_from('<3I', assembly, export_offset + 24)
    names_offset = file_offset(name_rva)
    exports = []
    for i in range(name_count):
        rva = struct.unpack_from('<I', assembly, names_offset + i * 4)[0]
        start = file_offset(rva)
        name = assembly[start:assembly.index(b'\0', start)].decode('ascii')
        if name.startswith('il2cpp_'):
            exports.append(name)

    header = (data / 'globalgamemanagers').read_bytes()[:4096]
    versions = [m.decode() for m in re.findall(rb'\d{4}\.\d+\.\d+[abfp]\d+', header)]
    return {
        'game_root': str(root), 'unity_versions': versions,
        'machine': hex(machine), 'metadata_version': version,
        'metadata_sha256': hashlib.sha256(blob).hexdigest(),
        'metadata_size': len(blob),
        'type_count': len(types), 'method_count': len(methods),
        'validated_method_owners': validated,
        'il2cpp_exports': exports,
        'selected_types': selected,
        'limitations': ['Static names and metadata only; no native method bodies inspected.',
                        'Type indexes are not resolved into full parameter types.',
                        'Runtime loading, object access and action semantics are untested.'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game_root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.game_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ('selected_types', 'il2cpp_exports')}, indent=2))
    print('Selected types:', len(result['selected_types']))
    print('Verified IL2CPP exports:', len(result['il2cpp_exports']))
