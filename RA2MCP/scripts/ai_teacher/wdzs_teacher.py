"""Executable vanilla adapter for selected real WDZS teams, not a NPatch VM.

Uses the existing raw-v2 bridge intent format. It never changes the student's
301 contract, imports RPC code, or treats raw squad commands as 301 labels.
"""
from __future__ import annotations
import collections
import json
import math
import pathlib
import re
from dsh_episode.hostility import is_opponent

VERSION="wdzs_vanilla_teacher:0.1"
TEAMS=("09000221-G","09000092-G")  # Grizzly Rush Medium -> Allied Bombard

def norm(name):return re.sub("[^a-z0-9]","",(name or "").lower())
def placed(o):return o.get("on_map",True) and not o.get("in_limbo") and o.get("health",0)>0
def distance(a,b):return math.dist(a,b)

class WdzsTeacher:
    def __init__(self,bundle):
        bundle=pathlib.Path(bundle)
        self.library=json.loads((bundle/"compiled_ai.json").read_text(encoding="utf-8"))
        self.rules=json.loads((bundle/"rules_reference.json").read_text(encoding="utf-8"))
        provenance=json.loads((bundle/"SOURCE_PROVENANCE.json").read_text(encoding="utf-8"))
        if self.library["source_sha256"]!=provenance["candidate"]["sha256"]:raise ValueError("source conflict")
        self.team_index=0;self.issued={};self.rally_since=None;self.launched=False;self.turn=0
        for ident in TEAMS:
            t=self.library["teams"][ident]
            codes={a["code"] for a in self.library["scripts"][t["script_id"]]["actions"]}
            if not codes<={0,5,6,49,53,54}:raise ValueError("unsupported native opcode")

    def _unit_name(self,ident):return self.rules["units"][ident]["name"]
    def _order(self,intents,groups,key,intent,t,seconds=6):
        signature=json.dumps(intent,sort_keys=True)
        old=self.issued.get(key)
        if old and t-old[0]<seconds and old[1]==signature:return
        if old and t-old[0]<1.5:return
        self.issued[key]=(t,signature)
        intents.append(intent);groups.append(key)

    def plan(self,row,spec,starts,map_soa):
        # Only own state and actually visible enemies are inspected.
        own=[o for o in row.get("own_objects",[]) if placed(o)]
        buildings=[o for o in own if o.get("abstract")=="ABSTRACT_TYPE_BUILDING"]
        combat=[o for o in own if o.get("abstract") in ("ABSTRACT_TYPE_INFANTRY","ABSTRACT_TYPE_UNIT")
                and not any(x in o.get("type_name","") for x in ("Miner","Harvester","Construction Vehicle","Engineer","Spy"))]
        t=float(row.get("t",0));mask=spec.masks(row,starts,map_soa)
        counts=collections.Counter(norm(o.get("type_name")) for o in own)
        slots=collections.defaultdict(list)
        for i,item in enumerate(spec.types):slots[norm(item["name"])].append(i)
        intents=[];groups=[];economic=[]
        base=next((o["cell"] for o in buildings if "Construction Yard" in o.get("type_name","")),None)

        def production(name,reason):
            for slot in slots[norm(name)]:
                if mask[2+slot]:
                    intent=spec.intent(2+slot,row,starts,map_soa)
                    self._order(intents,groups,"production:"+intent["production_type"],intent,t,3)
                    economic.append(reason);return True
            return False

        # Existing exposed mask/native place query remain the economy authority.
        place_actions=[a for a in range(98,130) if mask[a]]
        if place_actions:
            action=place_actions[self.turn%min(8,len(place_actions))];self.turn+=1
            intent=spec.intent(action,row,starts,map_soa)
            self._order(intents,groups,"place",intent,t,1.5);economic.append("PLACE_COMPLETED")
        if base is None:
            if mask[1]:self._order(intents,groups,"deploy",spec.intent(1,row,starts,map_soa),t,3)
            return self._result(intents,groups,"DEPLOY_BASE",economic,None)

        # Explicit vanilla bootstrap using WDZS [AI] roles; not native trigger emulation.
        roles=self.rules["ai_build_roles"]
        chain=(("buildpower",1),("buildrefinery",1),("buildbarracks",1),("buildweapons",1),
               ("buildradar",1),("buildtech",1),("buildrefinery",2))
        power=next(iter(row.get("houses",[])),{})
        if power.get("power_output",0)<power.get("power_drain",0)+50:
            chain=(("buildpower",sum("Power Plant" in o.get("type_name","") for o in buildings)+1),)+chain
        for role,needed in chain:
            names=[self._unit_name(ident.strip().upper()) for ident in roles.get(role,"").split(",")
                   if ident.strip().upper() in self.rules["units"]
                   and self._unit_name(ident.strip().upper()).startswith("Allied")]
            if names and sum(counts[norm(name)] for name in names)<needed:
                for name in names:
                    if production(name,role):break
                break  # wait instead of spending on unrelated duplicate buildings

        if counts[norm("Allied Battle Lab")]:self.team_index=1
        team=self.library["teams"][TEAMS[self.team_index]]
        force=self.library["taskforces"][team["taskforce_id"]]
        script=self.library["scripts"][team["script_id"]]
        missing=[]
        for unit in force["units"]:
            name=self._unit_name(unit["type_id"])
            if not slots[norm(name)]:raise ValueError("formation not available in target catalog: "+name)
            remaining=max(0,unit["count"]-counts[norm(name)])
            missing.append({"name":name,"type_id":unit["type_id"],"wanted":unit["count"],"missing":remaining})
        # First get the low-tech wave, then progress to the native mixed formation.
        for unit in missing:
            if unit["missing"] and production(unit["name"],"SOURCE_TEAM_COMPOSITION"):break

        if combat:
            enemy_goal=max(starts,key=lambda s:distance(base,s)) if starts else None
            enemies=[e for e in row.get("visible_enemies",[]) if placed(e)
                     and e.get("is_visible",True) and not e.get("cell_shrouded",False)
                     and is_opponent(e) and e.get("owner_ptr")!=own[0].get("owner_ptr")]
            threats=[e for e in enemies if distance(e["cell"],base)<22]
            ready=not any(u["missing"] for u in missing)
            if ready and self.rally_since is None:self.rally_since=t
            # Existing first wave may stay deployed while the advanced wave assembles.
            if ready and self.rally_since is not None and t-self.rally_since>=5:self.launched=True
            if not self.launched and t>=90 and len(combat)>=7:self.launched=True
            if enemy_goal:
                dx,dy=enemy_goal[0]-base[0],enemy_goal[1]-base[1];length=max(1,math.hypot(dx,dy))
                vector=(dx/length,dy/length)
            else:vector=(1,0)
            squads={"front":[],"fire":[],"support":[]}
            for obj in combat:
                role="fire" if obj["type_name"] in ("Prism Tank","Mirage Tank") else "support" if obj.get("abstract")=="ABSTRACT_TYPE_INFANTRY" else "front"
                squads[role].append(obj)
            for role,members in squads.items():
                if not members:continue
                center=[sum(o["cell"][i] for o in members)/len(members) for i in (0,1)]
                candidates=threats or enemies if self.launched else threats
                if candidates:
                    # Source General Bombard prefers buildings after gathering;
                    # visible local threats take precedence over a distant objective.
                    target=min(candidates,key=lambda e:(0 if threats else (0 if role=="fire" and e.get("abstract")=="ABSTRACT_TYPE_BUILDING" else 1),distance(e["cell"],center)))
                    intent={"action":"ATTACK","object_addresses":[o["addr"] for o in members],
                            "target_address":target["addr"],"target_type":target["type_name"],
                            "coordinates":[int(c*256+128) for c in target["cell"]]}
                    self._order(intents,groups,role,intent,t,8)
                else:
                    if self.launched and enemy_goal:
                        goal=[enemy_goal[i]-vector[i]*(5 if role=="fire" else 2 if role=="support" else 0) for i in (0,1)]
                    else:goal=[base[i]+vector[i]*(8 if role=="front" else 5) for i in (0,1)]
                    if distance(center,goal)>4:
                        intent={"action":"MOVE","object_addresses":[o["addr"] for o in members],
                                "coordinates":[int(max(0,c)*256+128) for c in goal]}
                        self._order(intents,groups,role,intent,t,10)
            # Single-unit scout, not the whole economy/army.
            if not self.launched and enemy_goal and t>15:
                scout=next((o for o in combat if o["type_name"]=="GI"),None)
                if scout:
                    # Remove this actor from conflicting support orders in this plan.
                    for intent in intents:
                        if intent.get("action") in ("MOVE","ATTACK"):
                            intent["object_addresses"]=[a for a in intent["object_addresses"] if a!=scout["addr"]]
                    self._order(intents,groups,"scout",{"action":"MOVE","object_addresses":[scout["addr"]],
                        "coordinates":[int(c*256+128) for c in enemy_goal]},t,20)
        valid=[(intent,group) for intent,group in zip(intents,groups)
               if intent.get("action") in ("PRODUCE","PLACE") or intent.get("object_addresses")]
        phase="ATTACK" if self.launched else "RALLY" if self.rally_since is not None else "ASSEMBLE"
        return self._result([p[0] for p in valid],[p[1] for p in valid],phase,economic,
            {"id":team["id"],"name":team["name"],"taskforce_id":force["id"],"script_id":script["id"],
             "script_actions":[a["raw"] for a in script["actions"]],"composition":missing})

    def _result(self,intents,groups,phase,economic,team):
        return {"teacher":VERSION,"source_ai_sha256":self.library["source_sha256"],"phase":phase,
                "economic_goals":economic,"team":team,"intents":intents,"groups":groups,
                "native_interpreter_reproduced":False,"student_301_label":None,
                "adaptations":["vanilla economy/prerequisites", "visible-target validation", "role-based ground squad executor"]}
