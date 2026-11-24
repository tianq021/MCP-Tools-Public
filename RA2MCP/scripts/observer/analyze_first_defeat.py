r"""First-defeated-house autopsy from an observer run (offline, no game).

Reads ``_runs_observer/<run>/raw/states.jsonl`` (and ``extract/match.json``) and
reconstructs, for the first house that was defeated:

  * start cell, neighbours and their distances;
  * build order (from the extractor);
  * 30 s timeline of money / objects / units / buildings / damage received;
  * damage events per object (health decrease between samples) with inferred nearby
    enemy houses (INFERRED: proximity based, not an engine damage log);
  * object-loss classification: combat loss (damaged or enemies within 10 cells) vs
    MCV-deploy conversion vs sold/converted/parse gap;
  * per-60 s damage/attacker timeline and enemy presence near its base;
  * snapshot of all participants just before defeat;
  * its own offensive activity (attack missions).

Usage:
  E:\\RA2MCP\\.venv\\Scripts\\python.exe scripts\\observer\\analyze_first_defeat.py ^
      --run-dir E:\\RA2MCP\\_runs_observer\\observer-p3-20min-01 ^
      --out E:\\RA2MCP\\reports\\observer-collect-2026-11-03\\FIRST_DEFEAT_AUTOPSY_house7.md ^
      --json-out E:\\RA2MCP\\reports\\observer-collect-2026-11-03\\FIRST_DEFEAT_AUTOPSY_house7.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

ATTACK_RADIUS = 10          # cells: local neighbourhood for attribution
BASE_RADIUS = 25            # cells: "enemy had reached my base area"
LOSS_CONFIRM_SAMPLES = 3    # absent for this many consecutive samples => removal
BUILDING_HINTS = ("Construction Yard", "Power Plant", "Reactor", "Refinery", "Barracks",
                  "War Factory", "Radar", "Battle Lab", "Air Force", "Pill Box", "Sentry",
                  "Bunker", "Patriot", "Prism Tower", "Ore Purifier", "Shipyard",
                  "Robot Control", "Wall", "Cannon", "Missile")


def is_building(type_name: str) -> bool:
    return any(hint in type_name for hint in BUILDING_HINTS)


def load_run(run_dir: pathlib.Path) -> tuple[list[dict], dict]:
    run_dir = pathlib.Path(run_dir)
    states = [json.loads(line) for line in
              (run_dir / "raw" / "states.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    match = json.loads((run_dir / "extract" / "match.json").read_text(encoding="utf-8"))
    return states, match


def load_type_strength(run_dir: pathlib.Path) -> dict[int, int]:
    path = pathlib.Path(run_dir) / "raw" / "object_types.json"
    strength: dict[int, int] = {}
    if not path.is_file():
        return strength
    for entry in json.loads(path.read_text(encoding="utf-8")):
        try:
            strength[int(entry["pointer"])] = int(entry.get("strength") or 0)
        except Exception:  # noqa: BLE001
            continue
    return strength


def analyze(run_dir: pathlib.Path, house_index: int | None = None) -> dict:
    run_dir = pathlib.Path(run_dir)
    states, match = load_run(run_dir)
    fps = float(match.get("fps_estimate") or 62.4)
    frame0 = int(match.get("frame_start") or states[0]["frame"])
    participants = list(match["participants"])
    if house_index is None:
        house_index = match.get("first_defeated_house")
    if house_index is None:
        raise ValueError("no first-defeated house")
    first_defeat_frame = match.get("first_defeat_frame")
    t_of = lambda frame: round((int(frame) - frame0) / fps, 1)  # noqa: E731
    first_defeat_t = t_of(first_defeat_frame) if first_defeat_frame else None
    strength = load_type_strength(run_dir)

    tracks: dict[int, dict] = {}
    missing: collections.Counter = collections.Counter()
    losses: list[dict] = []
    identity_changes: list[dict] = []
    damage_events: list[dict] = []
    timeline: list[dict] = []
    windows: dict[int, dict] = collections.defaultdict(
        lambda: {"damage": 0, "losses": 0, "attacker_hits": collections.Counter(),
                 "enemy_near_base": 0, "samples": 0})
    start_cells: dict[int, tuple[int, int]] = {}
    base_cell = None
    first_damage_t = None
    snapshot_before = None
    next_t = 0.0
    h7_state = None
    offense = collections.Counter()
    defeat_cutoff = (first_defeat_t + 2.0) if first_defeat_t is not None else float("inf")
    economy_hints = ("Miner", "Harvester", "Chrono Miner", "War Miner")

    for sample in states:
        frame = int(sample["frame"])
        t = t_of(frame)
        houses = {int(h[0]): {"name": h[1], "money": int(h[2]), "power_out": int(h[3]),
                              "power_drain": int(h[4]), "defeated": bool(h[5])}
                  for h in sample.get("houses", [])}
        h7 = houses.get(house_index)
        if h7:
            h7_state = h7
        objects = sample.get("objects", [])
        cur_addr = set()
        per_house = {p: {"objects": 0, "units": 0, "buildings": 0} for p in participants}
        by_owner_pos: dict[int, list[tuple[int, int, str, int]]] = collections.defaultdict(list)
        for o in objects:
            owner = int(o[0])
            if owner in participants:
                by_owner_pos[owner].append((int(o[3]), int(o[4]), str(o[1]), int(o[7])))
        window = int(t // 60) * 60
        w = windows[window]
        w["samples"] += 1
        # enemy presence around house7 base
        if base_cell is not None:
            near = 0
            for owner, points in by_owner_pos.items():
                if owner == house_index:
                    continue
                for px, py, _pt, _pm in points:
                    if math.hypot(px - base_cell[0], py - base_cell[1]) <= BASE_RADIUS:
                        near += 1
            w["enemy_near_base"] = max(w["enemy_near_base"], near)

        for o in objects:
            owner = int(o[0])
            if owner not in participants:
                continue
            type_name = str(o[1])
            health = int(o[2])
            x, y = int(o[3]), int(o[4])
            on_map, limbo = int(o[5]), int(o[6])
            mission = int(o[7])
            addr = int(o[8]) if len(o) > 8 else hash((owner, type_name, x, y))
            cur_addr.add(addr)
            per_house[owner]["objects"] += 1
            if on_map and not limbo:
                if is_building(type_name):
                    per_house[owner]["buildings"] += 1
                else:
                    per_house[owner]["units"] += 1
            if owner == house_index and mission in (1, 2, 3, 15, 29):
                offense[mission] += 1
            rec = tracks.get(addr)
            if rec is not None and (rec["owner"] != owner or rec["type"] != type_name):
                # native address reuse, or a type transformation (MCV -> Construction Yard)
                identity_changes.append({"addr": addr, "old_owner": rec["owner"],
                                         "old_type": rec["type"], "new_owner": owner,
                                         "new_type": type_name, "t": t})
                if rec["owner"] == house_index and t <= defeat_cutoff:
                    if "Construction Vehicle" in rec["type"] and rec["first_t"] < 60:
                        category = "mcv_deploy"
                    else:
                        category = "other_disappearance"
                    losses.append({
                        "t": rec["last_t"], "first_t": rec["first_t"], "type": rec["type"],
                        "cell": list(rec["cell"]), "hp_last": rec["hp"],
                        "max_hp": rec["max_hp"], "category": category,
                        "nearby_enemy_houses": {}, "nearby_enemy_types": {},
                    })
                rec = None
            if rec is None:
                max_hp = strength.get(int(o[9]), health) if len(o) > 9 else health
                tracks[addr] = {"owner": owner, "type": type_name, "hp": health,
                                "prev_hp": health, "max_hp": max_hp if max_hp > 0 else health,
                                "first_t": t, "last_t": t, "cell": [x, y],
                                "mission": mission, "prev_nearby": {}}
                if owner not in start_cells and "Construction Vehicle" in type_name:
                    start_cells[owner] = (x, y)
                    if owner == house_index:
                        base_cell = (x, y)
            else:
                rec["prev_hp"] = rec["hp"]
                rec["hp"] = health
                rec["last_t"] = t
                rec["cell"] = [x, y]
                rec["mission"] = mission
        # ---- damage + loss for house7 ----
        for addr, rec in list(tracks.items()):
            if rec["owner"] != house_index:
                if addr not in cur_addr:
                    missing[addr] += 1
                    if missing[addr] > LOSS_CONFIRM_SAMPLES:
                        tracks.pop(addr, None)
                        missing.pop(addr, None)
                continue
            if t > defeat_cutoff:
                continue
            if addr not in cur_addr:
                missing[addr] += 1
                if missing[addr] == LOSS_CONFIRM_SAMPLES:
                    x, y = rec["cell"]
                    nearby = collections.Counter()
                    nearby_types = collections.Counter()
                    for owner, points in by_owner_pos.items():
                        if owner == house_index:
                            continue
                        for px, py, ptype, _pm in points:
                            d = math.hypot(px - x, py - y)
                            if d <= 10:
                                nearby[owner] += 1
                                nearby_types[ptype] += 1
                    damaged = rec["hp"] < rec["max_hp"] * 0.98
                    if "Construction Vehicle" in rec["type"] and rec["last_t"] < 60:
                        category = "mcv_deploy"
                    elif damaged or nearby:
                        category = "combat_loss"
                    else:
                        category = "other_disappearance"
                    losses.append({
                        "t": rec["last_t"], "first_t": rec["first_t"],
                        "type": rec["type"], "cell": list(rec["cell"]),
                        "hp_last": rec["hp"], "max_hp": rec["max_hp"],
                        "category": category,
                        "nearby_enemy_houses": dict(nearby),
                        "nearby_enemy_types": dict(nearby_types.most_common(6)),
                    })
                    if category == "combat_loss":
                        w2 = windows[int(rec["last_t"] // 60) * 60]
                        w2["losses"] += 1
                continue
            missing.pop(addr, None)
            # damage between previous and current sample
            if rec["hp"] < rec["prev_hp"]:
                delta = rec["prev_hp"] - rec["hp"]
                if first_damage_t is None:
                    first_damage_t = t
                best = None
                best_d = 1e9
                for owner, points in by_owner_pos.items():
                    if owner == house_index:
                        continue
                    for px, py, ptype, pmission in points:
                        if any(hint in ptype for hint in economy_hints):
                            continue
                        d = math.hypot(px - rec["cell"][0], py - rec["cell"][1])
                        if pmission in (1, 2, 3, 15, 29):
                            d -= 2.0  # actively attacking units are more likely attackers
                        if d < best_d:
                            best_d = d
                            best = (owner, ptype)
                if best is not None and best_d <= ATTACK_RADIUS:
                    w["attacker_hits"][best[0]] += 1
                w["damage"] += delta
                damage_events.append({
                    "t": t, "type": rec["type"], "loss_hp": delta,
                    "cell": list(rec["cell"]),
                    "nearest_attacker": best, "dist": round(best_d, 1) if best else None,
                })
        # ---- 30 s timeline ----
        if t + 1e-9 >= next_t:
            timeline.append({"t": t, "frame": frame, "h7_money": h7["money"] if h7 else None,
                             "h7": dict(per_house.get(house_index, {})),
                             "houses": {str(k): {"money": v["money"], "defeated": v["defeated"]}
                                        for k, v in houses.items()}})
            next_t += 30.0
        if (first_defeat_t is not None and h7 is not None and not h7["defeated"]
                and t <= first_defeat_t):
            snapshot_before = {"t": t, "frame": frame,
                               "houses": {str(k): v for k, v in houses.items()},
                               "per_house": {str(k): v for k, v in per_house.items()}}
        if h7 is not None and h7["defeated"] and first_defeat_t is None:
            first_defeat_t = t

    # ---------------------------------------------------------------- summary ----
    combat_losses = [e for e in losses if e["category"] == "combat_loss"]
    first_combat_loss_t = combat_losses[0]["t"] if combat_losses else None
    neighbor_distances = []
    h7_start = None
    if house_index in start_cells:
        sx, sy = start_cells[house_index]
        h7_start = [sx, sy]
        for owner, (px, py) in sorted(start_cells.items()):
            if owner == house_index:
                continue
            neighbor_distances.append({"house": owner, "cell": [px, py],
                                       "distance": round(math.hypot(px - sx, py - sy), 1)})
    h7_build = next((h for h in match["houses"] if h["house_index"] == house_index), {})
    attack_windows = []
    for window in sorted(windows):
        w = windows[window]
        attack_windows.append({
            "t": window, "damage": int(w["damage"]), "combat_losses": int(w["losses"]),
            "attacker_hits": dict(w["attacker_hits"]),
            "enemy_near_base_max": int(w["enemy_near_base"]),
        })
    return {
        "run_dir": str(run_dir), "house_index": house_index,
        "first_defeat_t": first_defeat_t, "first_defeat_frame": first_defeat_frame,
        "fps": fps, "start_cell": h7_start, "base_cell": list(base_cell) if base_cell else None,
        "neighbor_distances": neighbor_distances,
        "build_order": h7_build.get("build_order", []),
        "build_count": h7_build.get("build_count"),
        "first_damage_t": first_damage_t, "first_combat_loss_t": first_combat_loss_t,
        "damage_events": damage_events,
        "losses": losses, "combat_losses": combat_losses,
        "identity_changes": identity_changes,
        "loss_category_counts": dict(collections.Counter(e["category"] for e in losses)),
        "attack_windows": attack_windows,
        "timeline": timeline,
        "offense_missions": dict(offense),
        "snapshot_before_defeat": snapshot_before,
        "participants": participants,
        "end_reason": match.get("end_reason"), "winner": match.get("winner_name"),
    }


def markdown(r: dict) -> str:
    h = r["house_index"]
    lines = [
        f"# 首个失败者 autopsy：house {h}",
        "",
        f"- 来源：`{r['run_dir']}`；首个被击败 **{r['first_defeat_t']} s**（frame {r['first_defeat_frame']}）",
        f"- 出生点：{r['start_cell']}；最近邻居：" + ", ".join(
            f"house {n['house']}={n['distance']} 格" for n in r["neighbor_distances"]),
        f"- 首次受伤 {r['first_damage_t']} s；首次**战斗损失** {r['first_combat_loss_t']} s；"
        f"损失分类 {r['loss_category_counts']}；地址复用/形态转换 {len(r['identity_changes'])} 次",
        f"- 工程：{r['build_count']} 个建筑；进攻 mission 计数 {r['offense_missions']}",
        "",
        "## 1. 建造顺序",
        "",
    ]
    for item in r["build_order"]:
        lines.append(f"- {item['first_seen_sim_s']:>7} s  {item['type_name']} @{item['cell']}")
    lines += ["", "## 2. 经济/兵力（30 s）", "",
              "| t(s) | 资金 | 对象 | 单位 | 建筑 | 敌方单位进入基地 25 格 |",
              "|---|---|---|---|---|---|"]
    enemy_base = {w["t"]: w["enemy_near_base_max"] for w in r["attack_windows"]}
    for row in r["timeline"]:
        h7 = row["h7"] or {}
        lines.append(f"| {row['t']:.0f} | {row['h7_money']} | {h7.get('objects')} | "
                     f"{h7.get('units')} | {h7.get('buildings')} | "
                     f"{enemy_base.get(int(row['t'] // 60) * 60, '')} |")
    lines += ["", "## 3. 每分钟：受伤 / 战斗损失 / 攻击者命中（最近敌方归属，INFERRED）", "",
              "| t(s) 起 | 受伤HP | 战斗损失 | 攻击者命中(house:次数) | 敌方进入基地最大数 |",
              "|---|---|---|---|---|"]
    for w in r["attack_windows"]:
        lines.append(f"| {w['t']} | {w['damage']} | {w['combat_losses']} | "
                     f"{w['attacker_hits']} | {w['enemy_near_base_max']} |")
    lines += ["", "## 4. 战斗损失明细（伤害/附近有敌人才算）", "",
              "| t(s) | 类型 | 位置 | HP | 附近敌方 house | 附近敌方兵种 |",
              "|---|---|---|---|---|---|"]
    for e in r["combat_losses"][:80]:
        lines.append(f"| {e['t']} | {e['type']} | {e['cell']} | {e['hp_last']}/{e['max_hp']} | "
                     f"{e['nearby_enemy_houses']} | {e['nearby_enemy_types']} |")
    snap = r.get("snapshot_before_defeat")
    if snap:
        lines += [f"", f"## 5. 被击败前一刻（t≈{snap['t']} s）", "",
                  "| house | 资金 | 对象 | 单位 | 建筑 | 已败 |", "|---|---|---|---|---|---|"]
        for owner in sorted(snap["houses"], key=int):
            if int(owner) not in r["participants"]:
                continue
            hs = snap["houses"][owner]
            ph = snap["per_house"].get(owner, {})
            lines.append(f"| {owner} | {hs['money']} | {ph.get('objects')} | {ph.get('units')} | "
                         f"{ph.get('buildings')} | {hs['defeated']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--house", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)
    result = analyze(pathlib.Path(args.run_dir), args.house)
    md = markdown(result)
    if args.out:
        pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.out).write_text(md, encoding="utf-8")
        print(f"wrote {args.out}")
    if args.json_out:
        pathlib.Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")
    print(json.dumps({k: result[k] for k in (
        "house_index", "first_defeat_t", "first_damage_t", "first_combat_loss_t",
        "start_cell", "loss_category_counts", "build_count", "offense_missions")},
        ensure_ascii=False, indent=2))
    print("damage events:", len(result["damage_events"]),
          "combat losses:", len(result["combat_losses"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
