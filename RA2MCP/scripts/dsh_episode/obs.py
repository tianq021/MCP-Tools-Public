"""Capture raw observations from the native bridge state.

Only legal player knowledge is stored per object:
  * own objects: all fields of the proto `Object` (visible to the owner anyway)
  * enemies: only objects whose `cell_shrouded` is False and whose cloak state is
    not Cloaking/Cloaked (the visibility contract). Hidden enemy objects are NOT
    stored individually — only `hidden_enemy_count`, so the episode file itself
    cannot leak map-hack information into training.
"""
from dsh_episode.hostility import is_opponent

CELL = 256


def _mission_name(m):
    try:
        from ra2yrproto import ra2yr
        return ra2yr.Mission.Name(m)
    except Exception:  # noqa: BLE001
        return str(m)


def _abstract_name(a):
    try:
        from ra2yrproto import ra2yr
        return ra2yr.AbstractType.Name(a)
    except Exception:  # noqa: BLE001
        return str(a)


def object_to_raw(o, types, houses, include_coords=True):
    """Convert one proto Object into a JSON-safe raw dict."""
    type_ptr = int(getattr(o, "pointer_technotypeclass", 0))
    rec = {
        "addr": int(o.pointer_self),
        "type_ptr": type_ptr,
        "type_name": types.get(type_ptr, "?"),
        "abstract": _abstract_name(getattr(o, "object_type", 0)),
        "owner_ptr": int(getattr(o, "pointer_house", 0)),
        "owner": houses.get(int(getattr(o, "pointer_house", 0)), "?"),
        "health": int(o.health),
        "cell": [int(o.coordinates.x) // CELL, int(o.coordinates.y) // CELL],
        "xyz": [int(o.coordinates.x), int(o.coordinates.y)],
        "mission": _mission_name(getattr(o, "current_mission", 0)),
        "in_limbo": bool(getattr(o, "in_limbo", False)),
        "on_map": bool(getattr(o, "on_map", False)),
        "selected": bool(getattr(o, "selected", False)),
        "cell_shrouded": bool(getattr(o, "cell_shrouded", False)),
        "cell_mapped": bool(getattr(o, "cell_mapped", False)),
        "discovered_by_current_player": bool(
            getattr(o, "discovered_by_current_player", False)),
        "is_visible": bool(getattr(o, "is_visible", False)),
        "cloak_state": int(getattr(o, "cloak_state", 0)),
    }
    if hasattr(o, "deployed"):
        rec["deployed"] = bool(o.deployed)
    if not include_coords:
        rec.pop("xyz", None)
    return rec


CLOAK_HIDDEN = (1, 2)  # Cloaking / Cloaked


def capture_observation(st, hero_self, types, houses, map_soa, wall,
                        frame=None, stage_name=None, map_patch=None):
    """Build one schema observation dict from a GetGameState reply."""
    frame = int(st.current_frame if frame is None else frame)
    stage_name = stage_name or _stage_name(st.stage)
    own, visible = [], []
    hidden = 0
    for o in st.objects:
        if int(o.health) <= 0:
            continue
        owner = int(getattr(o, "pointer_house", 0))
        if owner == hero_self:
            # keep own in-limbo objects (e.g. a finished building waiting to be
            # placed) so the policy can see and place them; the flag is recorded
            own.append(object_to_raw(o, types, houses))
            continue
        if owner == 0 or bool(getattr(o, "in_limbo", False)):
            continue
        if not is_opponent({"owner": houses.get(owner, "?")}):
            continue
        is_hidden = bool(getattr(o, "cell_shrouded", True))
        cloak = int(getattr(o, "cloak_state", 0))
        if is_hidden or cloak in CLOAK_HIDDEN:
            hidden += 1
            continue
        visible.append(object_to_raw(o, types, houses))

    map_summary = None
    if map_soa is not None:
        map_summary = {
            "width": int(map_soa.map_width),
            "height": int(map_soa.map_height),
            "mapped": sum(1 for v in map_soa.mapped if v),
            "shrouded": sum(1 for v in map_soa.shrouded if v),
            "cells": len(map_soa.shrouded),
        }
    factories = []
    for f in st.factories:
        factories.append({
            "owner_ptr": int(f.owner),
            "object_ptr": int(f.object),
            "progress_timer": int(f.progress_timer),
            "completed": bool(f.completed),
            "on_hold": bool(getattr(f, "on_hold", False)),
            "queued_objects": [int(q) for q in getattr(f, "queued_objects", [])],
        })
    houses_raw = [{
        "ptr": int(h.self), "name": h.name, "faction": h.faction,
        "array_index": int(h.array_index),
        "current_player": bool(getattr(h, "current_player", False)),
        "is_human": bool(getattr(h, "is_human_player", False)),
        "money": int(h.money),
        "power_output": int(getattr(h, "power_output", 0)),
        "power_drain": int(getattr(h, "power_drain", 0)),
        "defeated": bool(h.defeated), "is_loser": bool(getattr(h, "is_loser", False)),
        "is_winner": bool(getattr(h, "is_winner", False)),
        "is_game_over": bool(getattr(h, "is_game_over", False)),
        "start_credits": int(getattr(h, "start_credits", 0)),
    } for h in st.houses]
    hero = next((h for h in st.houses if int(h.self) == hero_self), None)
    neutral = []
    try:
        from ra2v2 import neutral_objects as _NO
        neutral = _NO.capture_neutral_objects(st.objects, hero_self, houses, frame,
                                              types=types)
    except Exception:  # noqa: BLE001 - observation capture must never break
        neutral = []
    return {
        "type": "observation",
        "frame": frame,
        "wall": wall,
        "stage": stage_name,
        "money": int(hero.money) if hero is not None else 0,
        "houses": houses_raw,
        "own_objects": own,
        "visible_enemies": visible,
        "neutral_objects": neutral,
        "factories": factories,
        "hidden_enemy_count": hidden,
        "map": map_summary,
        "map_patch": map_patch,
        "game_frame_present": frame > 0,
    }


def _stage_name(stage):
    try:
        from ra2yrproto import ra2yr
        return ra2yr.LoadStage.Name(stage)
    except Exception:  # noqa: BLE001
        return str(stage)


def map_patch_from_soa(map_soa, center_cell, size=9):
    """Local mapped/shrouded/passability patch around a cell (for v2 features)."""
    if map_soa is None:
        return None
    w, h = int(map_soa.map_width), int(map_soa.map_height)
    cx, cy = int(center_cell[0]), int(center_cell[1])
    half = size // 2
    mapped, shrouded, passability = [], [], []
    for dy in range(-half, half + 1):
        for dx in range(-half, half + 1):
            x, y = cx + dx, cy + dy
            if 0 <= x < w and 0 <= y < h:
                i = y * w + x
                mapped.append(1 if map_soa.mapped[i] else 0)
                shrouded.append(1 if map_soa.shrouded[i] else 0)
                passability.append(int(map_soa.passability[i]))
            else:
                mapped.append(0)
                shrouded.append(1)
                passability.append(0)
    return {"center": [cx, cy], "size": size, "mapped": mapped,
            "shrouded": shrouded, "passability": passability}
