"""Validated read-only city entity and component snapshots for the supported build."""
import struct
from research_state import ResearchReadError

PLACE_ACTIVITY_SYSTEM_RVA = 0x3FC20B8


def string(reader, address, limit=192):
    result = bytearray()
    while len(result) < limit:
        current = address + len(result)
        chunk = reader.read(current, min(limit-len(result), 4096-current % 4096))
        value, end, _ = chunk.partition(b'\0')
        result.extend(value)
        if end:
            return result.decode('utf-8')
    raise ResearchReadError('unterminated engine string')


def type_name(reader, obj):
    table = reader.pointer(obj)
    locator = reader.pointer(table-8)
    descriptor, = struct.unpack('<I', reader.read(locator+12, 4))
    return string(reader, reader.base+descriptor+16)


def component(reader, entity, slot, expected):
    obj = reader.pointer(entity+0x2a0+8*slot)
    if obj and type_name(reader, obj) != f'.?AVFrostpunk{expected}Component@@':
        raise ResearchReadError(f'{expected} component index changed')
    return obj


def entity_table(reader):
    system = reader.pointer(reader.base+PLACE_ACTIVITY_SYSTEM_RVA)
    if not system or type_name(reader, system) != '.?AVFrostpunkPlaceActivitySystem@@':
        raise ResearchReadError('city activity system is unavailable')
    table, count = struct.unpack('<QI', reader.read(system+0x2060, 12))
    if not table or not 0 < count <= 8192:
        raise ResearchReadError('invalid city entity table')
    return system, table, count


def entity_rows(reader):
    system, table, count = entity_table(reader)
    raw = reader.read(table, count*8)
    rows = []
    for entity in struct.unpack(f'<{count}Q', raw):
        if type_name(reader, entity) != '.?AVFrostpunkEntity@@':
            raise ResearchReadError('city entity type changed')
        rows.append((entity, reader.read(entity+0x18, 16).hex(),
                     string(reader, reader.pointer(entity+0x48))))
    if (system, table, count) != entity_table(reader) or raw != reader.read(table, len(raw)):
        raise ResearchReadError('city entity table changed; retry')
    return rows


def city_state(reader):
    result = []
    for entity, entity_id, name in entity_rows(reader):
        item = {'id': entity_id, 'name': name}
        activity = component(reader, entity, 6, 'PlaceActivity')
        if activity:
            item['activity_state_code'], = struct.unpack('<I', reader.read(activity+0x30, 4))
            template = reader.pointer(activity+0x20)
            item['can_toggle'] = bool(reader.read(template+0x40,1)[0])
            item['enabled'] = not bool(reader.read(activity+0x3c,1)[0])
        item['building'] = bool(component(reader, entity, 43, 'Building'))
        manpower = component(reader, entity, 51, 'Manpower')
        if manpower:
            # Names/offsets are from the engine's own reflected component properties.
            active, = struct.unpack('<I', reader.read(manpower+0x40, 4))
            working, = struct.unpack('<I', reader.read(manpower+0x58, 4))
            if active > 10000 or working > 10000:
                raise ResearchReadError('invalid manpower collection size')
            item.update(active_agents=active, working_agents=working)
        generator = component(reader, entity, 111, 'Generator')
        if generator:
            item['generator'] = {'requested_steam': struct.unpack('<I', reader.read(generator+0x30,4))[0]}
        result.append(item)
    return result
