"""Read-only diagnosis of collected episodes: contact timing, movement direction,
damage accounting bugs, and combat-opportunity thresholds.

Writes reports/dsh-combat-diagnosis.json and prints a compact summary.
No game, no training, no file mutation outside the report.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EP = ROOT / "reports" / "dsh-learning" / "episodes"
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import schema  # noqa: E402

CELL = 256


def read_rows(path):
    meta, result, rows = None, None, []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        t = rec.get("type")
        if t == "meta":
            meta = rec.get("meta")
        elif t == "result":
            result = rec.get("result")
        else:
            rows.append(rec)
    return meta, result, rows


def approx_seconds(frame, frame0, fps):
    return round((frame - frame0) / fps, 1)


def base_cell(obs_row):
    for o in obs_row.get("own_objects") or []:
        if o.get("type_name") == "Allied Construction Yard":
            return (o["cell"][0], o["cell"][1])
    return None


def infantry(obs_row):
    return [o for o in obs_row.get("own_objects") or []
            if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY"]


def diagnose(eid, fps=62.5):
    meta, result, rows = read_rows(EP / f"{eid}.jsonl")
    obs = [r for r in rows if r.get("type") == "observation"]
    frame0 = obs[0]["frame"]
    elapsed = (result or {}).get("elapsed_s") or 1.0
    fps_est = round((obs[-1]["frame"] - frame0) / max(1.0, elapsed), 2)
    fps = fps_est if 30 < fps_est < 120 else fps

    timeline = []
    for target_s in (30, 60, 90, 120, 150, 180, 210, 240, 280):
        tf = frame0 + int(target_s * fps)
        if tf > obs[-1]["frame"]:
            break
        o = min(obs, key=lambda x: abs(x["frame"] - tf))
        inf = infantry(o)
        ys = [u["cell"][1] for u in inf]
        xs = [u["cell"][0] for u in inf]
        timeline.append({
            "t_approx_s": approx_seconds(o["frame"], frame0, fps),
            "frame": o["frame"],
            "infantry": len(inf),
            "inf_min_y": min(ys) if ys else None,
            "inf_max_y": max(ys) if ys else None,
            "inf_mean_x": round(sum(xs) / len(xs), 1) if xs else None,
            "visible_enemies": len(o.get("visible_enemies") or []),
            "base": base_cell(o),
        })

    first_visible = next((r for r in obs if r.get("visible_enemies")), None)
    first_attack = None
    last_obs = None
    for r in rows:
        if r.get("type") == "observation":
            last_obs = r
        elif r.get("type") == "action" and r.get("action") == "ATTACK":
            first_attack = {"frame": (last_obs or {}).get("frame"),
                            "target": r.get("target_address"),
                            "visible": len((last_obs or {}).get("visible_enemies") or []),
                            "error": r.get("error")}
            break
    # first enemy hp decrease (visible enemy damage)
    first_enemy_damage = None
    last_hp = {}
    for r in rows:
        if r.get("type") != "observation":
            continue
        hp_now = {}
        for o in r.get("own_objects") or []:
            hp_now[("own", o["addr"])] = o["health"]
        for e in r.get("visible_enemies") or []:
            hp_now[("enemy", e["addr"])] = e["health"]
        for key, hp in hp_now.items():
            if key in last_hp and hp < last_hp[key] and key[0] == "enemy":
                first_enemy_damage = {"frame": r.get("frame"),
                                      "addr": key[1],
                                      "before": last_hp[key], "after": hp}
                break
        if first_enemy_damage:
            break
        last_hp = hp_now

    # damage accounting check: stale-address reuse
    stale = []
    last = {}
    for r in rows:
        if r.get("type") != "observation":
            continue
        hp_now = {o["addr"]: o["health"] for o in r.get("own_objects") or []}
        for e in r.get("visible_enemies") or []:
            hp_now[e["addr"]] = -e["health"]
        for a, hp in hp_now.items():
            if a in last and hp < last[a]:
                stale.append({"frame": r.get("frame"), "addr": a,
                              "before": last[a], "after": hp})
            last[a] = hp
        # NOTE: simulate the *buggy* old accounting (never drop stale keys)
    corrected = schema.derive_metrics(rows)
    # corrected accounting: drop entries that disappeared before comparing
    own_lost = 0
    last = {}
    for r in rows:
        if r.get("type") != "observation":
            continue
        hp_now = {o["addr"]: o["health"] for o in r.get("own_objects") or []}
        for e in r.get("visible_enemies") or []:
            hp_now[e["addr"]] = -e["health"]
        for a, hp in hp_now.items():
            if a in last:
                prev = last[a]
                if hp >= 0 and prev >= 0 and hp < prev:
                    own_lost += prev - hp
        last = hp_now
    return {
        "episode_id": eid,
        "duration_s": elapsed,
        "fps_est": fps,
        "frames": [frame0, obs[-1]["frame"]],
        "base_cell_first": base_cell(obs[0]),
        "base_cell_last": base_cell(obs[-1]),
        "first_visible_enemy": None if first_visible is None else {
            "frame": first_visible["frame"],
            "t_approx_s": approx_seconds(first_visible["frame"], frame0, fps),
            "count": len(first_visible["visible_enemies"]),
        },
        "first_attack": None if first_attack is None else {
            **first_attack,
            "t_approx_s": None if first_attack["frame"] is None
            else approx_seconds(first_attack["frame"], frame0, fps),
        },
        "first_enemy_damage": None if first_enemy_damage is None else {
            **first_enemy_damage,
            "t_approx_s": approx_seconds(first_enemy_damage["frame"], frame0, fps),
        },
        "movement": {
            "base_y": (base_cell(obs[-1]) or [None, None])[1],
            "advance_direction_requested": "south(+y) hardcoded by rule policy",
            "infantry_y_at_end": [u["cell"][1] for u in infantry(obs[-1])][:8],
            "timeline": timeline,
        },
        "damage_accounting": {
            "reported_result_own_hp_lost": (result or {}).get("metrics", {}).get(
                "own_hp_lost"),
            "reported_result_damage_dealt": (result or {}).get("metrics", {}).get(
                "damage_dealt"),
            "schema_derive_metrics": corrected,
            "recomputed_own_hp_lost_dropping_stale_addrs": own_lost,
            "stale_addr_decreases": stale[:5],
            "note": "old derive_metrics kept disappeared addresses; an MCV(1000hp) "
                    "address reused by a 750hp CY was mis-counted as 250 own HP lost",
        },
    }


def main():
    out = {"generated_at": schema.now_wall(), "episodes": []}
    for eid in ("ep-0003", "ep-0004", "ep-0006"):
        p = EP / f"{eid}.jsonl"
        if p.exists():
            out["episodes"].append(diagnose(eid))
    dest = ROOT / "reports" / "dsh-combat-diagnosis.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for e in out["episodes"]:
        print(json.dumps({
            "episode": e["episode_id"],
            "base_first": e["base_cell_first"], "base_last": e["base_cell_last"],
            "first_visible": e["first_visible_enemy"],
            "first_attack": e["first_attack"],
            "first_enemy_damage": e["first_enemy_damage"],
            "reported_own_hp_lost": e["damage_accounting"]["reported_result_own_hp_lost"],
            "recomputed_own_hp_lost": e["damage_accounting"][
                "recomputed_own_hp_lost_dropping_stale_addrs"],
            "stale": e["damage_accounting"]["stale_addr_decreases"],
        }, ensure_ascii=False))
    print(f"written: {dest}")


if __name__ == "__main__":
    main()
