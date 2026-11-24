"""Skeleton scripted teacher distilled from the parsed RA2/YR AI scripts.

The rules come from `reports/ai-teacher/scripts-parse/ai-script-reference.md`:
  * build priorities from Extreme-AI.ini `[AI]` section (const/power/refinery/barracks/factory)
  * unit compositions from TaskForces (infantry squads + tanks)
  * behaviour sequences from ScriptTypes (regroup -> gather -> attack target)
  * attack timing approximated by an army-size threshold
This module is deliberately small: it is the "teacher" that produces the first
(state, action) demonstrations for behaviour cloning.
"""
import math
import re

from dsh_rl import full_spec as S

POWER = re.compile(r"power plant|tesla reactor|bio reactor|nuclear reactor", re.I)
REFINERY = re.compile(r"ore refinery|ore processor", re.I)
BARRACKS = re.compile(r"barracks", re.I)
WARFACTORY = re.compile(r"war factory", re.I)
DEFENSE = re.compile(r"pillbox|pill box|sentry gun|flak cannon|battle bunker|tank bunker|"
                     r"patriot|tesla coil|gatling|cannon|prism tower", re.I)
INFANTRY = re.compile(r"\bGI\b|Conscript|Initiate|Attack Dog|Flak Trooper|Guardian GI|"
                      r"Shock Trooper|Engineer|Tesla Trooper|Desolator|Brute|Virus|Yuri|"
                      r"Tanya|Boris|SEAL|Rocketeer|Jumpjet|Lasher", re.I)
VEHICLE = re.compile(r"\bTank\b|IFV|Grizzly|Rhino|Lasher|Prism Tank|Mirage|Apocalypse|"
                     r"Tesla Tank|Flak Track|\bV3\b|Terror Drone", re.I)
NON_COMBAT = re.compile(r"Construction Vehicle|Miner|Harvester|Chrono Miner", re.I)
MAX_ARMY = 10

# (name, pattern, kind, target_count) in build order. Extreme AI [AI] section order.
BUILD_PLAN = [
    ("power-1", POWER, "building", 1),
    ("refinery-1", REFINERY, "building", 1),
    ("barracks-1", BARRACKS, "building", 1),
    ("power-2", POWER, "building", 2),
    ("refinery-2", REFINERY, "building", 2),
    ("war-factory-1", WARFACTORY, "building", 1),
    ("defense-1", DEFENSE, "building", 1),
    ("infantry-1", INFANTRY, "infantry", 4),
    ("defense-2", DEFENSE, "building", 2),
    ("infantry-2", INFANTRY, "infantry", 6),
    ("vehicle-1", VEHICLE, "unit", 2),
]

# ---------------------------------------------------------------------------
# 「上帝的玩具」盟军风格教师（2026-10-30）
# 依据：reports/ai-teacher/wdzs-godstoy/ai_data.json（132 任务部队 / 88 脚本）
# 与 match-01-report.md 实测（盟军 AI：90s 时 38 GI + 38 Guardian GI，随后
# Prism/Mirage/IFV；进攻波次在 87–134s 派约 10 单位）。只保留标准 YR 里可生产
# 的盟军类型，因此不照搬苏军/尤里阵容。
# ---------------------------------------------------------------------------
GUARD_INFANTRY = re.compile(r"\bGI\b|Guardian GI", re.I)
GODSTOY_BUILD_PLAN = [
    ("power-1", POWER, "building", 1),
    ("refinery-1", REFINERY, "building", 1),
    ("barracks-1", BARRACKS, "building", 1),
    ("barracks-2", BARRACKS, "building", 2),
    ("power-2", POWER, "building", 2),
    ("refinery-2", REFINERY, "building", 2),
    ("war-factory-1", WARFACTORY, "building", 1),
    ("defense-1", DEFENSE, "building", 1),
    ("infantry-1", GUARD_INFANTRY, "infantry", 8),
    ("infantry-2", GUARD_INFANTRY, "infantry", 14),
    ("vehicle-1", VEHICLE, "unit", 4),
    ("defense-2", DEFENSE, "building", 2),
    ("vehicle-2", VEHICLE, "unit", 8),
]
GODSTOY_MAX_ARMY = 18
GODSTOY_MIN_ATTACK_ARMY = 6
TEACHER_VARIANTS = ("extreme", "godstoy")


def _matches(pattern, text):
    return bool(pattern.search(text or ""))


