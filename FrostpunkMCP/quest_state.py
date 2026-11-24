"""Live quest state for the verified Frostpunk build, without scenario guesses.

Layout evidence is the game's reflected properties (157FB40, 157FEE0),
quest panel (1A50BB0/1A4F5F0), and progress getter (156F080).
Only localization calls enter the game thread; no quest mutations are exposed.
"""
import struct

from city_state import string, type_name
from research_state import ResearchReadError

QUEST_SYSTEM_RVA = 0x2B8ED40
LOCALIZATION_RVA = 0x2985988
ENTRY_SIZE = 0x90
OBJECTIVE_SIZE = 0x20
NO_TIME = 0x7FFFFFFFFFFFFFFF
RESULTS = {-1: 'in_progress', 0: 'success', 1: 'failure'}


def wide_string(reader, address, max_units=8192):
    if not address:
        return ''
    if address % 2:
        raise ResearchReadError('unaligned localized text')
    data = bytearray()
    while len(data) < max_units * 2:
        current = address + len(data)
        chunk = reader.read(current, min(256, 4096-current % 4096, max_units*2-len(data)))
        for i in range(0, len(chunk), 2):
            if chunk[i:i+2] == b'\0\0':
                data.extend(chunk[:i])
                return data.decode('utf-16-le')
        data.extend(chunk)
    raise ResearchReadError('localized task text exceeds limit')


def localized(controller, pointer, cache):
    if not pointer:
        return {'text': '', 'localization_key': None, 'localized': True}
    r = controller.reader
    key = string(r, pointer, 4096)
    if key in cache:
        return cache[key]
    if not key.startswith('@'):
        result = {'text': key, 'localization_key': None, 'localized': True}
    else:
        owner = r.pointer(r.base + LOCALIZATION_RVA)
        if not owner:
            raise ResearchReadError('localization system is unavailable')
        # Exact lookup used by UI localized strings (1C31250): strip '@',
        # use current language, fallback enabled, return a borrowed string reference.
        ref = controller.caller.invoke(0xE84CD0,
            (owner, pointer+1, r.base+0x2982530, 0, 1, 1))
        text = wide_string(r, r.pointer(ref))
        result = {'text': text or None, 'localization_key': key[1:], 'localized': bool(text)}
    cache[key] = result
    return result


def _vector(reader, address, stride, maximum):
    header = reader.read(address, 12)
    pointer, count = struct.unpack('<QI', header)
    if count > maximum or (count and not pointer):
        raise ResearchReadError('invalid quest collection layout')
    raw = reader.read(pointer, count*stride) if count else b''
    return header, pointer, count, raw


