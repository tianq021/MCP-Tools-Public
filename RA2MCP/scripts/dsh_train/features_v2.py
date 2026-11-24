"""Observation builder for spec v2 (richer, versioned; v1 untouched).

Uses only the legal raw observation fields recorded by `dsh_episode.obs`:
own objects (full detail), visibility-filtered enemies, factories (queue
progress), houses (money/power), map summary + local map_patch
(mapped/shrouded/passability). Hidden enemies are never used.
"""
import math

import spec_v2 as S


def _clip(v, lo=-5.0, hi=5.0):
    return max(lo, min(hi, float(v)))


def _hp(v):
    try:
        return max(0.0, min(1.5, float(v) / 1000.0))
    except (TypeError, ValueError):
        return 0.0


def _cell(o):
    c = o.get("cell") or [0, 0]
    try:
        return (float(c[0]), float(c[1]))
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0)


def _kind(o):
    a = str(o.get("abstract") or "")
    if "BUILDING" in a:
        return "building"
    if "INFANTRY" in a:
        return "infantry"
    return "unit"


def _mission(o):
    return str(o.get("mission") or "")


def _type_known(o, type_id_map):
    return 1.0 if str(o.get("type_name")) in type_id_map else 0.0


def _enemy_threat(e):
    hp = _hp(e.get("health"))
    k = _kind(e)
    weight = 1.0 if k == "building" else (0.5 if k == "infantry" else 1.2)
    return min(2.0, hp * weight)


