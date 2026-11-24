"""Analyze the recorded God's Toy AI match: build order, army, attacks, economy."""
import collections
import json
import pathlib
import statistics
import sys

RUN = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                   r"E:\RA2MCP\reports\ai-teacher\wdzs-godstoy\live-full\20261030-002526")
OUT = RUN / "analysis"
OUT.mkdir(exist_ok=True)

samples = [json.loads(line) for line in
           (RUN / "states_full.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
           if line.strip()]
first, last = samples[0], samples[-1]
wall = last["wall"] - first["wall"]
frames = last["frame"] - first["frame"]
print(f"samples={len(samples)} frames={first['frame']}->{last['frame']} "
      f"wall={wall:.1f}s fps={frames/max(1,wall):.1f}")

# ---- houses ----
house_names = {h[0]: h[1] for h in last["houses"]}
owners = sorted(house_names)
print("houses:", [(k, house_names[k]) for k in owners])

money_series = {o: [] for o in owners}
for idx, s in enumerate(samples):
    for h in s["houses"]:
        money_series[h[0]].append((idx, h[2]))
for o in owners:
    vals = [v for _, v in money_series[o]]
    print(f"owner {o} {house_names[o]}: money start={vals[0]} end={vals[-1]} "
          f"min={min(vals)} max={max(vals)}")

# ---- position stability to classify buildings vs mobile units ----
tracks = collections.defaultdict(list)  # (owner,type) -> [(sample_idx, x, y)]
for idx, s in enumerate(samples):
    for o in s["objects"]:
        owner, kind, health, x, y, on_map, limbo, mission = o
        if owner < 0 or not on_map:
            continue
        tracks[(owner, kind)].append((idx, x, y))

def mobility(key):
    pts = tracks[key]
    if len(pts) < 5:
        return 0.0
    xs = [p[1] for p in pts]
    ys = [p[2] for p in pts]
    return statistics.pstdev(xs) + statistics.pstdev(ys)

mobile_units = set()
for key in tracks:
    if mobility(key) > 1.2:
        mobile_units.add(key)

# known infantry/vehicle keywords as a fallback for rarely-moving units
MOBILE_HINTS = ("GI", "Trooper", "Conscript", "Dog", "Spy", "Engineer", "Tanya", "Boris",
                "Yuri", "Initiate", "Brute", "Virus", "Tank", "IFV", "Miner", "Harvester",
                "Apocalypse", "V3", "Tesla", "Desolator", "Seal", "Chrono", "Prism Tank",
                "Mirage", "Grizzly", "Rhino", "Lasher", "Gattling", "Flak Track", "Terror Drone")

def is_unit(owner, kind):
    if (owner, kind) in mobile_units:
        return True
    return any(h in kind for h in MOBILE_HINTS)

# ---- build order per AI ----
build_order = {}
for o in owners:
    if o == 0:
        continue
    seen = {}
    for idx, s in enumerate(samples):
        for owner, kind, health, x, y, on_map, limbo, mission in s["objects"]:
            if owner != o or on_map == 0 or limbo:
                continue
            if is_unit(owner, kind):
                continue
            if kind not in seen:
                seen[kind] = (idx, x, y)
    build_order[o] = sorted(seen.items(), key=lambda kv: kv[1][0])

print("\n=== build order (AI owners; wall sec since capture start) ===")
for o, items in build_order.items():
    print(f"owner {o}:")
    for kind, (idx, x, y) in items[:20]:
        sec = samples[idx]["wall"] - first["wall"]
        print(f"  {sec:6.0f}s  {kind}  @({x},{y})")

# ---- army size and composition over time ----
print("\n=== army / building counts at checkpoints ===")
checkpoints = [int(len(samples) * f) for f in (0.2, 0.4, 0.6, 0.8, 0.99)]
for idx in checkpoints:
    s = samples[min(idx, len(samples) - 1)]
    counts = collections.Counter()
    comp = collections.Counter()
    for owner, kind, health, x, y, on_map, limbo, mission in s["objects"]:
        if owner < 0 or not on_map:
            continue
        if is_unit(owner, kind):
            counts[owner] += 1
            comp[(owner, kind)] += 1
    sec = s["wall"] - first["wall"]
    line = f"t={sec:5.0f}s "
    for o in owners:
        if o == 0:
            continue
        top = [f"{k}:{v}" for (oo, k), v in comp.most_common(40) if oo == o][:4]
        line += f"| AI{o} units={counts.get(o,0)} [{','.join(top)}] "
    print(line)

# ---- battle activity: health sum losses per owner in 30s buckets ----
print("\n=== battle intensity (total HP lost per 30s bucket) ===")
bucket = collections.defaultdict(lambda: collections.defaultdict(float))
prev_hp = {}
for idx, s in enumerate(samples):
    sec = s["wall"] - first["wall"]
    b = int(sec // 30)
    hp_now = collections.defaultdict(float)
    for owner, kind, health, x, y, on_map, limbo, mission in s["objects"]:
        if owner >= 0:
            hp_now[owner] += health
    for o, hp in hp_now.items():
        if o in prev_hp and prev_hp[o] > hp:
            bucket[b][o] += prev_hp[o] - hp
        prev_hp[o] = hp
for b in sorted(bucket):
    row = " ".join(f"AI{o}={v:.0f}" for o, v in sorted(bucket[b].items()) if o != 0)
    human = bucket[b].get(0, 0)
    print(f"  {b*30:3d}-{(b+1)*30:3d}s  {row}   human={human:.0f}")

# ---- attack waves near human base ----
human_buildings = collections.defaultdict(list)
for idx, s in enumerate(samples):
    for owner, kind, health, x, y, on_map, limbo, mission in s["objects"]:
        if owner == 0 and on_map and not is_unit(0, kind):
            human_buildings[kind].append((x, y))
base_pts = [(statistics.mean(p[0] for p in pts), statistics.mean(p[1] for p in pts))
            for pts in human_buildings.values()]
base = (statistics.mean(p[0] for p in base_pts), statistics.mean(p[1] for p in base_pts)) if base_pts else None
print(f"\nhuman base center: {base}")

if base:
    waves = collections.defaultdict(list)
    for idx, s in enumerate(samples):
        sec = s["wall"] - first["wall"]
        near = collections.defaultdict(int)
        for owner, kind, health, x, y, on_map, limbo, mission in s["objects"]:
            if owner <= 0 or not on_map or not is_unit(owner, kind):
                continue
            d = ((x - base[0]) ** 2 + (y - base[1]) ** 2) ** 0.5
            if d < 12:
                near[owner] += 1
        for o, n in near.items():
            if n >= 5:
                waves[o].append((sec, n))
    for o in sorted(waves):
        secs = [w[0] for w in waves[o]]
        print(f"AI{o} units near human base (>=5): first={secs[0]:.0f}s "
              f"last={secs[-1]:.0f}s count_of_samples={len(secs)} max={max(w[1] for w in waves[o])}")

# ---- final composition ----
print("\n=== final army composition per AI ===")
for o in owners:
    if o == 0:
        continue
    comp = collections.Counter()
    for owner, kind, health, x, y, on_map, limbo, mission in last["objects"]:
        if owner == o and on_map and is_unit(o, kind):
            comp[kind] += 1
    print(f"AI{o}: total={sum(comp.values())} {comp.most_common(12)}")

# save machine-readable summary
summary = {
    "samples": len(samples), "frames": [first["frame"], last["frame"]],
    "wall_seconds": round(wall, 1), "fps": round(frames / max(1, wall), 2),
    "houses": [{"index": k, "name": house_names[k]} for k in owners],
    "money": {o: money_series[o][-1][1] for o in owners},
    "build_orders": {str(o): [[k, samples[v[0]]["wall"] - first["wall"], v[1], v[2]]
                              for k, v in items] for o, items in build_order.items()},
    "human_base": base,
}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
print("\nwrote", OUT / "summary.json")
