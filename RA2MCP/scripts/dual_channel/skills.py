"""Skill-instance lifecycle evidence; state changes are never causal proof."""
from __future__ import annotations

import math
from collections import Counter


def own_counts(obs):
    return Counter(o.get("type_name") for o in obs.get("own_objects", [])
                   if o.get("on_map") and not o.get("in_limbo") and o.get("health", 0) > 0)


class SkillRuntime:
    def __init__(self, episode_id):
        self.episode_id = episode_id
        self.serial = 0
        self.active = {}
        self.events = []

    def start(self, proposal, obs, decision_id):
        self.serial += 1
        ident = f"{self.episode_id}:skill:{self.serial:05d}"
        inst = {"schema": "ra2.skill-instance/1", "instance_id": ident,
                "skill_id": proposal["skill_id"], "skill_version": "1",
                "owner_source": "collector_teacher", "decision_id": decision_id,
                "start_frame": obs["frame"], "start_time": float(obs.get("t") or 0),
                "state": "RUNNING", "intent": proposal.get("intent"),
                "slot_bindings": proposal["slot_bindings"],
                "before_counts": dict(own_counts(obs)), "elapsed_ticks": 0,
                "evidence_level": "UNKNOWN", "effect_actor_eligible": False}
        self.active[ident] = inst
        self.events.append(dict(inst))
        return ident

    def observe(self, obs, terminal=False):
        now = float(obs.get("t") or 0)
        own = own_counts(obs)
        objects = {o.get("addr"): o for o in obs.get("own_objects", [])}
        for ident, inst in list(self.active.items()):
            inst["elapsed_ticks"] += 1
            intent = inst.get("intent") or {}
            action = intent.get("action")
            success = False
            if action == "PRODUCE":
                name = intent.get("production_type")
                success = own.get(name, 0) > inst["before_counts"].get(name, 0)
            elif action == "DEPLOY":
                success = any("Construction Yard" in str(k) and v > inst["before_counts"].get(k, 0) for k, v in own.items())
            elif action == "PLACE":
                obj = objects.get(intent.get("building_address"))
                success = bool(obj and obj.get("on_map") and not obj.get("in_limbo"))
            elif action == "MOVE" and intent.get("coordinates"):
                dest = [(c - 128) / 256 for c in intent["coordinates"]]
                actors = [objects[a] for a in intent.get("object_addresses", []) if a in objects]
                success = bool(actors) and all(math.dist(o["cell"], dest) <= 3 for o in actors)
            age = now - inst["start_time"]
            if success or terminal or age > (70 if action == "PRODUCE" else 25):
                inst.update(state="SUCCEEDED" if success else "ABORTED" if terminal else "EXPIRED",
                            end_frame=obs["frame"], end_time=now,
                            evidence_level="STATE_DERIVED" if success else "UNKNOWN",
                            reason="observed state condition" if success else "episode ended" if terminal else "skill timeout")
                self.events.append(dict(inst))
                del self.active[ident]

    def drain(self):
        out, self.events = self.events, []
        return out