def build_obs_v2(obs_row, type_id_map, map_width=182, map_height=182):
    own = obs_row.get("own_objects") or []
    enemies = [e for e in (obs_row.get("visible_enemies") or [])
               if str(e.get("owner") or "").casefold() not in
               ("neutral", "special", "civilian", "?")]
    factories = obs_row.get("factories") or []
    houses = obs_row.get("houses") or []
    hero = next((h for h in houses if h.get("current_player") and h.get("is_human")), {})
    money = float(obs_row.get("money") or hero.get("money") or 0)
    power_out = float(hero.get("power_output") or 0)
    power_drain = float(hero.get("power_drain") or 0)
    map_summary = obs_row.get("map") or {}

    buildings = [o for o in own if _kind(o) == "building"]
    infantry = [o for o in own if _kind(o) == "infantry"]
    units = [o for o in own if _kind(o) == "unit"]
    cy = next((b for b in buildings
               if b.get("type_name") == "Allied Construction Yard"), None)
    base = _cell(cy) if cy else (0.0, 0.0)
    pts = [_cell(o) for o in own] or [(0.0, 0.0)]
    centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    own_hps = [_hp(o.get("health")) for o in own] or [0.0]
    enemy_hps = [_hp(e.get("health")) for e in enemies] or [0.0]
    factory_active = sum(1 for f in factories if 0 < f.get("progress_timer", 0) < 100)
    factory_completed = sum(1 for f in factories if f.get("completed"))
    factory_queued = sum(len(f.get("queued_objects") or []) for f in factories)
    mapped = float(map_summary.get("mapped") or 0)
    total_cells = float(map_summary.get("cells") or max(1, map_width * map_height))

    obs = [
        _clip(money / 10000.0, 0.0, 3.0),
        _clip(power_out / 500.0, 0.0, 3.0),
        _clip(power_drain / 500.0, 0.0, 3.0),
        _clip(power_out / (power_drain + 1.0), 0.0, 5.0),
        min(2.0, len(buildings) / 10.0),
        min(2.0, len(units) / 20.0),
        min(2.0, len(infantry) / 20.0),
        min(2.0, len(enemies) / 10.0),
        _clip(float(obs_row.get("frame") or 0) / 24000.0, 0.0, 2.0),
        _clip(mapped / max(1.0, total_cells), 0.0, 1.0),
        _clip(base[0] / 182.0, 0.0, 1.0),
        _clip(base[1] / 182.0, 0.0, 1.0),
        _clip(centroid[0] / 182.0, 0.0, 1.0),
        _clip(centroid[1] / 182.0, 0.0, 1.0),
        1.0 if cy else 0.0,
        sum(own_hps) / len(own_hps),
        min(own_hps),
        sum(enemy_hps) / len(enemy_hps),
        min(1.0, factory_active / 4.0),
        min(1.0, factory_completed / 4.0),
        min(1.0, factory_queued / 10.0),
        1.0 if obs_row.get("defensive_trigger") else 0.0,
        _clip(map_width / 200.0, 0.0, 2.0),
        _clip(map_height / 200.0, 0.0, 2.0),
    ]
    assert len(obs) == S.GLOBAL_DIM

    own_sorted = sorted(own, key=lambda o: math.hypot(_cell(o)[0] - centroid[0],
                                                      _cell(o)[1] - centroid[1]))
    for i in range(S.SLOTS_OWN):
        if i < len(own_sorted):
            o = own_sorted[i]
            c = _cell(o)
            m = _mission(o)
            k = _kind(o)
            obs += [
                1.0,
                _clip((c[0] - centroid[0]) / 30.0),
                _clip((c[1] - centroid[1]) / 30.0),
                _hp(o.get("health")),
                1.0 if k == "building" else 0.0,
                1.0 if k == "unit" else 0.0,
                1.0 if k == "infantry" else 0.0,
                1.0 if o.get("in_limbo") else 0.0,
                1.0 if o.get("on_map") else 0.0,
                1.0 if m == "Mission_Attack" else 0.0,
                1.0 if m in ("Mission_Move", "Mission_QMove") else 0.0,
                1.0 if m == "Mission_Guard" else 0.0,
                1.0 if m == "Mission_Construction" else 0.0,
                _type_known(o, type_id_map),
                1.0 if o.get("cell_mapped") else 0.0,
                {"building": 0.0, "unit": 1.0, "infantry": 0.5}[k],
            ]
        else:
            obs += [0.0] * S.OWN_PER

    enemies_sorted = sorted(enemies, key=lambda e: math.hypot(
        _cell(e)[0] - centroid[0], _cell(e)[1] - centroid[1]))
    for i in range(S.SLOTS_ENEMY):
        if i < len(enemies_sorted):
            e = enemies_sorted[i]
            c = _cell(e)
            k = _kind(e)
            obs += [
                1.0,
                _clip((c[0] - centroid[0]) / 30.0),
                _clip((c[1] - centroid[1]) / 30.0),
                _hp(e.get("health")),
                1.0 if k == "building" else 0.0,
                1.0 if k == "unit" else 0.0,
                1.0 if k == "infantry" else 0.0,
                1.0 if str(e.get("owner")) == "Neutral" else 0.0,
                _clip(float(e.get("cloak_state") or 0) / 3.0, 0.0, 1.0),
                1.0 if e.get("cell_mapped") else 0.0,
                _enemy_threat(e),
                _type_known(e, type_id_map),
            ]
        else:
            obs += [0.0] * S.ENEMY_PER

    for i in range(S.SLOTS_FACTORY):
        if i < len(factories):
            f = factories[i]
            obs += [
                1.0,
                _clip(float(f.get("progress_timer") or 0) / 100.0, 0.0, 1.5),
                1.0 if f.get("completed") else 0.0,
                min(2.0, len(f.get("queued_objects") or []) / 5.0),
                1.0 if int(f.get("owner_ptr") or 0) == int(hero.get("ptr") or 0) else 0.0,
            ]
        else:
            obs += [0.0] * S.FACTORY_PER

    patch = downward_sample_patch(obs_row.get("map_patch"), S.MAP_PATCH_SIZE)
    for i in range(S.MAP_PATCH_SIZE * S.MAP_PATCH_SIZE):
        if patch and i < len(patch.get("mapped") or []):
            obs.append(float(patch["mapped"][i]))
            obs.append(min(1.0, float(patch["passability"][i]) / 3.0))
            obs.append(float(patch["shrouded"][i]))
        else:
            obs += [0.0] * len(S.MAP_PATCH_FEATURES)

    assert len(obs) == S.OBS_DIM, (len(obs), S.OBS_DIM)
    return obs


def downward_sample_patch(patch, target_size):
    """Take the centre `target_size x target_size` of a recorded odd patch."""
    if not patch:
        return None
    src = int(patch.get("size") or 0)
    if src <= target_size or src % 2 == 0 or target_size % 2 == 0:
        return patch if src == target_size else None
    half_src, half_dst = src // 2, target_size // 2
    offset = half_src - half_dst
    out = {"center": patch["center"], "size": target_size,
           "mapped": [], "shrouded": [], "passability": []}
    for y in range(target_size):
        for x in range(target_size):
            i = (y + offset) * src + (x + offset)
            out["mapped"].append(patch["mapped"][i])
            out["shrouded"].append(patch["shrouded"][i])
            out["passability"].append(patch["passability"][i])
    return out


def validate_visibility(obs_row):
    """Raise if a hidden enemy slipped into the observation (regression guard)."""
    for e in obs_row.get("visible_enemies") or []:
        if e.get("cell_shrouded") is not False:
            raise ValueError(f"hidden enemy {e.get('addr')} in visible_enemies")
        if int(e.get("cloak_state") or 0) in (1, 2):
            raise ValueError(f"cloaked enemy {e.get('addr')} in visible_enemies")
    return True
