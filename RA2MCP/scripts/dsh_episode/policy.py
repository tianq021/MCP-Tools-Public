"""Explainable rule policy used to collect expert trajectories (v2 collector).

The policy only consumes the raw observation dict captured by `obs.py`, which
already excludes hidden enemies. It returns *intents*; the runner turns them into
native commands and records the full parameters + ACK.

All thresholds are plain parameters so phase-4 can run policy search (grid /
evolution) over `POLICY_DEFAULTS`.
"""
import math
from dsh_episode.hostility import is_opponent

CELL = 256

POLICY_DEFAULTS = {
    "build_order": ["Allied Power Plant", "Allied Barracks"],
    "infantry_type": "GI",
    "infantry_cap": 8,
    "attack_radius_cells": 8.0,
    "attack_reissue_s": 3.0,
    "produce_interval_s": 2.5,
    "build_reissue_s": 4.0,
    "place_reissue_s": 1.0,
    "advance_step_cells": 8,
    "advance_interval_s": 3.0,
    "advance_edge_margin_frac": 0.15,
    "max_move_units": 8,
    "defend_window_s": 20.0,
    "move_waypoint_max_y_cell": 150,
}


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _obj_cell(o):
    return (o["cell"][0], o["cell"][1])


class RulePolicy:
    def __init__(self, params=None, type_catalog=None):
        self.p = dict(POLICY_DEFAULTS)
        if params:
            self.p.update(params)
        self.type_catalog = type_catalog or []
        self.build_send_at = {}
        self.place_send_at = 0.0
        self.gi_send_at = -99.0
        self.deploy_send_at = -99.0
        self.attack_send_at = {}
        self.last_move_at = -99.0
        self.advance_goal_y = None
        self.advance_dir = None
        self.move_invalid_at = -99.0
        self.advance_pause_until = -1.0
        self.last_hp = {}
        self.defend_until = -1.0
        self.events = []       # events detected by the policy (defend triggers...)
        self.violations = 0    # attempted orders against invisible enemies
        self.produce_ok_at = {}    # type name -> last successful PRODUCE ack time
        self.produce_fail_at = {}  # type name -> last failed PRODUCE ack time

    # ---- helpers ----
    def _type_by_name(self, name):
        for t in self.type_catalog:
            if t.get("name") == name:
                return t
        return None

    def _type_ptr_by_name(self, name):
        t = self._type_by_name(name)
        return int(t["pointer_self"]) if t else None

    def note(self, t, kind, **data):
        self.events.append({"event": kind, "t": round(t, 3), **data})

    # ---- main entry ----
    def step(self, ctx):
        t = ctx["t"]
        for ack in ctx.get("last_acks") or []:
            if ack.get("action") == "PRODUCE" and ack.get("production_type"):
                name = ack["production_type"]
                if ack.get("error"):
                    self.produce_fail_at[name] = ack.get("t", t)
                else:
                    self.produce_ok_at[name] = ack.get("t", t)
            elif ack.get("action") == "MOVE" and ack.get("error"):
                # native rejected the destination (e.g. outside the playable map
                # diamond): pull the advance goal back and pause advancing.
                # regression: ep-trial-03 issued 38x "MOVE: invalid cell" when
                # the goal hit the map edge (y=10/14).
                if "invalid cell" in str(ack.get("error")):
                    self.move_invalid_at = ack.get("t", t)
                    self.advance_pause_until = t + 2 * self.p["advance_interval_s"]
                    if self.advance_goal_y is not None:
                        self.advance_goal_y -= self.advance_dir * \
                            max(2, self.p["advance_step_cells"] // 2)
        own = ctx["own_objects"]
        inf = [o for o in own if o["abstract"] == "ABSTRACT_TYPE_INFANTRY"]
        buildings = [o for o in own if o["abstract"] == "ABSTRACT_TYPE_BUILDING"]
        units = [o for o in own if o["abstract"] in ("ABSTRACT_TYPE_UNIT",
                                                     "ABSTRACT_TYPE_AIRCRAFT")]
        visible = [e for e in ctx["visible_enemies"] if is_opponent(e)
                   and e.get("abstract") != "ABSTRACT_TYPE_AIRCRAFT"]
        have = {b["type_name"] for b in buildings}
        intents = []

        # --- defend detection: own building lost health ---
        for b in buildings:
            prev = self.last_hp.get(b["addr"])
            if prev is not None and b["health"] < prev:
                if t > self.defend_until:
                    self.note(t, "defend_triggered", addr=b["addr"],
                              hp_before=prev, hp_now=b["health"])
                self.defend_until = t + self.p["defend_window_s"]
            self.last_hp[b["addr"]] = b["health"]
        for o in own:
            self.last_hp[o["addr"]] = o["health"]

        # --- 1) deploy MCV ---
        cy = next((b for b in buildings if b["type_name"] == "Allied Construction Yard"), None)
        if cy is None:
            mcv = next((u for u in units
                        if "Construction Vehicle" in u["type_name"]), None)
            if mcv is not None and t - self.deploy_send_at > 2.0:
                self.deploy_send_at = t
                intents.append({"action": "DEPLOY", "object_addresses": [mcv["addr"]]})
            return intents
        base = _obj_cell(cy)

        # --- 2) construction by parameterised order ---
        # only *placed* buildings count towards the build order; a completed but
        # unplaced building must be placed first (phase-2 bug fixed: previously
        # the unplaced power plant was counted as done and the policy stalled).
        placed = [b for b in buildings if b.get("on_map") and not b.get("in_limbo")]
        have = {b["type_name"] for b in placed}
        pending = self._pending_building_any(ctx, self.p["build_order"])
        if pending is not None and t - self.place_send_at > self.p["place_reissue_s"]:
            self.place_send_at = t
            intents.append({"action": "PLACE",
                            "building_address": pending["addr"],
                            "production_type": pending["type_name"],
                            "production_type_ptr": pending["type_ptr"],
                            "anchor": list(base),
                            "object_addresses": []})
            return intents
        next_building = next((b for b in self.p["build_order"] if b not in have), None)
        if next_building is not None:
            ptr = self._type_ptr_by_name(next_building)
            ok_at = self.produce_ok_at.get(next_building, -99.0)
            # if a production of this type already ACKed recently, wait for the
            # building to appear instead of spamming PRODUCE (avoids the 60x
            # "unbuildable" error loop seen in the first real episode)
            waiting = (t - ok_at) < 30.0
            if ptr is not None and not waiting and \
                    t - self.build_send_at.get(next_building, -99.0) > \
                    self.p["build_reissue_s"]:
                self.build_send_at[next_building] = t
                intents.append({"action": "PRODUCE", "production_type": next_building,
                                "production_type_ptr": ptr,
                                "production_action": "BEGIN",
                                "object_addresses": []})
            return intents

        # --- 3) infantry production ---
        gi_ptr = self._type_ptr_by_name(self.p["infantry_type"])
        gi_ok = self.produce_ok_at.get(self.p["infantry_type"], -99.0)
        if gi_ptr is not None and len(inf) < self.p["infantry_cap"] and \
                t - self.gi_send_at > self.p["produce_interval_s"] and \
                t - gi_ok > 3.0:
            self.gi_send_at = t
            intents.append({"action": "PRODUCE",
                            "production_type": self.p["infantry_type"],
                            "production_type_ptr": gi_ptr,
                            "production_action": "BEGIN",
                            "object_addresses": []})

        # --- 4) military ---
        fighters = inf[: self.p["max_move_units"]]
        if not fighters:
            return intents
        centroid = (sum(_obj_cell(o)[0] for o in fighters) / len(fighters),
                    sum(_obj_cell(o)[1] for o in fighters) / len(fighters))
        target, dist = self._pick_target(visible, centroid, self.p["attack_radius_cells"])
        if target is not None:
            # visibility hygiene: never order an attack against a hidden object
            if target.get("cell_shrouded") is not False:
                self.violations += 1
                self.note(t, "visibility_violation_skipped", addr=target["addr"])
                return intents
            if t - self.attack_send_at.get(target["addr"], -99.0) > \
                    self.p["attack_reissue_s"]:
                self.attack_send_at[target["addr"]] = t
                intents.append({"action": "ATTACK",
                                "object_addresses": [o["addr"] for o in fighters],
                                "target_address": target["addr"],
                                "target_type": target["type_name"],
                                "coordinates": target["xyz"]})
            return intents

        # advance towards the enemy side when defending is not needed.
        # Direction is derived from the own base position (spawn side varies per
        # game: observed base_y 58 (north) and 126 (south) for the same config),
        # and waypoints advance on a timer as well as on arrival, so one stuck
        # unit cannot deadlock the whole group (regression: ep-0003/0004/0006
        # infantry stayed at y=64..67 for 150-240 s and never met the enemy).
        if t < self.defend_until or t < self.advance_pause_until:
            return intents
        starts = [s for s in ctx.get("public_start_cells", []) if _dist(s, base) > 12]
        if starts:
            # Native pathfinding can route to a known passable map spawn. The
            # old timed y-waypoints could overshoot into cliffs/water, producing
            # successful ACKs followed by idle units halfway across the map.
            goal = min(starts, key=lambda s: _dist(s, centroid))
            if t - self.last_move_at > self.p["advance_interval_s"]:
                self.last_move_at = t
                intents.append({"action": "MOVE", "object_addresses": [o["addr"] for o in fighters],
                                "coordinates": [goal[0] * CELL + CELL // 2,
                                                goal[1] * CELL + CELL // 2]})
            return intents
        if t - self.last_move_at > self.p["advance_interval_s"]:
            map_height = 182
            obs_row = ctx.get("observation") or {}
            m = obs_row.get("map") or {}
            if m.get("height"):
                map_height = int(m["height"])
            margin = max(5, round(map_height * self.p["advance_edge_margin_frac"]))
            min_y, max_y = margin, map_height - margin
            if self.advance_dir is None:
                self.advance_dir = 1 if base[1] < map_height / 2.0 else -1
            if self.advance_goal_y is None:
                self.advance_goal_y = base[1] + self.advance_dir * \
                    self.p["advance_step_cells"]
            else:
                near_goal = any(
                    abs(_obj_cell(o)[1] - self.advance_goal_y) <= 3 for o in fighters)
                self.advance_goal_y += self.advance_dir * (
                    self.p["advance_step_cells"] if near_goal else
                    max(2, self.p["advance_step_cells"] // 2))
            self.advance_goal_y = max(min_y, min(max_y, self.advance_goal_y))
            # at the playable-area inset boundary: stop pushing instead of
            # spamming orders that the native layer rejects as invalid cells
            at_boundary = (self.advance_goal_y <= min_y and self.advance_dir < 0) or \
                          (self.advance_goal_y >= max_y and self.advance_dir > 0)
            if at_boundary and not near_goal:
                return intents
            self.last_move_at = t
            dest_x = round(centroid[0]) * CELL + CELL // 2
            intents.append({"action": "MOVE",
                            "object_addresses": [o["addr"] for o in fighters],
                            "coordinates": [dest_x,
                                            self.advance_goal_y * CELL + CELL // 2]})
        return intents

    # ---- helpers ----
    def _pending_building_any(self, ctx, names):
        """First completed-but-unplaced building whose type is in `names`."""
        factories = ctx.get("factories") or []
        own_by_addr = {o["addr"]: o for o in ctx["own_objects"]}
        for f in factories:
            if not f.get("completed") or not f.get("object_ptr"):
                continue
            obj = own_by_addr.get(f["object_ptr"])
            if obj is None:
                continue
            if obj["type_name"] not in names:
                continue
            if not obj.get("on_map") or obj["xyz"] == [0, 0]:
                return obj
        return None

    @staticmethod
    def _pick_target(visible, centroid, radius_cells):
        best = None
        for e in visible:
            if e.get("cell_shrouded") is not False:
                continue
            d = _dist((e["cell"][0], e["cell"][1]), centroid)
            if d <= radius_cells and (best is None or d < best[1]):
                best = (e, d)
        return (best[0], best[1]) if best else (None, None)
