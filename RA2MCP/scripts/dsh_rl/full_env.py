"""Real-game model-controlled economy + units, with no rule-policy warmup."""
import json
import math
import os

import gymnasium as gym
import numpy as np
from google.protobuf.json_format import MessageToDict

from dsh_rl.env import RA2MicroEnv
from dsh_rl import full_spec as S
from dsh_rl.reward import reward_parts, progress_reward
from dsh_rl.reward_config import load as load_rewards, RewardLedger, CONFIG_PATH
from dsh_rl.truth_tracker import TruthTracker, snapshot_from_state
from dsh_episode.hostility import is_opponent
import features_v2
import spec_v2


def deployment_safe_reward(before, after, kind="running", invalid=0):
    """MCV -> construction yard is a conversion, never a 1000-HP casualty."""
    old_cy = sum(S.is_building(o) and "Construction Yard" in o.get("type_name", "") for o in before.get("own_objects", []))
    new_cy = [o for o in after.get("own_objects", []) if S.is_building(o) and "Construction Yard" in o.get("type_name", "")]
    current = {o["addr"] for o in after.get("own_objects", [])}
    if len(new_cy) > old_cy:
        own = [o for o in before.get("own_objects", []) if not
               (S.is_mcv(o) and o["addr"] not in current
                and any(math.dist(o["cell"], b["cell"]) < 4 for b in new_cy))]
        before = dict(before, own_objects=own)
    return reward_parts(before, after, kind, invalid)


# Mobile combat/utility units only: buildings, the MCV and ore miners are not
# evidence that the army is advancing, and miners would farm the advance bonus
# by driving to ore fields instead of to the enemy.
_NON_COMBAT_HINTS = ("Construction Vehicle", "Miner", "Harvester", "Chrono Miner")


def combat_units(row):
    result = []
    for o in row.get("own_objects", []):
        if not S.placed(o) or S.is_building(o) or S.is_mcv(o):
            continue
        name = o.get("type_name", "")
        if any(hint in name for hint in _NON_COMBAT_HINTS):
            continue
        result.append(o)
    return result


def advance_potential(row, starts):
    """Negative distance from our mobile units' centre to the enemy spawn.

    Public map spawns are used, never hidden live enemy positions. With no
    mobile units there is nothing to push, so the potential is undefined and the
    advance term contributes exactly zero.
    """
    units = combat_units(row)
    goal = S.enemy_start(row, starts)
    if not units or goal is None:
        return None
    center = [sum(o["cell"][i] for o in units) / len(units) for i in (0, 1)]
    return -min(200.0, math.dist(center, goal))


def advance_delta(before, after, starts):
    if not starts:
        return 0.0
    old, new = advance_potential(before, starts), advance_potential(after, starts)
    if old is None or new is None:
        return 0.0
    return new - old


def per_unit_advance_delta(before, after, starts):
    """Mean per-unit change of "distance to the public enemy spawn" (cells).

    V4 changed the advance term from the centre of mass of the whole army to
    per-unit potentials: producing a unit at the base or losing a unit at the
    front no longer moves the measured centre and must not create negative
    "advance" (the v3 centre-of-mass term was noisy, audit section 1/Q4).
    Only identities present in both frames contribute, so spawns/deaths are
    neutral.  The returned value is a per-unit average (matches the previous
    reward scale: 0.1 points per cell, step cap 0.3).
    """
    if not starts:
        return 0.0
    goal = S.enemy_start(before, starts) or S.enemy_start(after, starts)
    if goal is None:
        return 0.0

    def potentials(row):
        out = {}
        for o in combat_units(row):
            key = (o["addr"], o.get("type_name"), o.get("owner"))
            out[key] = -min(200.0, math.dist(o["cell"], goal))
        return out

    old, new = potentials(before), potentials(after)
    deltas = [new[key] - old[key] for key in new if key in old]
    if not deltas:
        return 0.0
    return sum(deltas) / len(deltas)


def enemy_base_contact(row, starts, radius=25.0, min_units=2):
    units = combat_units(row)
    goal = S.enemy_start(row, starts)
    if len(units) < min_units or goal is None:
        return False
    center = [sum(o["cell"][i] for o in units) / len(units) for i in (0, 1)]
    return math.dist(center, goal) <= radius


def enemy_building_sight(row):
    return any(S.is_building(o) for o in row.get("visible_enemies", []) if is_opponent(o))


