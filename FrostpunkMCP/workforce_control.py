"""Work assignment via the same native AI entry points used by the game's UI."""
import struct
from city_state import component, entity_rows, string, type_name
from ability_control import resolve


def systems(controller):
    r=controller.reader
    ai=r.pointer(r.base+0x3fea0b8)
    place=r.pointer(r.base+0x3fe9700)
    if type_name(r,ai)!='.?AVFrostpunkAISystem@@' or type_name(r,place)!='.?AVFrostpunkPlaceForAgentSystem@@':
        raise RuntimeError('workforce system layout changed')
    return ai,place


def agent_rows(controller):
    r=controller.reader
    ai,_=systems(controller)
    _,place=systems(controller)
    collections=[ai+0x24a0]  # UnassignedAgents[WORK], native assignment/removal both use this vector.
    for entity,eid,name in entity_rows(r):
        if component(r,entity,38,'PlaceForAgent'):
            collections.append(controller.caller.invoke(0x14c3100,(place,entity,1)))
    pointers=set()
    for vec in collections:
        table,count=struct.unpack('<QI',r.read(vec,12))
        if not 0<=count<10000:raise RuntimeError('invalid agent collection')
        if count:pointers.update(struct.unpack(f'<{count}Q',r.read(table,count*8)))
    result=[]
    for entity in sorted(pointers):
        if type_name(r,entity)!='.?AVFrostpunkEntity@@':raise RuntimeError('invalid AI entity')
        # Resolve only validated agent entities with AI components.
        if component(r,entity,29,'AI'):
            result.append((entity,r.read(entity+0x18,16).hex(),string(r,r.pointer(entity+0x48))))
    return result


def assigned(controller,building_id):
    entity,_=resolve(controller.reader,building_id)
    if not component(controller.reader,entity,38,'PlaceForAgent'):
        raise ValueError('building has no assignment slots')
    _,place=systems(controller)
    vec=controller.caller.invoke(0x14c3100,(place,entity,1))
    table,count=struct.unpack('<QI',controller.reader.read(vec,12))
    if not 0<=count<=10000:raise RuntimeError('invalid assignment count')
    rows=[]
    for agent in struct.unpack(f'<{count}Q',controller.reader.read(table,count*8)) if count else []:
        rows.append({'id':controller.reader.read(agent+0x18,16).hex(),
                     'name':string(controller.reader,controller.reader.pointer(agent+0x48))})
    return {'building_id':building_id,'assigned':rows,'count':count}


def set_workplace(controller,agent_id,building_id=None):
    controller.pause()
    r=controller.reader
    ai,_=systems(controller)
    agent=next((p for p,i,n in agent_rows(controller) if i==agent_id),None)
    if not agent:raise ValueError('unknown agent ID')
    before=controller.caller.invoke(0x14e41a0,(ai,agent,1))
    target=resolve(r,building_id)[0] if building_id else 0
    if target and not component(r,target,38,'PlaceForAgent'):
        raise ValueError('target has no assignment slots')
    previous=r.read(before+0x18,16).hex() if before else None
    if before==target:
        return {'accepted':True,'changed':False,'agent_id':agent_id,'building_id':building_id,'previous_building_id':previous}
    if target:
        accepted=bool(controller.caller.invoke(0x14e0b50,(ai,agent,target,1,r.base+0x2971c60))&255)
    else:
        accepted=bool(controller.caller.invoke(0x14f94d0,(ai,agent,1,r.base+0x2971c60))&255)
    after=controller.caller.invoke(0x14e41a0,(ai,agent,1))
    if accepted!=(after==target):raise RuntimeError('assignment acknowledgement differs from live state')
    return {'accepted':accepted,'changed':after!=before,'agent_id':agent_id,
            'building_id':r.read(after+0x18,16).hex() if after else None,'previous_building_id':previous}
