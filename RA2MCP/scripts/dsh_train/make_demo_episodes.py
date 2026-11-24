"""Generate synthetic demo episodes so the training pipeline can be smoke-tested
without the game running.

These episodes are NOT game data (meta.synthetic = true). They follow exactly the
same JSONL schema the game runner produces, so switching to real episodes later
is a pure data-path change.

Usage:
  python scripts/dsh_train/make_demo_episodes.py \
      --out reports/dsh-learning/episodes-demo --episodes 4 --rows 160
"""
import argparse
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from episode_io import write_jsonl  # noqa: E402
from spec import CELL  # noqa: E402

CELL_PX = CELL


def make_episode(rng, ep_index, rows):
    base_cell = (91, 58)
    money = 10000
    buildings = [{"type": "Allied Construction Yard", "cell": list(base_cell), "hp": 1000}]
    infantry = []
    centroid = [base_cell[0] * CELL_PX + 128, base_cell[1] * CELL_PX + 128]
    mapped = 220
    out_rows = []
    frame = 300
    outcome = rng.choice(["defeat", "timeout", "defeat"])
    for i in range(rows):
        frame += rng.randint(25, 40)
        mode = i / max(1, rows - 1)
        if i < 4:
            orders = [{"action": "DEPLOY", "ack_ms": 16, "err": None}]
        elif len(buildings) < 3:
            orders = [{"action": "PRODUCE",
                       "type": "Allied Power Plant" if len(buildings) < 2 else "Allied Barracks",
                       "ack_ms": 15, "err": None}]
            if rng.random() < 0.35:
                buildings.append({"type": "Allied Power Plant" if len(buildings) < 2
                                  else "Allied Barracks",
                                  "cell": [base_cell[0] + rng.randint(-4, 4),
                                           base_cell[1] + rng.randint(-4, 4)], "hp": 1000})
                orders.append({"action": "PLACE",
                               "cell": [base_cell[0] + 2, base_cell[1] + 2]})
        elif len(infantry) < 6 and rng.random() < 0.6:
            orders = [{"action": "PRODUCE", "type": "GI", "ack_ms": 15, "err": None}]
            if rng.random() < 0.5:
                infantry.append({"type": "GI",
                                 "cell": [base_cell[0] + rng.randint(-2, 2),
                                          base_cell[1] + rng.randint(1, 3)], "hp": 125})
        elif mode < 0.6:
            d = rng.choice([(0, 1), (0, -1), (1, 0), (-1, 0)])
            dest = [centroid[0] + d[0] * rng.randint(4, 8) * CELL_PX,
                    centroid[1] + d[1] * rng.randint(4, 8) * CELL_PX]
            orders = [{"action": "MOVE", "dest": dest, "ack_ms": 16, "err": None}]
            centroid[0] += d[0] * 2 * CELL_PX
            centroid[1] += d[1] * 2 * CELL_PX
            mapped += rng.randint(2, 12)
        else:
            orders = []

        visible = []
        n_vis = rng.randint(0, 3) if i > rows // 3 else 0
        for k in range(n_vis):
            vis = {"addr": 900000 + k,
                   "type": rng.choice(["GI", "Grizzly Tank", "Power Plant", "Pill Box"]),
                   "owner": rng.choice(["Computer", "Neutral"]),
                   "cell": [(centroid[0] // CELL_PX) + rng.randint(-5, 5),
                            (centroid[1] // CELL_PX) + rng.randint(-5, 5)],
                   "hp": rng.choice([125, 300, 800, 1000]),
                   "cloak_state": 0}
            visible.append(vis)
        if visible and rng.random() < 0.55:
            target = rng.choice(visible)
            target["addr"] += 100          # keep id stable-ish per row
            orders = [{"action": "ATTACK", "target": target["addr"],
                       "type": target["type"], "hp": target["hp"],
                       "ack_ms": 16, "err": None}]
            target["addr"] -= 100

        money = max(1000, money - rng.randint(0, 400) + rng.randint(0, 300))
        if rng.random() < 0.25 and infantry:
            infantry[0]["hp"] = max(1, infantry[0]["hp"] - rng.randint(1, 40))
        objects = [{"addr": 500000 + idx, "type": b["type"], "cell": b["cell"],
                    "hp": b["hp"], "mission": "Mission_Guard"}
                   for idx, b in enumerate(buildings)]
        objects += [{"addr": 600000 + idx, "type": u["type"], "cell": u["cell"],
                     "hp": u["hp"], "mission": rng.choice(["Mission_Guard", "Mission_Move"])}
                    for idx, u in enumerate(infantry)]
        out_rows.append({
            "t": round(i * 0.5, 1), "frame": frame,
            "money": money,
            "human": {"buildings": len(buildings), "units": len(infantry),
                      "infantry": len(infantry), "objects": objects},
            "visible_enemies": visible,
            "map": {"mapped": mapped, "shrouded": 16097 - max(0, mapped - 220)},
            "orders": orders,
        })
    meta = {"episode_id": f"demo-{ep_index:02d}", "synthetic": True,
            "started_at": "2026-10-29T00:00:00",
            "params": {"demo": True}, "type_names_count": 3642}
    result = {"episode_id": f"demo-{ep_index:02d}",
              "outcome": {"kind": outcome, "detail": "synthetic"},
              "duration_s": rows * 0.5, "params": {"demo": True},
              "metrics": {"damage_dealt": rng.randint(0, 200),
                          "own_hp_lost": rng.randint(0, 300),
                          "buildings_peak": len(buildings),
                          "units_peak": len(infantry),
                          "visibility_violations": 0}}
    return meta, out_rows, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/dsh-learning/episodes-demo")
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--rows", type=int, default=160)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    total = 0
    for i in range(a.episodes):
        meta, rows, result = make_episode(rng, i, a.rows)
        write_jsonl(out / f"{meta['episode_id']}.jsonl", meta, rows, result)
        total += len(rows)
    print(json.dumps({"out": str(out), "episodes": a.episodes, "rows": total,
                      "synthetic": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
