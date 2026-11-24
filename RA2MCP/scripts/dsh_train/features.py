"""Turn one episode row into the fixed-size observation vector of spec v1.

Rules enforced here (visibility contract):
  * own objects: always allowed (they are the player's own units/buildings)
  * enemies: only `visible_enemies` as recorded by the runner, which itself was
    filtered by Object.cell_shrouded == False at record time.  Rows that still
    contain a hidden enemy are rejected by the hygiene check below.
"""
import math

from spec import (CELL, ENEMY_PER, FRAME_NORM, GLOBAL_DIM, HP_NORM, MAP_NORM,
                  OWN_PER, SLOTS_ENEMY, SLOTS_OWN, action_from_order)


def _clip(x, lo=-5.0, hi=5.0):
    return max(lo, min(hi, x))


BUILDING_HINTS = ("Yard", "Plant", "Barracks", "Refinery", "Factory", "Pill",
                  "Bunker", "Derrick", "Airport", "Shop", "Hut", "Wall", "Radar",
                  "Lab", "Silo", "Helipad", "Turret", "Sam", "Tesla", "Conveyor",
                  "Reactor", "Tower", "Gate", "Repair", "Bunker", "HQ", "Palace")
INFANTRY_NAMES = ("GI", "Conscript", "Infantry", "Engineer", "Attack Dog",
                  "Tesla Trooper", "Flak Trooper", "SEAL", "Tanya", "Rocketeer")


def _kind(obj):
    """Classify an episode object as building / infantry / unit (dataset-side)."""
    abstract = str(obj.get("abstract") or "")
    if "BUILDING" in abstract:
        return "building"
    if "INFANTRY" in abstract:
        return "infantry"
    if "UNIT" in abstract or "AIRCRAFT" in abstract:
        return "unit"
    t = str(obj.get("type") or "")
    if t in INFANTRY_NAMES:
        return "infantry"
    if any(h in t for h in BUILDING_HINTS):
        return "building"
    return "unit"


def _hp(hp):
    try:
        return max(0.0, min(1.5, float(hp) / HP_NORM))
    except (TypeError, ValueError):
        return 0.0


def _cell_center(obj):
    c = obj.get("cell") or [0, 0]
    try:
        return (float(c[0]) * CELL + CELL / 2.0, float(c[1]) * CELL + CELL / 2.0)
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0)


