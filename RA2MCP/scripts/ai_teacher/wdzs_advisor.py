"""Optional read-only WDZS guidance for the existing RA2FullEnv host interface.

Nothing is installed into the production loop. This module never executes an
action, opens a client, changes model inputs, or emits BC ground-truth labels.
"""
from __future__ import annotations

import collections
import re


def normal_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def yes(value):
    return str(value).casefold() in ("yes", "true", "1")


class WdzsAdvisor:
    def __init__(self, library, rules):
        self.library, self.rules = library, rules
        self.name_ids = collections.defaultdict(list)
        for ident, unit in rules["units"].items():
            if unit.get("name"):
                self.name_ids[normal_name(unit["name"])].append(ident)

    def type_id(self, name):
        ids = self.name_ids.get(normal_name(name), [])
        return ids[0] if len(ids) == 1 else None

    def property_card(self, ident):
        unit = self.rules["units"].get(ident)
        if not unit:
            return {"id": ident, "status": "UNKNOWN"}
        fields = unit["fields"]
        weapons = []
        for role in ("primary", "secondary"):
            key = fields.get(role, "").upper()
            if key:
                weapon = self.rules["weapons"].get(key, {})
                warhead = self.rules["warheads"].get(weapon.get("warhead", "").upper(), {})
                projectile = self.rules["projectiles"].get(weapon.get("projectile", "").upper(), {})
                weapons.append({"role": role, "id": key,
                                "damage": weapon.get("damage"), "range": weapon.get("range"),
                                "rof": weapon.get("rof"), "warhead": weapon.get("warhead"),
                                "verses": warhead.get("verses"), "aa": projectile.get("aa"),
                                "ag": projectile.get("ag"), "status": "STATIC_RULE_FIELDS"})
        return {"id": ident, "type_index": self.rules["vocabulary"][ident],
                "name": unit.get("name"), "kind": unit["kind"],
                "cost": fields.get("cost"), "strength": fields.get("strength"),
                "armor": fields.get("armor"), "speed": fields.get("speed"),
                "prerequisite": fields.get("prerequisite"), "weapons": weapons,
                "status": "STATIC_REFERENCE_NOT_RUNTIME_EFFECTIVE",
                "source_sha256": unit["rules_source_sha256"]}

    def _combat_template(self, runtime_catalog):
        supported = {self.type_id(item.get("name")) for item in runtime_catalog}
        candidates = []
        for team in self.library["teams"].values():
            force = self.library["taskforces"].get(team["taskforce_id"], {})
            script = self.library["scripts"].get(team["script_id"], {})
            composition = force.get("units", [])
            if not composition or any(unit["type_id"] not in supported for unit in composition):
                continue
            source_units = [self.rules["units"].get(unit["type_id"], {}) for unit in composition]
            # Engine actions named ATTACK can mean capture when actors are engineers.
            # Do not translate these, naval/aircraft, transports or harvesters to fire.
            if any(unit.get("kind") not in ("infantry", "vehicle") or
                   any(yes(unit.get("fields", {}).get(key, "no"))
                       for key in ("engineer", "agent", "infiltrate", "thief", "naval", "harvester", "resourcegatherer")) or
                   int(unit.get("fields", {}).get("passengers", "0")) > 0
                   for unit in source_units):
                continue
            if not any(a["code"] in (0, 46) for a in script.get("actions", [])):
                continue
            if any(a["coverage"] == "UNSUPPORTED" for a in script.get("actions", [])):
                continue
            size = sum(unit["count"] for unit in composition)
            costs = {self.type_id(item.get("name")): item.get("cost") for item in runtime_catalog}
            total_cost = sum(float(costs.get(unit["type_id"]) or 10**6) * unit["count"] for unit in composition)
            # Advisory preference, not a claim about the native AI's priorities:
            # prefer actual mixed compositions, then a lower target-game cost.
            rank = (len(composition) < 2, total_cost, size)
            candidates.append((rank, team["id"], team, force, script))
        return min(candidates, default=None, key=lambda item: (item[0], item[1]))

    def suggest(self, observation, runtime_catalog, action_mask):
        if len(action_mask) != 301:
            raise ValueError("existing ACTION_N=301 mask required")
        own = [o for o in observation.get("own_objects", [])
               if o.get("on_map", True) and not o.get("in_limbo", False)
               and o.get("health", 0) > 0]
        counts = collections.Counter(self.type_id(o.get("type_name")) for o in own)
        slots = collections.defaultdict(list)
        for index, item in enumerate(runtime_catalog[:96]):
            ident = self.type_id(item.get("name"))
            if ident:
                slots[ident].append(index)
        result = {"schema": "wdzs.advice/1", "frame": observation.get("frame"),
                  "role": "ADVISORY_ONLY", "training_label_eligible": False,
                  "source_ai_sha256": self.library["source_sha256"],
                  "source_rules_sha256": self.rules["source_sha256"],
                  "candidate_301": None, "team_template": None,
                  "limitations": ["No native engine trigger reproduction or live command execution.",
                                  "Mod static properties may differ from the target game's rules."]}
        pending_actions = [action for action in range(98, 130) if action_mask[action]]
        if pending_actions:
            result.update(goal="PLACE_FINISHED_BUILDING", direction_origin="EXISTING_HOST_ACTION_MASK",
                          candidate_301=pending_actions[0], readiness="MASK_LEGAL_CANDIDATE")
            return result
        roles = self.rules["ai_build_roles"]
        # Adapter bootstrap, explicitly distinguished from a native AI build order.
        # [AI] Build* values list building roles; their INI order is NOT an order list.
        bootstrap = (("buildconst", "DEPLOY_BASE"), ("buildpower", "BUILD_POWER"),
                     ("buildrefinery", "BUILD_REFINERY"), ("buildbarracks", "BUILD_BARRACKS"),
                     ("buildweapons", "BUILD_WAR_FACTORY"))
        for key, goal in bootstrap:
            ids = [item.strip().upper() for item in roles.get(key, "").split(",") if item.strip()]
            if not ids or (goal != "DEPLOY_BASE" and not any(ident in slots or counts[ident] for ident in ids)):
                continue
            if any(counts[ident] for ident in ids):
                continue
            result.update(goal=goal, direction_origin="ADAPTER_BOOTSTRAP_WITH_RULE_ROLE_REFERENCES",
                          desired_types=ids, source_reference=f"RulesMD.ini:[AI] {key}")
            if goal == "DEPLOY_BASE":
                result["candidate_301"] = 1 if action_mask[1] else None
            else:
                legal = [2+slot for ident in ids for slot in slots[ident] if action_mask[2+slot]]
                result["candidate_301"] = legal[0] if legal else None
            result["readiness"] = "MASK_LEGAL_CANDIDATE" if result["candidate_301"] is not None else "WAIT_FOR_FUNDS_OR_PREREQUISITE"
            return result  # never spend on more barracks while refinery is missing
        chosen = self._combat_template(runtime_catalog)
        if chosen is None:
            result.update(goal="NO_COMPATIBLE_TEMPLATE", direction_origin="UNKNOWN",
                          readiness="DOMAIN_OR_ACTION_GAP")
            return result
        _, _, team, force, script = chosen
        missing = [{**unit, "missing": max(0, unit["count"]-counts[unit["type_id"]])}
                   for unit in force["units"]]
        result.update(goal="ASSEMBLE_TEAM" if any(u["missing"] for u in missing) else "TEAM_TACTICAL_GUIDANCE",
                      direction_origin="EXTRACTED_TEAM_COMPOSITION_AND_SCRIPT",
                      team_template={"team_id": team["id"], "name": team["name"],
                                     "taskforce_id": force["id"], "composition": missing,
                                     "script_id": script["id"], "objectives": script["actions"]},
                      property_cards=[self.property_card(unit["type_id"]) for unit in force["units"]],
                      readiness="ADVICE_AVAILABLE_NATIVE_TRIGGER_UNVERIFIED")
        for unit in missing:
            legal = [2+slot for slot in slots[unit["type_id"]] if action_mask[2+slot]]
            if unit["missing"] and legal:
                result["candidate_301"] = legal[0]
                break
        return result

    def suggest_from_env(self, env):
        """Read the existing host contract; never calls step/reset/execute/client."""
        return self.suggest(env.current, env.full.types, env.action_masks())
