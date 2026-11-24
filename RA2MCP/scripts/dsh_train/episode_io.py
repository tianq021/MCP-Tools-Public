"""Read/write episode JSONL files produced by the RA2 learner runner.

Format (one episode = one file `<episode_id>.jsonl`):
  line 1  : {"meta": {...}}
  lines 2..N: {"t": ..., "frame": ..., "human": {...}, "visible_enemies": [...],
               "map": {...}, "orders": [...], "events": [...]}
  last line: {"result": {...}}

This module is deliberately tolerant: rows missing optional keys are kept and
handled by features.build_obs.
"""
import json
import pathlib


def load_episode(path):
    path = pathlib.Path(path)
    meta = {}
    rows = []
    result = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "meta" in rec:
                meta = rec["meta"]
            elif "result" in rec:
                result = rec["result"]
            else:
                rows.append(rec)
    return {"path": str(path), "meta": meta, "rows": rows, "result": result}


def iter_episodes(episodes_dir):
    episodes_dir = pathlib.Path(episodes_dir)
    for p in sorted(episodes_dir.glob("*.jsonl")):
        if p.name.endswith(".corrupt"):
            continue
        ep = load_episode(p)
        if ep["rows"]:
            yield ep


def write_jsonl(path, meta, rows, result):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.write(json.dumps({"result": result}, ensure_ascii=False) + "\n")
