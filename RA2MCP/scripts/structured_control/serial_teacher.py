"""Replan on every real observation; commit bookkeeping only for selected role.

The fixed group builder affects teacher semantics. Raw formation membership may
be remapped to that role's fixed group, and both intents are kept for review.
Discarded proposals never become a queue of stale commands.
"""
import copy
from ai_teacher.formation_teacher import FormationTeacher,semantic
from .contract import (FAMILIES,ROLES,intent_of,validate_action,base_cell_of,place_anchor_mode,
    PLACE_ANCHOR_RELATIVE,PLACE_OFFSET_ORIGIN)


def encode_command(command,snapshot):
    intent=command['intent'];family=intent['action'];action={'family':FAMILIES.index(family)}
    if family in ('STOP','MOVE','ATTACK'):
        action['group']=ROLES.index(command['role'])
    if family=='PRODUCE':action['production']=next(p['slot'] for p in snapshot['production'] if p['ptr']==intent['production_type_ptr'])
    if family=='PLACE':
        action['pending']=next(i for i,p in enumerate(snapshot['pending']) if p['addr']==intent['building_address'])
        if place_anchor_mode(snapshot)==PLACE_ANCHOR_RELATIVE:
            base=base_cell_of(snapshot)
            if base is None:raise ValueError('relative PLACE anchor requires a placed base building')
            action.update(x=PLACE_OFFSET_ORIGIN+int(intent['anchor'][0])-base[0],
                          y=PLACE_OFFSET_ORIGIN+int(intent['anchor'][1])-base[1])
        else:action.update(x=int(intent['anchor'][0]),y=int(intent['anchor'][1]))
    if family=='MOVE':
        x,y=intent['coordinates'];action.update(x=int((x-128)/256),y=int((y-128)/256))
    if family=='ATTACK':action['target']=next(i for i,e in enumerate(snapshot['entities']) if e['addr']==intent['target_address'])
    validate_action(snapshot,action)
    return action


class SerialTeacher:
    def __init__(self):
        self.planner=FormationTeacher();self.selected_at={};self.tick=0

    def choose(self,observation,spec,starts,map_soa,snapshot):
        self.tick+=1;before=copy.deepcopy(self.planner)
        plan=self.planner.plan(observation,spec,starts,map_soa)
        candidates=[];rejected=[]
        for command in plan['commands']:
            try:
                action=encode_command(command,snapshot)
                candidates.append((command,action))
            except (ValueError,StopIteration) as exc:
                rejected.append({'role':command['role'],'reason':str(exc) or type(exc).__name__})
        selected=min(candidates,key=lambda c:(self.selected_at.get(c[0]['role'],-100000),
                      0 if c[0]['role']=='economy' else 1,ROLES.index(c[0]['role']) if c[0]['role'] in ROLES else -1)) if candidates else None
        role=selected[0]['role'] if selected else None
        # Planner.plan marks proposed commands as issued; undo unselected roles.
        for r in ROLES:
            if r!=role:
                if r in before.issued:self.planner.issued[r]=before.issued[r]
                else:self.planner.issued.pop(r,None)
        if role!='economy':
            economy_tick=self.planner.economy.tick
            self.planner.economy=before.economy
            self.planner.economy.tick=economy_tick
        if selected:
            command,action=selected;self.selected_at[role]=self.tick
            proposed=command['intent'];resolved=intent_of(snapshot,action)
            return {'action':action,'intent':resolved,'original_raw_proposal':proposed,'role':role,
                'skill_id':command['skill_id'],'membership_remapped':semantic(proposed)!=semantic(resolved),
                'candidate_roles':[c[0]['role'] for c in candidates],'discarded_proposals':rejected,
                'scheduler':'least-recently-selected-role/1; no stale queue','script_contribution':'fixed group memberships + teacher tactics + role scheduling'}
        return {'action':{'family':0},'intent':None,'original_raw_proposal':None,'role':None,
            'skill_id':'serial.wait','membership_remapped':False,'candidate_roles':[],
            'discarded_proposals':rejected,'scheduler':'least-recently-selected-role/1; no stale queue'}
