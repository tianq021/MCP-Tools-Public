"""Normal engine on/off commands, including activity recomputation and events."""
from ability_control import resolve
from city_state import component, entity_table
from fp1 import BridgeError


def set_enabled(controller, entity_id, enabled):
    if type(enabled) is not bool:
        raise ValueError('enabled must be boolean')
    controller.pause()
    r = controller.reader
    entity, name = resolve(r,entity_id)
    system, _, _ = entity_table(r)
    activity = component(r,entity,6,'PlaceActivity')
    if not activity or not r.read(r.pointer(activity+0x20)+0x40,1)[0]:
        raise BridgeError('the engine does not allow switching this place on/off')
    before = not bool(r.read(activity+0x3c,1)[0])
    if before != enabled:
        controller.caller.invoke(0x12315B0 if enabled else 0x12314E0,(system,entity,1))
    after = not bool(r.read(activity+0x3c,1)[0])
    if after != enabled:
        raise BridgeError('building command did not read back the requested state')
    return {'accepted':True,'changed':before!=after,'entity_id':entity_id,
            'name':name,'enabled':after}
