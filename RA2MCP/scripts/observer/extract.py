"""Pure extraction: raw observer recording -> match summary / houses / build orders / winner / first defeat.

Input format (as written by ``scripts/ai_teacher/record_ai_states_full.py`` and by the
upcoming observer collector):

    {"frame": 27, "wall": 1793356856.656, "houses": [[idx, name, money, power_out,
      power_drain, defeated, is_loser, is_winner], ...],
     "objects": [[owner_idx, type_name, health, cell_x, cell_y, on_map, in_limbo,
      mission_int], ...]}

The extractor is deliberately two-pass and streaming (the live collector can write tens
of thousands of samples; we never hold raw samples in memory):

  pass 1: house identity, first-defeat frames, transient is_winner flags, type mobility
          (to classify buildings vs mobile units), first appearance (build order), fps;
  pass 2: per-house timeline (money / object counts / power), composition snapshots,
          offensive-activity counters.

All outputs are plain JSON-serializable dicts; ``store.py`` persists them.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import statistics
from typing import Iterator

# House record layout of the recorder output.
H_IDX, H_NAME, H_MONEY, H_POWER_OUT, H_POWER_DRAIN = 0, 1, 2, 3, 4
H_DEFEATED, H_IS_LOSER, H_IS_WINNER = 5, 6, 7

# Object record layout of the recorder output.
O_OWNER, O_TYPE, O_HEALTH, O_X, O_Y, O_ON_MAP, O_LIMBO, O_MISSION = range(8)

NON_PLAYER_NAMES = {"Neutral", "Special"}
OFFENSIVE_MISSIONS = {1, 2, 3, 15, 29}  # Attack, Move, QMove, Hunt, AttackMove
MOBILE_HINTS = (
    "GI", "Conscript", "Trooper", "Dog", "Spy", "Engineer", "Tanya", "Boris", "Yuri",
    "Initiate", "Brute", "Virus", "Tank", "IFV", "Miner", "Harvester", "Chopper",
    "Rocketeer", "Desolator", "Tesla Trooper", "Flak", "Rhino", "Apocalypse", "Prism",
    "Mirage", "Grizzly", "Lasher", "Battle Fortress", "Siege", "Aircraft", "Ship",
    "Destroyer", "Dreadnought", "Amphibious", "Hover", "Navy",
)
BUILDING_HINTS = (
    "Construction Yard", "Power Plant", "Reactor", "Refinery", "Barracks", "War Factory",
    "Radar", "Battle Lab", "Air Force", "Pill Box", "Sentry", "Bunker", "Wall", "Gate",
    "Prism Tower", "Patriot", "Ore Purifier", "Helipad", "Shipyard", "Cloning", "Grinder",
    "Tank Bunker", "Laser", "Lightning", "Cannon", "Machine Gun", "Bunker", "Dome",
)


def sha256_file(path: pathlib.Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _open_text(path: pathlib.Path):
    if str(path).lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def iter_samples(path: pathlib.Path) -> Iterator[dict]:
    with _open_text(pathlib.Path(path)) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_dedup_samples(path: pathlib.Path) -> Iterator[dict]:
    """Yield samples with strictly increasing frame (drops paused/frozen duplicates).

    The recorder keeps polling while the game sits on a pause/score screen, so a real
    recording contains many repeated frames; they must never be treated as samples.
    """
    last = -1
    for sample in iter_samples(path):
        frame = int(sample.get("frame", -1))
        if frame <= last:
            continue
        last = frame
        yield sample


def _house_tuple(house: list) -> tuple:
    return (
        int(house[H_IDX]), str(house[H_NAME]), int(house[H_MONEY]),
        int(house[H_POWER_OUT]), int(house[H_POWER_DRAIN]),
        bool(house[H_DEFEATED]), bool(house[H_IS_LOSER]), bool(house[H_IS_WINNER]),
    )


def _is_building_type(owner_type: tuple, moves: float, first_type: str) -> tuple[bool, str]:
    """Heuristic building/unit classification; returns (is_building, reason)."""
    lowered = first_type.lower()
    if any(hint.lower() in lowered for hint in MOBILE_HINTS):
        return False, "mobile-name-hint"
    if any(hint.lower() in lowered for hint in BUILDING_HINTS):
        return True, "building-name-hint"
    if moves <= 1.2:
        return True, "static-position"
    return False, "moving-position"


def extract_recording(
    path: pathlib.Path,
    match_id: str,
    map_name: str = "UNKNOWN",
    seed: int | None = None,
    observer_indices: tuple[int, ...] = (),
    exclude_names: frozenset[str] = frozenset(NON_PLAYER_NAMES),
    source_label: str | None = None,
) -> dict:
    """Two-pass extraction of one recording file (``.jsonl`` or ``.jsonl.gz``)."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    # ---------------------------------------------------------------- pass 1 ----
    house_names: dict[int, str] = {}
    first_sample: dict | None = None
    last_sample: dict | None = None
    sample_count = 0

    first_defeat_frame: dict[int, int] = {}
    first_defeat_sample_idx: dict[int, int] = {}
    transient_winner: dict[int, dict] = {}

    # (owner, type_name) -> [min_x, max_x, min_y, max_y, n]
    tracks: dict[tuple[int, str], list[float]] = {}
    build_first: dict[tuple[int, str], dict] = {}

    for idx, sample in enumerate(iter_dedup_samples(path)):
        sample_count += 1
        if first_sample is None:
            first_sample = sample
        last_sample = sample
        frame = int(sample.get("frame", -1))

        for raw_house in sample.get("houses", []):
            h = _house_tuple(raw_house)
            house_names[h[0]] = h[1]
            if h[5] and h[0] not in first_defeat_frame:
                first_defeat_frame[h[0]] = frame
                first_defeat_sample_idx[h[0]] = idx
            if h[7]:
                rec = transient_winner.setdefault(h[0], {"count": 0, "first_frame": frame,
                                                         "last_frame": frame})
                rec["count"] += 1
                rec["last_frame"] = frame

        for obj in sample.get("objects", []):
            owner, type_name, _health, x, y, on_map, in_limbo, _mission = (
                int(obj[O_OWNER]), str(obj[O_TYPE]), int(obj[O_HEALTH]),
                int(obj[O_X]), int(obj[O_Y]), int(obj[O_ON_MAP]),
                int(obj[O_LIMBO]), int(obj[O_MISSION]),
            )
            if not on_map or in_limbo:
                continue
            key = (owner, type_name)
            track = tracks.get(key)
            if track is None:
                tracks[key] = [x, x, y, y, 1.0]
                build_first[key] = {"frame": frame, "cell": [x, y], "health": _health}
            else:
                track[0] = min(track[0], x)
                track[1] = max(track[1], x)
                track[2] = min(track[2], y)
                track[3] = max(track[3], y)
                track[4] += 1

    if first_sample is None or last_sample is None:
        raise ValueError(f"empty recording: {path}")

    # ------------------------------------------------- participants & winner ----
    participants = [
        idx for idx, name in house_names.items()
        if name not in exclude_names and idx not in observer_indices
    ]
    participants.sort()
    last_houses = {int(h[H_IDX]): _house_tuple(h) for h in last_sample.get("houses", [])}
    survivors = [idx for idx in participants
                 if idx in last_houses and not last_houses[idx][H_DEFEATED]]
    winner = survivors[0] if len(survivors) == 1 else None
    if winner is not None:
        end_reason = "WINNER"
    elif not survivors:
        end_reason = "NO_SURVIVORS"
    else:
        end_reason = "RECORDING_ENDED_WITH_MULTIPLE_SURVIVORS"

    participant_defeat_order = sorted(
        (idx for idx in first_defeat_frame if idx in participants),
        key=lambda k: (first_defeat_frame[k], k),
    )
    elimination = [
        {
            "house_index": idx,
            "name": house_names.get(idx, str(idx)),
            "defeat_frame": first_defeat_frame[idx],
            "defeat_sample_idx": first_defeat_sample_idx[idx],
            "elimination_rank": rank,
        }
        for rank, idx in enumerate(participant_defeat_order, start=1)
    ]

    frame0 = int(first_sample.get("frame", 0))
    frame1 = int(last_sample.get("frame", frame0))
    wall0 = first_sample.get("wall")
    wall1 = last_sample.get("wall")
    if isinstance(wall0, (int, float)) and isinstance(wall1, (int, float)) and wall1 > wall0:
        fps_estimate = round((frame1 - frame0) / (wall1 - wall0), 2)
        wall_seconds = round(float(wall1 - wall0), 2)
    else:
        fps_estimate = None
        wall_seconds = None
    sim_seconds = round((frame1 - frame0) / (fps_estimate or 62.0), 2)

    # ---------------------------------------------------- building classification ----
    building_of_type: dict[tuple[int, str], tuple[bool, str, float]] = {}
    for key, track in tracks.items():
        moves = (track[1] - track[0]) + (track[3] - track[2])
        is_building, reason = _is_building_type(key, moves, key[1])
        building_of_type[key] = (is_building, reason, moves)

    build_orders: dict[int, list[dict]] = {idx: [] for idx in participants}
    for (owner, type_name), meta in build_first.items():
        if owner not in build_orders:
            continue
        is_building, reason, moves = building_of_type[(owner, type_name)]
        if not is_building:
            continue
        build_orders[owner].append({
            "type_name": type_name,
            "first_seen_frame": int(meta["frame"]),
            "first_seen_sim_s": round((int(meta["frame"]) - frame0) / (fps_estimate or 62.0), 1),
            "cell": list(meta["cell"]),
            "classification": reason,
        })
    for idx in build_orders:
        build_orders[idx].sort(key=lambda row: row["first_seen_frame"])

    # ---------------------------------------------------------------- pass 2 ----
    timeline: list[dict] = []
    composition_at: dict[int, dict] = {}
    composition_targets = [60, 120, 180, 240, 300, 420, 600, 900, 1200]
    attack_first: dict[int, int] = {}
    attack_frames: dict[int, int] = {}
    peak_units: dict[int, int] = {idx: 0 for idx in participants}
    peak_objects: dict[int, int] = {idx: 0 for idx in participants}

    for idx, sample in enumerate(iter_dedup_samples(path)):
        frame = int(sample.get("frame", -1))
        sim_s = round((frame - frame0) / (fps_estimate or 62.0), 1)
        per_house: dict[int, dict] = {}
        unit_types: dict[int, dict[str, int]] = {h: {} for h in participants}
        for raw_house in sample.get("houses", []):
            h = _house_tuple(raw_house)
            if h[0] in participants:
                per_house[h[0]] = {
                    "money": h[2], "power_out": h[3], "power_drain": h[4],
                    "defeated": h[5], "objects": 0, "units": 0, "buildings": 0,
                }
        for obj in sample.get("objects", []):
            owner, type_name, _health, _x, _y, on_map, in_limbo, mission = (
                int(obj[O_OWNER]), str(obj[O_TYPE]), int(obj[O_HEALTH]),
                int(obj[O_X]), int(obj[O_Y]), int(obj[O_ON_MAP]),
                int(obj[O_LIMBO]), int(obj[O_MISSION]),
            )
            if owner not in per_house:
                continue
            bucket = per_house[owner]
            bucket["objects"] += 1
            if on_map and not in_limbo:
                is_building = building_of_type.get((owner, type_name), (False, "", 0.0))[0]
                if is_building:
                    bucket["buildings"] += 1
                else:
                    bucket["units"] += 1
                    unit_types[owner][type_name] = unit_types[owner].get(type_name, 0) + 1
            if mission in OFFENSIVE_MISSIONS:
                if owner not in attack_first:
                    attack_first[owner] = frame
                attack_frames[owner] = attack_frames.get(owner, 0) + 1
        for owner, bucket in per_house.items():
            peak_units[owner] = max(peak_units[owner], bucket["units"])
            peak_objects[owner] = max(peak_objects[owner], bucket["objects"])
        timeline.append({"sample_idx": idx, "frame": frame, "sim_s": sim_s,
                         "houses": per_house})

        for target in composition_targets:
            if target in composition_at:
                continue
            if sim_s >= target:
                composition_at[target] = {
                    "sim_s": sim_s,
                    "houses": {owner: dict(sorted(types.items(), key=lambda kv: -kv[1]))
                               for owner, types in unit_types.items()},
                }

    # ---------------------------------------------------------------- houses ----
    houses_rows = []
    for idx in participants:
        last = last_houses.get(idx)
        build = build_orders.get(idx, [])
        houses_rows.append({
            "house_index": idx,
            "name": house_names.get(idx, str(idx)),
            "defeat_frame": first_defeat_frame.get(idx),
            "defeat_sim_s": (round((first_defeat_frame[idx] - frame0) / (fps_estimate or 62.0), 1)
                             if idx in first_defeat_frame else None),
            "elimination_rank": next((e["elimination_rank"] for e in elimination
                                      if e["house_index"] == idx), None),
            "final_defeated": bool(last[H_DEFEATED]) if last else None,
            "final_is_winner": bool(last[H_IS_WINNER]) if last else None,
            "final_is_loser": bool(last[H_IS_LOSER]) if last else None,
            "final_money": int(last[H_MONEY]) if last else None,
            "final_power_output": int(last[H_POWER_OUT]) if last else None,
            "final_power_drain": int(last[H_POWER_DRAIN]) if last else None,
            "final_objects": (timeline[-1]["houses"].get(idx, {}).get("objects") if timeline else None),
            "final_units": (timeline[-1]["houses"].get(idx, {}).get("units") if timeline else None),
            "final_buildings": (timeline[-1]["houses"].get(idx, {}).get("buildings") if timeline else None),
            "peak_units": peak_units[idx],
            "peak_objects": peak_objects[idx],
            "first_attack_frame": attack_first.get(idx),
            "first_attack_sim_s": (round((attack_first[idx] - frame0) / (fps_estimate or 62.0), 1)
                                   if idx in attack_first else None),
            "offensive_samples": attack_frames.get(idx, 0),
            "build_order": build,
            "build_count": len(build),
            "is_winner": idx == winner,
            "is_first_defeated": bool(elimination and elimination[0]["house_index"] == idx),
        })

    first_defeated = elimination[0] if elimination else None
    result = {
        "match_id": match_id,
        "source_path": str(path),
        "source_label": source_label or path.parent.name,
        "source_sha256": sha256_file(path),
        "map_name": map_name,
        "seed": seed,
        "house_names": {str(k): v for k, v in sorted(house_names.items())},
        "participants": participants,
        "observer_indices": list(observer_indices),
        "sample_count": sample_count,
        "frame_start": frame0,
        "frame_end": frame1,
        "wall_seconds": wall_seconds,
        "sim_seconds": sim_seconds,
        "fps_estimate": fps_estimate,
        "end_reason": end_reason,
        "winner_house": winner,
        "winner_name": house_names.get(winner) if winner is not None else None,
        "survivors": survivors,
        "first_defeated_house": first_defeated["house_index"] if first_defeated else None,
        "first_defeated_name": first_defeated["name"] if first_defeated else None,
        "first_defeat_frame": first_defeated["defeat_frame"] if first_defeated else None,
        "first_defeat_sim_s": (round((first_defeated["defeat_frame"] - frame0) / (fps_estimate or 62.0), 1)
                               if first_defeated else None),
        "elimination_order": elimination,
        "transient_winner_flags": {str(k): v for k, v in transient_winner.items()},
        "houses": houses_rows,
        "timeline": timeline,
        "composition_snapshots": {str(k): v for k, v in sorted(composition_at.items())},
        "schema": "observer.match.v1",
    }
    return result