class ScriptedTeacher:
    """A deterministic macro policy over the 301-action full-control contract."""

    def __init__(self, variant="extreme"):
        if variant not in TEACHER_VARIANTS:
            raise ValueError(f"unknown teacher variant: {variant}")
        self.variant = variant
        if variant == "godstoy":
            self.build_plan = GODSTOY_BUILD_PLAN
            self.max_army = GODSTOY_MAX_ARMY
            self.min_attack_army = GODSTOY_MIN_ATTACK_ARMY
        else:
            self.build_plan = BUILD_PLAN
            self.max_army = MAX_ARMY
            self.min_attack_army = 3
        self.step = 0
        self.place_rotation = 0
        self.unit_rotation = 0
        self.target_rotation = 0

    # ---- state helpers -------------------------------------------------
    @staticmethod
    def counts(row):
        own = row.get("own_objects", [])
        placed = [o for o in own if S.placed(o)]
        buildings = [o for o in placed if S.is_building(o)]
        units = [o for o in placed if not S.is_building(o)
                 and not S.is_mcv(o) and not NON_COMBAT.search(o.get("type_name", ""))]
        by = {"building": buildings, "infantry": [], "unit": []}
        for o in units:
            abstract = o.get("abstract", "")
            if abstract == "ABSTRACT_TYPE_INFANTRY":
                by["infantry"].append(o)
            else:
                by["unit"].append(o)
        return by

    def _produce_action(self, full, mask, patterns, kind):
        wanted = S.KINDS[0] if kind == "building" else (
            S.KINDS[1] if kind == "infantry" else S.KINDS[2])
        for pattern in patterns:
            for slot, t in enumerate(full.types):
                if t.get("type") != wanted or not _matches(pattern, t.get("name", "")):
                    continue
                action = S.PRODUCE_START + slot
                if mask[action]:
                    return action
        return None

    # ---- main decision -------------------------------------------------
    def choose(self, env):
        self.step += 1
        row, full, starts = env.current, env.full, env.starts
        mask = env.action_masks()
        by = self.counts(row)

        # 1) deploy the initial MCV
        if S.base_cell(row) is None:
            return (1, "deploy") if mask[1] else (0, "noop")

        # 2) place finished buildings (rotate direction so a blocked sector retries)
        if S.pending(row):
            for offset in range(len(S.DIRECTIONS)):
                direction = (self.place_rotation + offset) % len(S.DIRECTIONS)
                action = S.PLACE_START + direction
                if mask[action]:
                    self.place_rotation = (direction + 1) % len(S.DIRECTIONS)
                    return action, "place"

        # 3) macro build plan (one order per 0.5 s decision)
        total_army = len(by["infantry"]) + len(by["unit"])
        for _name, pattern, kind, target in self.build_plan:
            if kind == "building":
                current = sum(1 for o in by["building"] if _matches(pattern, o.get("type_name", "")))
            else:
                if total_army >= self.max_army:
                    continue
                current = sum(1 for o in by[kind] if _matches(pattern, o.get("type_name", "")))
            if current >= target:
                continue
            action = self._produce_action(full, mask, [pattern], kind)
            if action is not None:
                return action, "produce"

        # 4) army behaviour: every other step push a combat unit toward the enemy
        if self.step % 2 == 0:
            action = self._army_action(row, starts, mask)
            if action is not None:
                return action
        return 0, "noop"

    def _army_action(self, row, starts, mask):
        units = S.all_units(row)
        combat = [i for i, o in enumerate(units)
                  if i < S.UNIT_SLOTS and not S.is_mcv(o)
                  and not NON_COMBAT.search(o.get("type_name", ""))
                  and not S.is_building(o)]
        if len(combat) < self.min_attack_army:
            return None

        # 「上帝的玩具」风格：兵够了就整队（selection=0）按波次集火进攻，
        # 目标槽轮换，产生成规模的 attack 示范而不是零散单点指令。
        if self.variant == "godstoy":
            group_base = S.UNIT_START
            for offset in range(S.TARGET_SLOTS):
                slot = (self.target_rotation + offset) % S.TARGET_SLOTS
                action = group_base + 9 + slot
                if action < S.ACTION_N and mask[action]:
                    self.target_rotation = (slot + 1) % S.TARGET_SLOTS
                    return action, "attack"

        index = combat[self.unit_rotation % len(combat)]
        self.unit_rotation += 1
        # action contract only exposes selections 0..UNIT_SLOTS (1-based slices)
        selection = min(index + 1, S.UNIT_SLOTS)
        base_action = S.UNIT_START + selection * S.UNIT_ACTIONS

        # attack the closest visible enemy when the mask allows it
        attack = base_action + 9
        if attack < S.ACTION_N and mask[attack]:
            return attack, "attack"

        goal = S.enemy_start(row, starts)
        if goal is None:
            return None
        cell = units[index]["cell"]
        dx, dy = goal[0] - cell[0], goal[1] - cell[1]
        norm = math.hypot(dx, dy) or 1.0
        best, best_dot = 0, -2.0
        for i, (vx, vy) in enumerate(S.DIRECTIONS):
            dot = (dx * vx + dy * vy) / (norm * math.hypot(vx, vy))
            if dot > best_dot:
                best, best_dot = i, dot
        move = base_action + 1 + best
        if mask[move]:
            return move, "move"
        return None


def intent_label(action):
    """Collapse the 301 actions into 8 macro intents (BC classes)."""
    if action == 0:
        return "noop"
    if action == 1:
        return "deploy"
    if S.PRODUCE_START <= action < S.PLACE_START:
        return "produce"
    if S.PLACE_START <= action < S.UNIT_START:
        return "place"
    if action >= S.UNIT_START:
        _, command = divmod(action - S.UNIT_START, S.UNIT_ACTIONS)
        if command == 0:
            return "stop"
        if 1 <= command <= len(S.DIRECTIONS):
            return "move"
        if 9 <= command < 9 + S.TARGET_SLOTS:
            return "attack"
        return "scout"
    return "noop"


LABELS = ["noop", "deploy", "produce", "place", "move", "attack", "stop", "scout"]
LABEL_TO_INDEX = {name: i for i, name in enumerate(LABELS)}
