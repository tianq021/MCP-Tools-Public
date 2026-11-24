"""Feature/action specification v2 — versioned replacement for spec.py (v1 kept).

v1 problems this fixes (see reports/dsh-training-data-review.md):
  * ATTACK lost its target (only "ATTACK_NEAREST" class)            -> target_slot
  * MOVE was collapsed to 4 compass directions                      -> dx/dy regression
  * PRODUCE split into building/infantry classes, losing the type   -> class PRODUCE + type id
  * missing economy/power/queue/unit-type/passability info          -> added blocks

Interfaces (all versioned so old data/models keep working):
  FEATURE_VERSION = "v2"
  Action = multi-head label:
      class_id        : 8 classes (NOOP/MOVE/ATTACK/STOP/RETREAT/DEPLOY/PRODUCE/PLACE)
      target_slot     : index into the observation's visible-enemy slots (-1 = none)
      move_dx, move_dy: continuous direction offset in cells (leptons / CELL / 30)
      produce_type_id : id from the dataset's type catalog (-1 = none)
      place_dx, place_dy: continuous cell offset from the construction yard anchor
    The raw action rows stay in the episode JSONL untouched; these heads are a
    lossless-enough view derived at dataset build time.
"""
CELL = 256
FEATURE_VERSION = "v2"
ACTION_SPACE_VERSION = "a2"

SLOTS_OWN = 10
SLOTS_ENEMY = 8
SLOTS_FACTORY = 4
MAP_PATCH_SIZE = 5          # 5x5 local grid from episode map_patch (mapped + passability)

# ---------------------------------------------------------------- observations --
GLOBAL_NAMES_V2 = [
    "money_norm", "power_output_norm", "power_drain_norm", "power_ratio",
    "buildings_n", "units_n", "infantry_n", "visible_enemies_n",
    "frame_norm", "mapped_ratio", "base_x", "base_y", "centroid_x", "centroid_y",
    "has_cy", "own_hp_mean", "own_hp_min", "enemy_hp_mean",
    "factory_active", "factory_completed", "factory_queued_n",
    "defensive_trigger",       # own building damaged recently (policy-agnostic flag)
    "map_width_norm", "map_height_norm",
]

OWN_NAMES_V2 = [
    "present", "dx", "dy", "hp", "is_building", "is_unit", "is_infantry",
    "in_limbo", "on_map", "mission_attack", "mission_move", "mission_guard",
    "mission_construction", "type_known", "cell_mapped", "armor_hint",
]

ENEMY_NAMES_V2 = [
    "present", "dx", "dy", "hp", "is_building", "is_unit", "is_infantry",
    "is_neutral", "cloak_norm", "cell_mapped", "threat_score", "type_known",
]

FACTORY_NAMES_V2 = ["present", "progress_norm", "completed", "queued_n", "is_own"]

MAP_PATCH_FEATURES = ["mapped", "passability_clear", "shrouded"]

GLOBAL_DIM = len(GLOBAL_NAMES_V2)
OWN_PER = len(OWN_NAMES_V2)
ENEMY_PER = len(ENEMY_NAMES_V2)
FACTORY_PER = len(FACTORY_NAMES_V2)
PATCH_DIM = MAP_PATCH_SIZE * MAP_PATCH_SIZE * len(MAP_PATCH_FEATURES)
OWN_DIM = SLOTS_OWN * OWN_PER
ENEMY_DIM = SLOTS_ENEMY * ENEMY_PER
FACTORY_DIM = SLOTS_FACTORY * FACTORY_PER
OBS_DIM = GLOBAL_DIM + OWN_DIM + ENEMY_DIM + FACTORY_DIM + PATCH_DIM

# ------------------------------------------------------------------- actions ---
ACTION_CLASSES = [
    "NOOP", "MOVE", "ATTACK", "STOP", "RETREAT", "DEPLOY", "PRODUCE", "PLACE",
]
ACTION_CLASS_IDS = {name: i for i, name in enumerate(ACTION_CLASSES)}
N_ACTION_CLASSES = len(ACTION_CLASSES)

TARGET_NONE = -1
TYPE_ID_NONE = -1