def build_obs(row, map_width=182, map_height=182):
    human = row.get("human") or {}
    objects = human.get("objects") or []
    visible = [e for e in (row.get("visible_enemies") or [])
               if e.get("cell") is not None]
    map_info = row.get("map") or {}
    total_cells = max(1, int(map_width) * int(map_height))
    mapped_ratio = float(map_info.get("mapped", 0)) / total_cells
    money = float(row.get("money") or 0)
    frame = float(row.get("frame") or 0)

    buildings = [o for o in objects if _kind(o) == "building"]
    infantry = [o for o in objects if _kind(o) == "infantry"]
    units = [o for o in objects if _kind(o) == "unit"]

    cy = next((o for o in objects if o.get("type") == "Allied Construction Yard"), None)
    base = _cell_center(cy) if cy else None
    if objects:
        cx = sum(_cell_center(o)[0] for o in objects) / len(objects)
        cyy = sum(_cell_center(o)[1] for o in objects) / len(objects)
    else:
        cx, cyy = (base or (0.0, 0.0))
    centroid = (cx, cyy)

    hps = [_hp(o.get("hp")) for o in objects] or [0.0]
    obs = []
    obs += [
        _clip(money / 10000.0, 0.0, 3.0),
        min(1.5, len(buildings) / 10.0),
        min(2.0, (len(units) + len(infantry)) / 20.0),
        min(2.0, len(infantry) / 20.0),
        min(2.0, len(visible) / 10.0),
        sum(hps) / len(hps),
        min(hps),
        _clip(frame / FRAME_NORM, 0.0, 2.0),
        _clip(mapped_ratio, 0.0, 1.0),
        _clip((base[0] if base else 0.0) / CELL / MAP_NORM, 0.0, 1.0),
        _clip((base[1] if base else 0.0) / CELL / MAP_NORM, 0.0, 1.0),
        _clip(centroid[0] / CELL / MAP_NORM, 0.0, 1.0),
        _clip(centroid[1] / CELL / MAP_NORM, 0.0, 1.0),
        1.0 if cy is not None else 0.0,
    ]

    # own slots: closest to base first (stable order)
    own_sorted = sorted(objects, key=lambda o: math.hypot(
        _cell_center(o)[0] - centroid[0], _cell_center(o)[1] - centroid[1]))
    for i in range(SLOTS_OWN):
        if i < len(own_sorted):
            o = own_sorted[i]
            ox, oy = _cell_center(o)
            t = str(o.get("type") or "")
            mission = str(o.get("mission") or "")
            kind = _kind(o)
            present = 1.0
            dx = _clip((ox - centroid[0]) / (30.0 * CELL))
            dy = _clip((oy - centroid[1]) / (30.0 * CELL))
            hp = _hp(o.get("hp"))
            is_inf = 1.0 if kind == "infantry" else 0.0
            is_bld = 1.0 if kind == "building" else 0.0
            is_atk = 1.0 if mission == "Mission_Attack" else 0.0
            is_mov = 1.0 if mission in ("Mission_Move", "Mission_QMove") else 0.0
        else:
            present = dx = dy = hp = is_inf = is_bld = is_atk = is_mov = 0.0
        obs += [present, dx, dy, hp, is_inf, is_bld, is_atk, is_mov]

    # enemy slots: visible only, closest first
    vis_sorted = sorted(visible, key=lambda e: math.hypot(
        _cell_center(e)[0] - centroid[0], _cell_center(e)[1] - centroid[1]))
    for i in range(SLOTS_ENEMY):
        if i < len(vis_sorted):
            e = vis_sorted[i]
            ex, ey = _cell_center(e)
            t = str(e.get("type") or "")
            owner = str(e.get("owner") or "")
            ekind = _kind(e)
            obs += [
                1.0,
                _clip((ex - centroid[0]) / (30.0 * CELL)),
                _clip((ey - centroid[1]) / (30.0 * CELL)),
                _hp(e.get("hp")),
                1.0 if ekind == "building" else 0.0,
                1.0 if ekind == "infantry" else 0.0,
                1.0 if owner == "Neutral" else 0.0,
                _clip(float(e.get("cloak_state") or 0) / 3.0, 0.0, 1.0),
            ]
        else:
            obs += [0.0] * ENEMY_PER

    assert len(obs) == GLOBAL_DIM + SLOTS_OWN * OWN_PER + SLOTS_ENEMY * ENEMY_PER
    return obs


def label_from_row(row):
    """Return (action_id, ok) — first order in the row becomes the label.

    Orders targeting an enemy that is not in this row's visible_enemies are
    rejected (they would teach the model to attack through the shroud).
    """
    orders = row.get("orders") or []
    if not orders:
        return 0, True  # NOOP
    visible_ids = {e.get("addr") for e in (row.get("visible_enemies") or [])}
    human_objs = (row.get("human") or {}).get("objects") or []
    centroid = (0.0, 0.0)
    if human_objs:
        pts = [_cell_center(o) for o in human_objs]
        centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    cy = next((o for o in human_objs
               if o.get("type") == "Allied Construction Yard"), None)
    base = _cell_center(cy) if cy else None
    for order in orders:
        a = str(order.get("action", "")).upper()
        if a == "ATTACK" and order.get("target") not in visible_ids:
            return None, False
        aid = action_from_order(order, centroid, base)
        if aid is not None:
            return aid, True
    return 0, True
