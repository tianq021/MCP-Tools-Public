"""Native ability operations through the same functions used by the game UI.

0x17435A0: visibility, 0x1743670: unlock check, 0x17447B0: toggle state.
0x1747080: execute with eligibility/cost/event handling (calls 0x17430E0).
"""
import struct
from city_state import component, entity_rows, string, type_name
from fp1 import BridgeError

ABILITY_SYSTEM_RVA = 0x3FF81D0


def resolve(reader, entity_id):
    for entity, identifier, name in entity_rows(reader):
        if identifier == entity_id:
            return entity, name
    raise BridgeError('entity no longer exists in the current city')


def definitions(reader, entity):
    caster = component(reader, entity, 48, 'AbilityCaster')
    if not caster:
        return []
    table, count = struct.unpack('<QI', reader.read(caster+0x30,12))
    if not 0 <= count <= 256 or (count and not table):
        raise BridgeError('invalid ability catalog')
    result = []
    for n in range(count):
        definition = reader.pointer(table+n*16)
        kind = type_name(reader, definition)
        if kind not in ('.?AVFrostpunkToggleModifierAbility@@', '.?AVFrostpunkInstantEffectAbility@@'):
            raise BridgeError('unrecognized ability type')
        result.append((definition, string(reader,reader.pointer(definition+0x18)),
                       'toggle' if 'ToggleModifier' in kind else 'instant'))
    return result


def system(reader):
    ptr = reader.pointer(reader.base+ABILITY_SYSTEM_RVA)
    if not ptr or type_name(reader,ptr) != '.?AVFrostpunkAbilitySystem@@':
        raise BridgeError('ability system is unavailable')
    return ptr


def abilities(controller, entity_id):
    reader, caller = controller.reader, controller.caller
    entity, name = resolve(reader,entity_id)
    owner = system(reader)
    result = []
    for ptr, label, kind in definitions(reader,entity):
        visible = bool(caller.invoke(0x17435A0,(owner,entity,ptr)) & 255)
        unlocked = bool(caller.invoke(0x1743670,(owner,entity,ptr,0)) & 255)
        item = {'name':label,'kind':kind,'visible':visible,'unlocked':unlocked}
        if kind == 'toggle':
            value = caller.invoke(0x17447B0,(owner,entity,ptr)) & 0xffffffff
            if value not in (0,1):
                raise BridgeError('unexpected ability state')
            item['enabled'] = bool(value)
        result.append(item)
    return {'entity_id':entity_id,'entity_name':name,'abilities':result}


def set_enabled(controller, entity_id, ability, enabled):
    if type(enabled) is not bool:
        raise ValueError('enabled must be boolean')
    controller.pause()
    reader, caller = controller.reader, controller.caller
    entity, name = resolve(reader,entity_id)
    owner = system(reader)
    for ptr, label, kind in definitions(reader,entity):
        if label != ability: continue
        if kind != 'toggle':
            raise BridgeError('this operation only accepts toggle abilities')
        visible = bool(caller.invoke(0x17435A0,(owner,entity,ptr)) & 255)
        unlocked = bool(caller.invoke(0x1743670,(owner,entity,ptr,0)) & 255)
        if not visible or not unlocked:
            return {'accepted':False,'reason':'ability_not_available'}
        before = caller.invoke(0x17447B0,(owner,entity,ptr)) & 0xffffffff
        if before not in (0,1): raise BridgeError('unexpected ability state')
        if bool(before) == enabled:
            return {'accepted':True,'changed':False,'enabled':enabled}
        accepted = bool(caller.invoke(0x1747080,(owner,entity,ptr,0)) & 255)
        after = caller.invoke(0x17447B0,(owner,entity,ptr)) & 0xffffffff
        if after not in (0,1) or accepted != (bool(after)==enabled):
            raise BridgeError('ability outcome is unknown: acknowledgement differs from state')
        return {'accepted':accepted,'changed':after!=before,'enabled':bool(after),
                'entity_id':entity_id,'ability':ability}
    raise BridgeError('ability not found on this entity')
