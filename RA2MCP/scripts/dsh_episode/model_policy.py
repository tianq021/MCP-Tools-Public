"""Model-driven policy (v2 multi-head) with rule-policy fallback.

Blending rules:
  * if the model is not enabled / low confidence / class unsupported -> fall back
    to the explainable RulePolicy for that tick
  * predicted ATTACK is only emitted if the target slot resolves to an enemy
    that is visible in this observation (visibility contract, no map hack)
  * all intents are still re-validated by the runner's liveness guard

The class maps raw multi-head predictions back to native intents; the runner
executes and records them exactly like rule-policy intents.
"""
import math

import spec_v2 as S  # noqa: F401  (path added by runner)
from dsh_episode.policy import RulePolicy
from dsh_episode.hostility import is_opponent

CELL = 256


class ModelPolicy:
    def __init__(self, model, type_catalog, rule_params=None,
                 confidence_threshold=0.35, fallback=True):
        self.model = model                    # dsh_train.infer_v2.LocalModelV2
        self.type_catalog = type_catalog      # [{pointer_self,name,...}]
        self.type_id_map = {}
        try:
            meta = model.meta or {}
            catalog = (meta.get("data_info") or {}).get("type_catalog") or []
            self.type_name_by_id = {i: n for i, n in enumerate(catalog)}
        except Exception:  # noqa: BLE001
            self.type_name_by_id = {}
        self.rule = RulePolicy(rule_params, type_catalog)
        self.threshold = confidence_threshold
        self.fallback_enabled = fallback
        self.events = []
        self.violations = 0
        self.stats = {"model_actions": 0, "fallback_actions": 0,
                      "low_confidence": 0, "unsupported": 0,
                      "model_noops": 0}
        self._rule_intents = []
        self._rule_violations_seen = 0
        self.decision_source = "rule"

    # ---- RulePolicy-compatible attributes ----
    @property
    def p(self):
        return self.rule.p

    def _emit_fallback(self, ctx, reason):
        self.stats["fallback_actions"] += 1
        self.events.append({"event": "model_fallback", "t": round(ctx["t"], 2),
                            "reason": reason})
        self.decision_source = "rule"
        return self._rule_intents if self.fallback_enabled else []

    # ---- main ----
    def step(self, ctx):
        # keep the rule policy's internal bookkeeping (defend timers, produce acks,
        # pending placement detection) in sync even when the model acts
        rule_intents = self.rule.step(ctx)
        self._rule_intents = rule_intents
        self.decision_source = "rule"
        self.violations += self.rule.violations - self._rule_violations_seen
        self._rule_violations_seen = self.rule.violations
        if not getattr(self.model, "enabled", False):
            return rule_intents
        obs_row = ctx.get("observation")
        if not obs_row:
            return self._emit_fallback(ctx, "no_observation")
        try:
            features = self._build_features(obs_row)
        except Exception as e:  # noqa: BLE001
            return self._emit_fallback(ctx, f"feature_error: {e}")
        pred = self.model.act(features)
        if not pred.get("enabled"):
            return self._emit_fallback(ctx, "model_disabled")
        if pred["confidence"] < self.threshold:
            self.stats["low_confidence"] += 1
            return self._emit_fallback(ctx, "low_confidence")
        action = pred["class_id"]
        name = S.ACTION_CLASSES[action] if 0 <= action < len(S.ACTION_CLASSES) else None
        intents = self._intents_for(name, pred, ctx, obs_row)
        if intents is None:
            self.stats["unsupported"] += 1
            return self._emit_fallback(ctx, f"unsupported_{name}")
        self.stats["model_actions"] += 1
        self.decision_source = "model"
        # never let the model attack through the shroud: safety filter
        safe = []
        for it in intents:
            if it["action"] == "ATTACK":
                target = int(it.get("target_address") or 0)
                visible = {int(e["addr"]) for e in
                           (obs_row.get("visible_enemies") or [])
                           if e.get("cell_shrouded") is False}
                if target not in visible:
                    self.violations += 1
                    self.events.append({"event": "visibility_violation_skipped",
                                        "t": round(ctx["t"], 2), "addr": target})
                    continue
            safe.append(it)
        if not intents:
            self.stats["model_noops"] += 1
            return []  # A model NOOP is a decision, not permission for a rule order.
        if not safe:
            return self._emit_fallback(ctx, "unsafe_intents")
        return safe

    # ---- helpers ----
    def _build_features(self, obs_row):
        import features_v2 as F
        import spec_v2 as S2
        type_id_map = {n: i for i, n in enumerate(
            (self.model.meta.get("data_info") or {}).get("type_catalog") or [])}
        w = int(self.model.meta.get("data_info", {}).get("map_width", 182) or 182)
        h = int(self.model.meta.get("data_info", {}).get("map_height", 182) or 182)
        return F.build_obs_v2(obs_row, type_id_map, w, h)

    def _infantry(self, obs_row):
        return [o for o in (obs_row.get("own_objects") or [])
                if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY"]

    def _centroid(self, obs_row):
        own = obs_row.get("own_objects") or []
        if not own:
            return (0.0, 0.0)
        pts = [(float(o["cell"][0]), float(o["cell"][1])) for o in own]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def _base(self, obs_row):
        for o in obs_row.get("own_objects") or []:
            if o.get("type_name") == "Allied Construction Yard":
                return (float(o["cell"][0]), float(o["cell"][1]))
        return None

    def _slot_enemy(self, obs_row, slot):
        # Resolve the slot against the observation ordering (sorted by distance to
        # own centroid, same as features_v2). Visibility enforcement happens in
        # step()'s safety filter so hidden-target attempts are *recorded* and
        # dropped instead of silently disappearing.
        enemies = [e for e in (obs_row.get("visible_enemies") or []) if is_opponent(e)]
        centroid = self._centroid(obs_row)
        enemies.sort(key=lambda e: math.hypot(float(e["cell"][0]) - centroid[0],
                                              float(e["cell"][1]) - centroid[1]))
        if 0 <= slot < len(enemies):
            return enemies[slot]
        return None

    def _pending_building(self, ctx):
        return self.rule._pending_building_any(ctx, self.rule.p["build_order"])

    def _intents_for(self, name, pred, ctx, obs_row):
        t = ctx["t"]
        inf = self._infantry(obs_row)
        addrs = [o["addr"] for o in inf[: self.rule.p["max_move_units"]]]
        centroid = self._centroid(obs_row)
        base = self._base(obs_row)
        if name == "NOOP":
            return []
        if name == "DEPLOY":
            mcv = next((o for o in (obs_row.get("own_objects") or [])
                        if "Construction Vehicle" in str(o.get("type_name"))), None)
            if mcv is None:
                return None
            return [{"action": "DEPLOY", "object_addresses": [mcv["addr"]]}]
        if name == "MOVE":
            if not addrs or base is None:
                return None
            dest_cell = (centroid[0] + pred["move_dx"] * 30.0,
                         centroid[1] + pred["move_dy"] * 30.0)
            return [{"action": "MOVE", "object_addresses": addrs,
                     "coordinates": [int(dest_cell[0] * CELL), int(dest_cell[1] * CELL)]}]
        if name == "RETREAT":
            if not addrs or base is None:
                return None
            return [{"action": "MOVE", "object_addresses": addrs,
                     "coordinates": [int(base[0] * CELL), int(base[1] * CELL)]}]
        if name == "ATTACK":
            if not addrs:
                return None
            target = self._slot_enemy(obs_row, pred["target_slot"])
            if target is None:
                return None
            return [{"action": "ATTACK", "object_addresses": addrs,
                     "target_address": target["addr"],
                     "target_type": target.get("type_name"),
                     "coordinates": [int(target["cell"][0] * CELL),
                                     int(target["cell"][1] * CELL)]}]
        if name == "STOP":
            if not addrs:
                return None
            return [{"action": "STOP", "object_addresses": addrs}]
        if name == "PRODUCE":
            tid = pred.get("produce_type_id", -1)
            tname = self.type_name_by_id.get(tid)
            tc = next((x for x in self.type_catalog if x.get("name") == tname), None)
            if tc is None:
                return None
            return [{"action": "PRODUCE", "object_addresses": [],
                     "production_type": tname,
                     "production_type_ptr": int(tc["pointer_self"]),
                     "production_action": "BEGIN"}]
        if name == "PLACE":
            pending = self._pending_building(ctx)
            if pending is None:
                return None
            b = base or (91.0, 58.0)
            anchor = (int(b[0] + pred["place_dx"] * 10.0),
                      int(b[1] + pred["place_dy"] * 10.0))
            return [{"action": "PLACE",
                     "building_address": pending["addr"],
                     "production_type": pending["type_name"],
                     "production_type_ptr": pending["type_ptr"],
                     "anchor": [anchor[0], anchor[1]],
                     "object_addresses": []}]
        return None