class RA2FullEnv(RA2MicroEnv):
    reward_version = "full_configurable_v4"

    def __init__(self, *args, **kwargs):
        self.full = None
        # P2.0 action-space version / target candidate radius.  A trainer or a
        # caller may override these through DSH_ACTION_SPACE_VERSION /
        # DSH_TARGET_CANDIDATE_RANGE; they are recorded in run metadata and in
        # the action contract so a checkpoint is never resumed across versions
        # without an explicit opt-in (see train_shared --allow-action-space-change).
        self.action_space_version = S.ACTION_SPACE_VERSION
        self.target_candidate_range = S.DEFAULT_TARGET_CANDIDATE_RANGE
        self.target_nearest_k = S.DEFAULT_TARGET_NEAREST_K
        # V4 reward-only ground-truth tracker (never part of the observation).
        self.truth = None
        self._truth_step = {}
        self._pending_attacks = []
        self.attacks_effective = 0
        self.economy_invalid_orders = 0
        # Per-episode counters (reset again in _warmup_controller).
        self.prev_unit_count = 0
        self.attack_issued = 0
        self.last_attack_issued = 0
        self.first_damage_done = False
        self.first_kill_done = False
        self.first_building_destroy_done = False
        self.last_action = None
        self.advance_seen = False
        self.enemy_sight_seen = False
        super().__init__(*args, **kwargs)
        self.action_space = gym.spaces.Discrete(S.ACTION_N)
        self.observation_space = gym.spaces.Box(
            -5.0, 5.0, (spec_v2.OBS_DIM + S.EXTRA_DIM + S.HISTORY_DIM,), np.float32)

    def _initialize_controller(self, initial, catalog):
        self.reward_ledger = RewardLedger(load_rewards())
        self._publish_reward_config(CONFIG_PATH)
        types = [MessageToDict(t, preserving_proto_field_name=True, including_default_value_fields=True)
                 for t in initial.object_types]
        groups = MessageToDict(initial.prerequisite_groups, preserving_proto_field_name=True,
                               including_default_value_fields=True)
        public_catalog = {"country_index": int(self.hero.type_array_index), "tech_level": int(initial.tech_level),
                          "groups": groups, "types": types}
        (self.out_dir.parent / "public-catalog.json").write_text(json.dumps(public_catalog, indent=2), encoding="utf-8")
        # P2.0: action space version / target-candidate radius are configuration
        # of the *action mapping*, not of the reward (V4 reward values untouched).
        version = os.environ.get("DSH_ACTION_SPACE_VERSION", S.ACTION_SPACE_VERSION).strip()
        if version not in S.ACTION_SPACE_VERSIONS:
            raise ValueError(f"DSH_ACTION_SPACE_VERSION must be one of {S.ACTION_SPACE_VERSIONS}")
        candidate_range = float(os.environ.get("DSH_TARGET_CANDIDATE_RANGE",
                                               S.DEFAULT_TARGET_CANDIDATE_RANGE))
        nearest_k = int(os.environ.get("DSH_TARGET_NEAREST_K", S.DEFAULT_TARGET_NEAREST_K))
        self.action_space_version = version
        self.target_candidate_range = candidate_range
        self.target_nearest_k = nearest_k
        self.full = S.FullControlSpec(types, groups, int(self.hero.type_array_index),
                                     int(initial.tech_level) if initial.tech_level > 0 else 10,
                                     action_space_version=version,
                                     target_candidate_range=candidate_range,
                                     target_nearest_k=nearest_k)
        self.seen_buildings, self.seen_units = set(), set()
        self.income_reward = 0.0
        # V4: reward-only truth tracker.  It is fed the raw GetGameState reply
        # (all houses, hidden objects included) but its events are never stored
        # in the observation or per-object in the episode JSONL.
        self.max_hp = {str(t.get("name")): int(t.get("strength") or 0)
                       for t in types if t.get("name") and t.get("strength")}
        self.truth = TruthTracker(max_hp=self.max_hp, hero_owner=self._hero_id,
                                  confirm_frames=2)
        self._truth_step = {}
        self._pending_attacks = []
        self.attacks_effective = 0
        self.first_kill_done = False
        self.first_building_destroy_done = False
        self.economy_invalid_orders = 0
        manifest = self.full.manifest()
        if getattr(self, "expected_catalog_hash", manifest["catalog_sha256"]) != manifest["catalog_sha256"]:
            raise RuntimeError("saved model production catalog does not match this game")
        contract = self.out_dir.parent / "action-contract.json"
        if contract.exists():
            old = json.loads(contract.read_text(encoding="utf-8"))
            if old["catalog_sha256"] != manifest["catalog_sha256"]:
                raise RuntimeError("production catalog changed; refusing to reuse model action semantics")
        else:
            contract.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _publish_reward_config(self, config_path):
        snapshot_dir = self.out_dir.parent / 'reward-configs'
        snapshot_dir.mkdir(exist_ok=True)
        (snapshot_dir / (self.reward_ledger.revision + '.json')).write_text(
            json.dumps(self.reward_ledger.config, indent=2), encoding='utf-8')
        active = {'revision': self.reward_ledger.revision, 'config_path': str(config_path),
                  'episode_number': self.episode_count, 'config': self.reward_ledger.config}
        temporary = self.out_dir.parent / 'active-rewards.json.tmp'
        temporary.write_text(json.dumps(active, indent=2), encoding='utf-8')
        temporary.replace(self.out_dir.parent / 'active-rewards.json')

    def _control_metadata(self):
        return "maskable_ppo_full", {"fixed_economy": False, "warmup": False,
                                     "reward_version": self.reward_version,
                                     "action_space_version": self.full.action_space_version,
                                     "target_candidate_range": self.full.target_candidate_range,
                                     "target_nearest_k": self.full.target_nearest_k,
                                     "unit_slots_stable": self.full.action_space_version != S.LEGACY_ACTION_SPACE_VERSION,
                                     "reward_revision": self.reward_ledger.revision,
                                     "reward_config": self.reward_ledger.config,
                                     "action_contract": self.full.manifest()}

    def _filter_observation(self, row):
        # Production visibility must not expose the computer's hidden factories.
        own = int(self.hero.self)
        row["factories"] = [f for f in row["factories"] if f.get("owner_ptr") == own]
        row["houses"] = [h for h in row["houses"] if h.get("ptr") == own]
        return row

    async def _warmup_controller(self):
        # Learning starts at the initial MCV. There are no automatic commands.
        self.seen_units = {o["type_name"] for o in self.current["own_objects"] if not S.is_building(o)}
        self.seen_buildings = {o["type_name"] for o in self.current["own_objects"] if S.is_building(o) and S.placed(o)}
        self.prev_unit_count = sum(1 for o in self.current["own_objects"]
                                   if not S.is_building(o) and S.placed(o) and not S.is_mcv(o))
        self.attack_issued = 0
        self.last_attack_issued = 0
        self.first_damage_done = False
        self.first_kill_done = False
        self.first_building_destroy_done = False
        self.last_action = None
        self.advance_seen = False
        self.enemy_sight_seen = False
        self.attacks_effective = 0
        self.economy_invalid_orders = 0
        self._truth_step = {}
        self._pending_attacks = []
        if self.truth is not None:
            # Warm-up frames are not reward-bearing: start the tracker after it
            # so the first decision step begins from a clean object table.
            self.truth.reset()

    async def _economy(self):
        return 0

    def _is_noop(self, action):
        return action == 0

    def action_masks(self):
        if self.full is None or not hasattr(self, "current"):
            mask = np.zeros(S.ACTION_N, dtype=bool)
            mask[0] = True
            return mask
        return self.full.masks(self.current, self.starts, self.map_soa)

    def _history_features(self):
        out = [0.0] * S.HISTORY_DIM
        if self.last_action is None:
            return out
        action = int(self.last_action)
        if action == 0 or action == 1 or S.PRODUCE_START <= action < S.UNIT_START:
            out[4] = 1.0  # idle / deploy / build / place
            return out
        _, command = divmod(action - S.UNIT_START, S.UNIT_ACTIONS)
        if command == 0:
            out[3] = 1.0
        elif 1 <= command <= len(S.DIRECTIONS):
            out[0] = 1.0
            out[5 + command - 1] = 1.0
        elif 9 <= command < 9 + S.TARGET_SLOTS:
            out[2] = 1.0
        else:
            out[1] = 1.0  # scout / retreat are long-range legs
        return out

    def _features(self):
        features_v2.validate_visibility(self.current)
        base = super()._features()
        extra = self.full.features(self.current, self.starts, self.map_soa)
        x = np.concatenate([base, extra, np.asarray(self._history_features(), dtype=np.float32)]).astype(np.float32)
        if not np.isfinite(x).all() or x.shape != self.observation_space.shape:
            raise RuntimeError("full-control observation contract violated")
        return np.clip(x, -5.0, 5.0)

    def _select_intent(self, action):
        if not self.action_masks()[action]:
            raise ValueError("masked action selected; use MaskablePPO and supply action_masks in evaluation")
        self.last_action = int(action)
        return self.full.intent(action, self.current, self.starts, self.map_soa)

    async def _commands(self, intents, source):
        if source == "ppo":
            attacks = [intent for intent in (intents or [])
                       if intent.get("action") == "ATTACK"]
            self.attack_issued += len(attacks)
            now = float(self.current.get("t") or 0)
            for intent in attacks:
                self._pending_attacks.append({
                    "target": intent.get("target_address"), "t": now,
                    "consumed": False})
        invalid = await super()._commands(intents, source)
        for ack, intent in zip(self.last_acks, intents):
            if intent["action"] == "PRODUCE":
                slot = self.full.slot_by_ptr[intent["production_type_ptr"]]
                if ack.get("error") and "unbuildable" in ack["error"]:
                    self.full.failed[slot] = self.full._signature(self.current)
                elif not ack.get("error"):
                    self.full.sent_until[slot] = self.current["t"] + 3.0
        return invalid

    ATTACK_MATCH_WINDOW = 3.0

    def _begin_transition(self):
        """Start a new PPO decision step (never called during warm-up)."""
        self._truth_step = {}
        now = float(self.current.get("t") or 0)
        self._pending_attacks = [
            p for p in self._pending_attacks
            if not p["consumed"] and now - p["t"] <= self.ATTACK_MATCH_WINDOW]

    async def _capture(self, state=None):
        row = await super()._capture(state)
        if self.truth is not None and getattr(self, "state", None) is not None:
            records = snapshot_from_state(self.state, self.type_names)
            houses = {int(h.self): h.name for h in self.state.houses}
            self._merge_truth(self.truth.observe(records, houses))
        return row

    def _merge_truth(self, events):
        """Accumulate one capture's truth events into the current decision step."""
        if not events:
            return
        step = self._truth_step
        for key in ("enemy_damage_hp", "own_damage_hp", "own_removed_hp",
                    "kills_infantry", "kills_vehicle", "building_destroyed",
                    "killed_hp_credit"):
            value = float(events.get(key) or 0.0)
            if value:
                step[key] = step.get(key, 0.0) + value
        now = float(self.current.get("t") or 0)
        for addr, _delta in events.get("enemy_damage_addrs") or []:
            for pending in self._pending_attacks:
                if pending["consumed"] or pending["target"] != addr:
                    continue
                if now - pending["t"] <= self.ATTACK_MATCH_WINDOW:
                    pending["consumed"] = True
                    step["effective_attacks"] = step.get("effective_attacks", 0) + 1
                    self.attacks_effective += 1
                    break
        self._pending_attacks = [
            p for p in self._pending_attacks
            if not p["consumed"] and now - p["t"] <= self.ATTACK_MATCH_WINDOW]

    def _tick_rewards(self, before, after, invalid):
        # V3 HP-delta parts are kept for the legacy advance/first-milestone
        # bookkeeping; the reward quantities themselves come from the V4 truth
        # tracker in _complete_reward_parts.
        return deployment_safe_reward(before, after, self.kind, invalid)

    def _complete_reward_parts(self, before, after, parts):
        # --- production/economy milestones (stage-B quantities, unchanged) ---
        building_types = {o["type_name"] for o in after["own_objects"] if S.is_building(o) and S.placed(o)}
        unit_types = {o["type_name"] for o in after["own_objects"] if not S.is_building(o) and S.placed(o)}
        first_buildings = building_types - self.seen_buildings
        first_units = unit_types - self.seen_units
        yards = sum('Construction Yard' in name for name in first_buildings)
        self.seen_buildings.update(building_types)
        self.seen_units.update(unit_types)
        mapped = int((after.get('map') or {}).get('mapped', 0))
        placed_units = [o for o in after['own_objects']
                        if not S.is_building(o) and S.placed(o) and not S.is_mcv(o)]
        unit_count = len(placed_units)
        unit_delta = max(0, unit_count - self.prev_unit_count)
        self.prev_unit_count = unit_count
        self.last_attack_issued = self.attack_issued

        # --- V4 truth-derived quantities (reward-only ground truth) ---
        truth = self._truth_step or {}
        enemy_hp = float(truth.get("enemy_damage_hp", 0.0))
        own_hp = (float(truth.get("own_damage_hp", 0.0))
                  + float(truth.get("own_removed_hp", 0.0)))
        kills_inf = int(truth.get("kills_infantry", 0))
        kills_veh = int(truth.get("kills_vehicle", 0))
        building_destroyed = int(truth.get("building_destroyed", 0))
        killed_hp = float(truth.get("killed_hp_credit", 0.0))
        effective_attacks = int(truth.get("effective_attacks", 0))

        first_damage = 0
        if enemy_hp > 0 and not self.first_damage_done:
            self.first_damage_done = True
            first_damage = 1
        first_kill = 0
        if (kills_inf + kills_veh) > 0 and not self.first_kill_done:
            self.first_kill_done = True
            first_kill = 1
        first_building = 0
        if building_destroyed > 0 and not self.first_building_destroy_done:
            self.first_building_destroy_done = True
            first_building = 1

        advance = 0.0 if self.kind != 'running' else per_unit_advance_delta(
            before, after, self.starts)
        contact = self.kind == 'running' and enemy_base_contact(after, self.starts)
        first_advance = 1 if contact and not self.advance_seen else 0
        if contact:
            self.advance_seen = True
        sight = self.kind == 'running' and enemy_building_sight(after)
        first_sight = 1 if sight and not self.enemy_sight_seen else 0
        if sight:
            self.enemy_sight_seen = True

        quantities = {
            'opponent_hp': enemy_hp,
            'killed_hp_credit': killed_hp,
            'enemy_kill_infantry': kills_inf,
            'enemy_kill_vehicle': kills_veh,
            'enemy_building_destroy': building_destroyed,
            'first_kill': first_kill,
            'first_building_destroy': first_building,
            'own_hp': own_hp,
            'invalid': -parts['invalid'] * 10,
            'victory': int(self.kind == 'win'),
            'defeat': int(self.kind == 'defeat'),
            'timeout': int(self.kind == 'timeout'),
            'time': max(0., after['t'] - before['t']),
            'progress': progress_reward(before, after, self.starts, self.kind) / .1,
            'exploration': max(0, mapped - self.max_mapped),
            'construction_yard': yards,
            'first_building_type': len(first_buildings) - yards,
            'first_unit_type': len(first_units),
            'unit_produced': unit_delta,
            'attack': effective_attacks,
            'first_damage': first_damage,
            'income': max(0, after['money'] - before['money']),
            'advance': advance,
            'first_advance': first_advance,
            'first_enemy_sight': first_sight,
        }
        self.max_mapped = max(self.max_mapped, mapped)
        return self.reward_ledger.apply(quantities)

    def _result_metrics_extra(self):
        """Aggregate truth-tracker summary (counts only; never hidden positions)."""
        totals = dict(getattr(self.truth, "totals", {}) or {})
        return {
            "truth_damage_events": int(len(totals.get("enemy_damage_addrs") or [])),
            "truth_kills_infantry": int(totals.get("kills_infantry", 0)),
            "truth_kills_vehicle": int(totals.get("kills_vehicle", 0)),
            "truth_building_destroyed": int(totals.get("building_destroyed", 0)),
            "truth_killed_hp": round(float(totals.get("killed_hp_credit", 0.0)), 1),
            "truth_own_removed_hp": round(float(totals.get("own_removed_hp", 0.0)), 1),
            "truth_identity_changes": int(totals.get("identity_changes", 0)),
            "truth_sold_like_removals": int(totals.get("sold_like_removals", 0)),
            "truth_mcv_removals": int(totals.get("mcv_removals", 0)),
            "truth_limbo_transitions": int(totals.get("limbo_transitions", 0)),
            "truth_unidentified_removals": int(totals.get("unidentified_removals", 0)),
            "attacks_issued": int(self.attack_issued),
            "attacks_effective": int(self.attacks_effective),
            "economy_invalid_orders": int(self.economy_invalid_orders),
        }


    def _action_details(self, action):
        selected = self.full.intent(action, self.current, self.starts, self.map_soa)
        try:
            stats = self.full.target_stats(self.current)
        except Exception:  # noqa: BLE001 - diagnostics must never break a step
            stats = {}
        return {"control_mode": "full", "action_contract": S.VERSION,
                "action_space_version": self.full.action_space_version,
                "target_candidate_range": self.full.target_candidate_range,
                "target_slots_visible": stats.get("visible_valid"),
                "target_candidates": stats.get("candidates"),
                "target_nearest_enemy_dist": stats.get("nearest_enemy_dist"),
                "target_candidate_nearest_dist": stats.get("candidate_nearest_dist"),
                "unit_slot_swaps": self.full.unit_slot_diagnostics()["unit_slot_swaps"],
                "reward_revision": self.reward_ledger.revision,
                "selected_intent": selected, "valid_action_count": int(self.action_masks().sum()),
                "observation_frame": self.current["frame"]}