def _snapshot(controller, include_completed, include_hidden):
    r = controller.reader
    owner = r.pointer(r.base + QUEST_SYSTEM_RVA)
    if not owner or type_name(r, owner) != '.?AVFrostpunkQuestSystem@@':
        raise ResearchReadError('quest system is not initialized')
    header, table, count, raw = _vector(r, owner+0x1C0, ENTRY_SIZE, 4096)
    watched = [(owner+0x1C0, header)]
    if raw:
        watched.append((table, raw))
    tasks, seen, texts = [], set(), {}
    hidden_count = completed_count = 0
    for index in range(count):
        entry = raw[index*ENTRY_SIZE:(index+1)*ENTRY_SIZE]
        identifier, = struct.unpack_from('<I', entry)
        definition, = struct.unpack_from('<Q', entry, 0x10)
        result, = struct.unpack_from('<i', entry, 0x18)
        if identifier in seen or not definition or result not in RESULTS:
            raise ResearchReadError('unexpected quest identity or result enum')
        seen.add(identifier)
        config = r.read(definition, 0xE0)
        kind, = struct.unpack_from('<i', config, 0xD8)
        displayable = kind != 9  # UI explicitly rejects kind 9 as non-displayable.
        hidden_count += not displayable
        completed_count += result != -1
        if (not include_completed and result != -1) or (not include_hidden and not displayable):
            continue
        watched.append((definition, config))
        objective_header, objective_ptr, objective_count, objective_raw = _vector(
            r, table+index*ENTRY_SIZE+0x78, OBJECTIVE_SIZE, 512)
        config_header, config_ptr, config_count, definitions_raw = _vector(r, definition+0x30, 8, 512)
        if config_count != objective_count:
            raise ResearchReadError('quest objective state/definition count differs; retry')
        if objective_raw:
            watched.append((objective_ptr, objective_raw))
        if definitions_raw:
            watched.append((config_ptr, definitions_raw))
        objective_defs = {}
        for obj in struct.unpack(f'<{config_count}Q', definitions_raw) if config_count else []:
            typename = type_name(r, obj)
            if not typename.startswith('.?AVFrostpunk') or 'QuestObjective' not in typename:
                raise ResearchReadError('unexpected quest objective type')
            obj_config = r.read(obj, 0x70)
            watched.append((obj, obj_config))
            guid = obj_config[0x60:0x70]
            if guid in objective_defs:
                raise ResearchReadError('duplicate objective GUID')
            objective_defs[guid] = (obj_config, typename)
        objectives = []
        for n in range(objective_count):
            guid, start, progress, maximum, active, mandatory = struct.unpack_from(
                '<16siiiBB2x', objective_raw, n*OBJECTIVE_SIZE)
            if guid not in objective_defs or active not in (0, 1) or mandatory not in (0, 1):
                raise ResearchReadError('quest objective GUID or flags mismatch')
            obj_config, typename = objective_defs[guid]
            label = localized(controller, struct.unpack_from('<Q', obj_config, 0x50)[0], texts)
            objectives.append({'id': guid.hex(), 'index': n, **label,
                'active': bool(active), 'mandatory': bool(mandatory),
                'starting_progress': start, 'progress': progress, 'target': maximum,
                'complete': progress >= maximum,
                'status': 'complete' if progress >= maximum else ('active' if active else 'inactive'),
                'hide_progress': bool(obj_config[0x31]),
                'display_remaining_progress': bool(obj_config[0x32]),
                'objective_type': typename.removeprefix('.?AV').removesuffix('@@')})
        start, expires, finished = struct.unpack_from('<qqq', entry, 0x60)
        name = string(r, struct.unpack_from('<Q', config)[0], 4096)
        label = localized(controller, struct.unpack_from('<Q', config, 0xA0)[0], texts)
        tasks.append({'id': identifier, 'definition_id': config[8:24].hex(),
            'internal_name': name, **label, 'status': RESULTS[result], 'result_code': result,
            'displayable': displayable, 'quest_type_code': kind,
            'start_time_raw': None if start == NO_TIME else str(start),
            'expires_at_raw': None if expires == NO_TIME else str(expires),
            'finished_at_raw': None if finished == NO_TIME else str(finished),
            'hide_timeout_timer': bool(config[0xBC]), 'objectives': objectives})
    timer = r.pointer(r.base+0x2B8F800)
    now, = struct.unpack('<q', r.read(timer+0xB0, 8))
    for item in tasks:
        expiration = item['expires_at_raw']
        item['remaining_time_raw'] = (str(max(0, int(expiration)-now))
            if expiration is not None and item['status'] == 'in_progress' else None)
    if r.pointer(r.base+QUEST_SYSTEM_RVA) != owner or any(r.read(p, len(b)) != b for p,b in watched):
        raise ResearchReadError('quest state changed while reading; retry')
    return {'ready': True, 'source': 'native_quest_system', 'tasks': tasks,
        'count': len(tasks), 'runtime_quest_count': count, 'internal_quest_count': hidden_count,
        'finished_quest_count': completed_count, 'clock_raw': str(now),
        'time_unit': 'engine_ticks', 'include_completed': include_completed,
        'include_hidden': include_hidden}


def get_tasks(controller, include_completed=False, include_hidden=False):
    # Retry a changing snapshot rather than returning mixed objective generations.
    for attempt in range(3):
        try:
            return _snapshot(controller, include_completed, include_hidden)
        except ResearchReadError as exc:
            if 'retry' not in str(exc) or attempt == 2:
                raise