def action_label(raw_action, obs_row, type_id_map, slots_enemy):
    """Convert one raw action row into a v2 multi-head label.

    Returns dict or None if the action must be dropped (e.g. targets something
    not present in the paired observation — such a sample would teach cheating).
    """
    a = str(raw_action.get("action", "")).upper()
    label = {
        "class_id": ACTION_CLASS_IDS.get("NOOP", 0),
        "target_slot": TARGET_NONE,
        "move_dx": 0.0, "move_dy": 0.0,
        "produce_type_id": TYPE_ID_NONE,
        "place_dx": 0.0, "place_dy": 0.0,
    }
    if a == "MOVE":
        label["class_id"] = ACTION_CLASS_IDS["MOVE"]
        coords = raw_action.get("coordinates")
        cx, cy = _centroid_cell(obs_row)
        if coords and cx is not None:
            label["move_dx"] = _clip((int(coords[0]) / CELL - cx) / 30.0)
            label["move_dy"] = _clip((int(coords[1]) / CELL - cy) / 30.0)
        return label
    if a == "ATTACK":
        label["class_id"] = ACTION_CLASS_IDS["ATTACK"]
        target = int(raw_action.get("target_address") or 0)
        enemies = [e for e in (obs_row.get("visible_enemies") or [])
                   if str(e.get("owner") or "").casefold() not in
                   ("neutral", "special", "civilian", "?")]
        cx, cy = _centroid_cell(obs_row)
        enemies = sorted(enemies, key=lambda e: (_obj_cell(e)[0] - (cx or 0)) ** 2
                         + (_obj_cell(e)[1] - (cy or 0)) ** 2)
        slot = next((i for i, e in enumerate(enemies)
                     if int(e.get("addr", -1)) == target), None)
        if slot is None or slot >= slots_enemy:
            return None                      # invisible / out-of-slot target: drop
        label["target_slot"] = slot
        return label
    if a == "RETREAT":
        label["class_id"] = ACTION_CLASS_IDS["RETREAT"]
        return label
    if a == "DEPLOY":
        label["class_id"] = ACTION_CLASS_IDS["DEPLOY"]
        return label
    if a == "STOP":
        label["class_id"] = ACTION_CLASS_IDS["STOP"]
        return label
    if a == "PRODUCE":
        label["class_id"] = ACTION_CLASS_IDS["PRODUCE"]
        name = raw_action.get("production_type")
        label["produce_type_id"] = int(type_id_map.get(name, TYPE_ID_NONE))
        return label
    if a == "PLACE":
        label["class_id"] = ACTION_CLASS_IDS["PLACE"]
        coords = raw_action.get("coordinates")
        base = _base_cell(obs_row)
        if coords and base is not None:
            label["place_dx"] = _clip((int(coords[0]) / CELL - base[0]) / 10.0)
            label["place_dy"] = _clip((int(coords[1]) / CELL - base[1]) / 10.0)
        return label
    return None


# ------------------------------------------------------------------- helpers ---
def _clip(v, lo=-5.0, hi=5.0):
    return max(lo, min(hi, float(v)))


def _obj_cell(o):
    cell = o.get("cell") or [0, 0]
    return (float(cell[0]), float(cell[1]))


def _base_cell(obs_row):
    for o in obs_row.get("own_objects") or []:
        if o.get("type_name") == "Allied Construction Yard":
            return _obj_cell(o)
    return None


def _centroid_cell(obs_row):
    own = obs_row.get("own_objects") or []
    if not own:
        return (None, None)
    pts = [_obj_cell(o) for o in own]
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def describe():
    lines = [f"FEATURE_VERSION={FEATURE_VERSION} ACTION_SPACE={ACTION_SPACE_VERSION} "
             f"OBS_DIM={OBS_DIM}",
             f"global={GLOBAL_DIM} own={SLOTS_OWN}x{OWN_PER} "
             f"enemy={SLOTS_ENEMY}x{ENEMY_PER} factory={SLOTS_FACTORY}x{FACTORY_PER} "
             f"patch={PATCH_DIM}",
             f"action classes={N_ACTION_CLASSES}: {ACTION_CLASSES}",
             "action heads: class_id, target_slot, move_dx, move_dy, "
             "produce_type_id, place_dx, place_dy"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