def write_extract_artifacts(result: dict, out_dir: pathlib.Path) -> dict:
    """Write one match's extracted artifacts (JSON + first-defeat / winner bundles)."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "match.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # first-defeated bundle
    fd_house = result.get("first_defeated_house")
    fd_dir = out_dir / "first_defeat"
    fd_dir.mkdir(exist_ok=True)
    fd_house_row = None
    for row in result.get("houses", []):
        if row["house_index"] == fd_house:
            fd_house_row = row
            break
    (fd_dir / "house.json").write_text(
        json.dumps(fd_house_row, ensure_ascii=False, indent=2), encoding="utf-8")
    # the first-defeated house's own timeline slice (whole match; caller can crop)
    if fd_house is not None:
        rows = []
        for sample in result.get("timeline", []):
            bucket = sample.get("houses", {}).get(fd_house)
            if bucket is None:
                continue
            rows.append({"frame": sample["frame"], "sim_s": sample["sim_s"], **bucket})
        with open(fd_dir / "timeline.csv", "w", encoding="utf-8-sig", newline="") as fh:
            import csv

            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["frame"])
            writer.writeheader()
            writer.writerows(rows)

    # winner bundle
    winner = result.get("winner_house")
    winner_dir = out_dir / "winner_bundle"
    winner_dir.mkdir(exist_ok=True)
    (winner_dir / "status.json").write_text(json.dumps({
        "winner_house": winner,
        "winner_name": result.get("winner_name"),
        "end_reason": result.get("end_reason"),
        "note": "winner is the single surviving participant at recording end; "
                "is_winner flags are kept in houses.json as cross-check only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if winner is not None:
        winner_row = next((r for r in result.get("houses", [])
                           if r["house_index"] == winner), None)
        (winner_dir / "house.json").write_text(
            json.dumps(winner_row, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"out_dir": str(out_dir), "first_defeat_house": fd_house, "winner_house": winner}
