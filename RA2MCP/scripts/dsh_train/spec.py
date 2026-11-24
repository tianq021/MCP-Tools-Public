"""Feature/action specification for the RA2 local-learning framework (v1).

Everything downstream (dataset, model, inference) derives dimensions from this
file, so changing the layout requires bumping FEATURE_VERSION and retraining.

Design goals
  * obs is a fixed-size float vector, cheap to compute from one episode row
  * only information the local player may legally know is used (visibility
    contract: reports/dsh-visibility-contract.md); hidden/global "cheat" counts
    are never part of obs
  * actions are the discrete subset of the native control interface

Run `python spec.py` to print the layout and dimension.
"""
import math

FEATURE_VERSION = "v1"
MODEL_FORMAT_VERSION = 1

CELL = 256            # leptons per cell
SLOTS_OWN = 8         # own objects tracked (sorted by distance to base)
SLOTS_ENEMY = 6       # visible enemy objects tracked
MAP_NORM = 182.0      # default map width/height used for coordinate normalisation
FRAME_NORM = 24000.0  # ~6.5 min at 60 fps
HP_NORM = 1000.0

# --- global block -------------------------------------------------------------
GLOBAL_NAMES = [
    "money_norm",          # money / 10000
    "buildings_norm",      # own buildings / 10
    "units_norm",          # own units+infantry / 20
    "infantry_norm",       # own infantry / 20
    "visible_enemies_norm",
    "own_hp_mean",         # mean health/1000 of own objects
    "own_hp_min",          # min health/1000 of own objects
    "frame_norm",
    "mapped_ratio",        # mapped cells / total cells
    "base_x_norm",         # construction yard x / MAP_NORM
    "base_y_norm",
    "centroid_x_norm",     # own-object centroid x / MAP_NORM
    "centroid_y_norm",
    "has_cy",              # construction yard present
]

# --- per own-object slot (SLOTS_OWN x OWN_PER) --------------------------------
OWN_NAMES = [
    "present", "dx_norm", "dy_norm", "hp_norm",
    "is_infantry", "is_building", "mission_attack", "mission_move",
]

# --- per visible-enemy slot (SLOTS_ENEMY x ENEMY_PER) -------------------------
ENEMY_NAMES = [
    "present", "dx_norm", "dy_norm", "hp_norm",
    "is_building", "is_infantry", "is_neutral", "cloak_norm",
]

GLOBAL_DIM = len(GLOBAL_NAMES)
OWN_PER = len(OWN_NAMES)
ENEMY_PER = len(ENEMY_NAMES)
OBS_DIM = GLOBAL_DIM + SLOTS_OWN * OWN_PER + SLOTS_ENEMY * ENEMY_PER

# --- action space -------------------------------------------------------------
ACTION_NAMES = [
    "NOOP",            # 0
    "MOVE_N",          # 1  (y-)
    "MOVE_S",          # 2  (y+)
    "MOVE_E",          # 3  (x+)
    "MOVE_W",          # 4  (x-)
    "ATTACK_NEAREST",  # 5
    "STOP",            # 6
    "RETREAT",         # 7  (move back to base)
    "DEPLOY",          # 8
    "PRODUCE_BUILDING",# 9
    "PRODUCE_INFANTRY",# 10
    "PLACE_BUILDING",  # 11
]
ACTION_IDS = {name: i for i, name in enumerate(ACTION_NAMES)}
N_ACTIONS = len(ACTION_NAMES)

# direction moves that need coordinates at inference time
DIRECTION_ACTIONS = {1: (0, -1), 2: (0, 1), 3: (1, 0), 4: (-1, 0)}


def action_from_order(order, centroid, base):
    """Map one episode order record (dict) to an action id (or None)."""
    a = str(order.get("action", "")).upper()
    if a == "DEPLOY":
        return ACTION_IDS["DEPLOY"]
    if a == "PLACE":
        return ACTION_IDS["PLACE_BUILDING"]
    if a == "PRODUCE":
        t = str(order.get("type", ""))
        if t in ("GI", "Conscript", "Infantry"):
            return ACTION_IDS["PRODUCE_INFANTRY"]
        return ACTION_IDS["PRODUCE_BUILDING"]
    if a == "ATTACK":
        return ACTION_IDS["ATTACK_NEAREST"]
    if a == "STOP":
        return ACTION_IDS["STOP"]
    if a == "MOVE":
        dest = order.get("dest") or order.get("target")
        if isinstance(dest, (list, tuple)) and len(dest) >= 2:
            dx = float(dest[0]) - float(centroid[0])
            dy = float(dest[1]) - float(centroid[1])
            if abs(dx) < CELL and abs(dy) < CELL and base is not None:
                # very short move: treat as retreat if base direction dominates
                dx = float(base[0]) - float(centroid[0])
                dy = float(base[1]) - float(centroid[1])
                if abs(dx) < CELL and abs(dy) < CELL:
                    return ACTION_IDS["NOOP"]
                return (ACTION_IDS["MOVE_E"] if dx > 0 and abs(dx) >= abs(dy)
                        else ACTION_IDS["MOVE_W"] if dx < 0 and abs(dx) >= abs(dy)
                        else ACTION_IDS["MOVE_S"] if dy > 0 else ACTION_IDS["MOVE_N"])
            if abs(dx) >= abs(dy):
                return ACTION_IDS["MOVE_E"] if dx > 0 else ACTION_IDS["MOVE_W"]
            return ACTION_IDS["MOVE_S"] if dy > 0 else ACTION_IDS["MOVE_N"]
        return ACTION_IDS["NOOP"]
    return None


def describe():
    rows = []
    rows.append(f"FEATURE_VERSION={FEATURE_VERSION} OBS_DIM={OBS_DIM}")
    rows.append(f"global={GLOBAL_DIM} own={SLOTS_OWN}x{OWN_PER} "
                f"enemy={SLOTS_ENEMY}x{ENEMY_PER} actions={N_ACTIONS}")
    offset = 0
    for n in GLOBAL_NAMES:
        rows.append(f"  [{offset:3d}] {n}")
        offset += 1
    for s in range(SLOTS_OWN):
        for j, n in enumerate(OWN_NAMES):
            rows.append(f"  [{offset:3d}] own[{s}].{n}")
            offset += 1
    for s in range(SLOTS_ENEMY):
        for j, n in enumerate(ENEMY_NAMES):
            rows.append(f"  [{offset:3d}] enemy[{s}].{n}")
            offset += 1
    rows.append("actions: " + ", ".join(f"{i}={n}" for i, n in enumerate(ACTION_NAMES)))
    return "\n".join(rows)


if __name__ == "__main__":
    print(describe())
