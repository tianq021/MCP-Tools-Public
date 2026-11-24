r"""Data-completeness audit for an observer run directory.

Reads ``_runs_observer/<run>/`` and checks that *all* collected artifacts and
full-state samples are present, parseable, continuous and internally consistent:

  states.jsonl         1 Hz full states: frame monotonicity, gap stats, houses,
                       objects rows (incl. address/type pointers), observer presence
  raw/map_source.map   raw map bytes (sha256)
  raw/map_static_meta.json   static map sections/sizes/waypoints
  raw/map_soa_meta.json + raw/map_soa.json.gz   runtime map SoA
  raw/object_types.json   type catalogue (pointer -> type metadata)
  raw/events.jsonl     network event buffer (may be empty; recorded as info)
  RESULT.json          collector result: status/samples/cleanup/protected
  extract/match.json   match extraction: participants, houses, eliminations, winner
  csv/*.csv            exported tables
  observer.sqlite      SQLite DB + row counts
  MATCH_REPORT.md      generated markdown report
  config/spawn*.ini    launch config + map copy

Outputs DATA_COMPLETENESS.json and DATA_COMPLETENESS.md into the run dir and prints
a PASS/FAIL summary.  Usage:

  E:\RA2MCP\.venv\Scripts\python.exe scripts\observer\audit_completeness.py ^
      --run-dir E:\RA2MCP\_runs_observer\observer-aienh-p5-01
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import sqlite3
import statistics
import sys


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ok(checks: list, name: str, passed: bool, detail: str = "") -> None:
    checks.append({"check": name, "pass": bool(passed), "detail": detail})


def audit(run_dir: pathlib.Path) -> dict:
    run_dir = pathlib.Path(run_dir)
    checks: list[dict] = []
    info: dict = {"run_dir": str(run_dir), "files": {}}
    files = sorted(p for p in run_dir.rglob("*") if p.is_file())
    info["total_files"] = len(files)
    info["total_size"] = sum(p.stat().st_size for p in files)

    # ---------------- RESULT.json ----------------
    result_path = run_dir / "RESULT.json"
    result = {}
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            ok(checks, "result_present", True, f"{result_path.stat().st_size} B")
        except Exception as exc:  # noqa: BLE001
            ok(checks, "result_present", False, f"parse error {exc}")
    else:
        ok(checks, "result_present", False, "missing RESULT.json")
    info["result"] = {k: result.get(k) for k in
                      ("run_id", "status", "end_reason", "samples", "max_objects", "seed",
                       "game_pid", "protected_unchanged")}
    cleanup = result.get("cleanup") or {}
    ok(checks, "cleanup_ok", bool(cleanup.get("cleanup_ok"))
       and int(cleanup.get("remaining_game_processes", 1)) == 0,
       f"remaining={cleanup.get('remaining_game_processes')}")
    ok(checks, "protected_unchanged", bool(result.get("protected_unchanged")), "")

    # ---------------- states.jsonl ----------------
    states_path = run_dir / "raw" / "states.jsonl"
    frames: list[int] = []
    samples = 0
    obj_counts: list[int] = []
    house_counts: set[int] = set()
    observer_flags = 0
    bad_lines = 0
    missing_objects = 0
    row_len_bad = 0
    pointer_missing = 0
    first_ts = last_ts = None
    obj_row_len: dict[int, int] = {}
    if states_path.is_file():
        with open(states_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                except Exception:  # noqa: BLE001
                    bad_lines += 1
                    continue
                samples += 1
                frames.append(int(s.get("frame", -1)))
                observers = s.get("observers") or s.get("observer") or []
                if observers:
                    observer_flags += 1
                houses = s.get("houses") or []
                if len(houses) == 10 and str(houses[0][1]).lower() in (
                        "<human player>", "observer", "human"):
                    observer_flags += 1
                wall = s.get("wall")
                if first_ts is None:
                    first_ts = wall
                last_ts = wall
                house_counts.add(len(houses))
                objs = s.get("objects")
                if objs is None:
                    missing_objects += 1
                    continue
                obj_counts.append(len(objs))
                if objs:
                    lens = {len(o) for o in objs if isinstance(o, list)}
                    for ln in lens:
                        obj_row_len[ln] = obj_row_len.get(ln, 0) + 1
                    if len(objs[0]) < 10:
                        row_len_bad += 1
                    else:
                        pointer_missing += sum(1 for o in objs
                                              if len(o) < 10 or o[9] in (0, None))
        ok(checks, "states_present", True,
           f"{states_path.stat().st_size} B / {samples} samples")
        ok(checks, "states_parse", bad_lines == 0, f"bad_lines={bad_lines}")
        ok(checks, "states_objects_field", missing_objects == 0,
           f"samples_missing_objects={missing_objects}")
        ok(checks, "states_row_length_10", row_len_bad == 0 and not any(
            ln < 10 for ln in obj_row_len), f"row_len_hist={obj_row_len}")
        monotonic = all(b > a for a, b in zip(frames, frames[1:]))
        ok(checks, "states_frame_monotonic", monotonic,
           f"frames={frames[0] if frames else None}..{frames[-1] if frames else None}")
        diffs = [b - a for a, b in zip(frames, frames[1:])]
        gaps = [d for d in diffs if d > 0]
        info["sample_stats"] = {
            "samples": samples,
            "frame_first": frames[0] if frames else None,
            "frame_last": frames[-1] if frames else None,
            "interval_min": min(gaps) if gaps else None,
            "interval_median": statistics.median(gaps) if gaps else None,
            "interval_max": max(gaps) if gaps else None,
            "max_gap_frames": max(gaps) if gaps else None,
            "objects_min": min(obj_counts) if obj_counts else None,
            "objects_median": statistics.median(obj_counts) if obj_counts else None,
            "objects_max": max(obj_counts) if obj_counts else None,
            "house_counts": sorted(house_counts),
            "observer_flags_samples": observer_flags,
            "pointer_missing_rows": pointer_missing,
            "first_capture_ts": first_ts,
            "last_capture_ts": last_ts,
        }
        ok(checks, "states_house_counts", house_counts == {10},
           f"house_counts={sorted(house_counts)}")
        ok(checks, "states_observer_flag", observer_flags == samples,
           f"{observer_flags}/{samples} samples carry observers")
        # expected ~number of simulated seconds: last-first over expected interval
        exp = (frames[-1] - frames[0]) / 62.4 if len(frames) > 1 else 0
        ok(checks, "states_duration_20min", exp >= 1050,
           f"~{exp:.1f} simulated s")
        # sampling continuity: no gap > 3x median
        med = statistics.median(gaps) if gaps else 0
        big = [d for d in gaps if med and d > 3 * med]
        ok(checks, "states_sampling_continuity", not big,
           f"gaps>3x median({med}): {big[:5]}")
    else:
        ok(checks, "states_present", False, "missing raw/states.jsonl")
        info["sample_stats"] = {}

    # ---------------- raw map / meta / types ----------------
    raw = run_dir / "raw"
    def file_check(name: str, min_size: int = 1) -> pathlib.Path | None:
        p = raw / name
        if p.is_file() and p.stat().st_size >= min_size:
            info["files"][f"raw/{name}"] = {
                "size": p.stat().st_size, "sha256": sha256(p)}
            ok(checks, f"raw_{name}", True, f"{p.stat().st_size} B")
            return p
        ok(checks, f"raw_{name}", False, "missing or too small")
        return None

    map_src = file_check("map_source.map", 1024)
    static_meta = file_check("map_static_meta.json", 100)
    soa_meta = file_check("map_soa_meta.json", 50)
    soa_gz = file_check("map_soa.json.gz", 100)
    types_path = file_check("object_types.json", 1024)
    events_path = raw / "events.jsonl"
    if events_path.is_file():
        info["files"]["raw/events.jsonl"] = {"size": events_path.stat().st_size,
                                             "sha256": sha256(events_path)}
        info["events_lines"] = sum(1 for _ in open(events_path, "rb"))
    if static_meta is not None:
        try:
            sm = json.loads(static_meta.read_text(encoding="utf-8"))
            info["map_static"] = {"sections": len(sm.get("sections") or {}),
                                  "declared": sm.get("declared_sizes"),
                                  "waypoints": len(sm.get("waypoints") or {})}
            ok(checks, "map_static_parse", True, json.dumps(info["map_static"])[:200])
        except Exception as exc:  # noqa: BLE001
            ok(checks, "map_static_parse", False, str(exc))
    if soa_meta is not None and soa_gz is not None:
        try:
            sm = json.loads(soa_meta.read_text(encoding="utf-8"))
            with gzip.open(soa_gz, "rt", encoding="utf-8") as fh:
                soa = json.load(fh)
            keys = sorted(soa.keys()) if isinstance(soa, dict) else []
            width = sm.get("map_width") or sm.get("width")
            height = sm.get("map_height") or sm.get("height")
            info["map_soa"] = {"meta": sm, "keys": keys[:20],
                               "width": width, "height": height}
            expected = width * height if width and height else None
            length_ok = True
            if expected:
                for k in ("land_type", "height", "tiberium"):
                    if k in soa and isinstance(soa[k], list) and len(soa[k]) != expected:
                        length_ok = False
            ok(checks, "map_soa_parse", True, f"keys={len(keys)} {width}x{height}")
            ok(checks, "map_soa_length", length_ok, f"expected={expected}")
        except Exception as exc:  # noqa: BLE001
            ok(checks, "map_soa_parse", False, str(exc))
    if types_path is not None:
        try:
            types = json.loads(types_path.read_text(encoding="utf-8"))
            n = len(types)
            ptr = sum(1 for t in types if t.get("pointer"))
            cost = sum(1 for t in types if t.get("cost") not in (None, 0))
            strength = sum(1 for t in types if t.get("strength") not in (None, 0))
            armor = sum(1 for t in types if t.get("armor_type") is not None)
            named = sum(1 for t in types if t.get("name"))
            info["object_types"] = {"count": n, "with_pointer": ptr,
                                    "with_cost": cost, "with_strength": strength,
                                    "with_armor_type": armor, "named": named,
                                    "sample_keys": sorted(types[0].keys()) if types else []}
            ok(checks, "object_types_count", n >= 1000, f"{n} types")
            ok(checks, "object_types_fields",
               ptr == n and strength > 500 and cost > 150 and armor >= 3000,
               f"pointer={ptr} cost={cost} strength={strength} armor_type={armor} "
               f"named={named}")
        except Exception as exc:  # noqa: BLE001
            ok(checks, "object_types_parse", False, str(exc))

    # ---------------- extract/match.json ----------------
    match_path = run_dir / "extract" / "match.json"
    if match_path.is_file():
        try:
            match = json.loads(match_path.read_text(encoding="utf-8"))
            parts = match.get("participants") or []
            houses = match.get("houses") or []
            elim = match.get("elimination_order") or []
            info["match"] = {
                "participants": parts,
                "houses": len(houses),
                "fps": match.get("fps_estimate"),
                "sample_count": match.get("sample_count"),
                "eliminations": [(e.get("house_index"), e.get("defeat_frame"))
                                 for e in elim],
                "winner": match.get("winner_name"),
                "end_reason": match.get("end_reason"),
                "first_defeated_house": match.get("first_defeated_house"),
                "first_defeat_frame": match.get("first_defeat_frame"),
            }
            ok(checks, "match_parse", True,
               f"{match_path.stat().st_size} B")
            ok(checks, "match_participants", len(parts) == 7, f"{parts}")
            ok(checks, "match_houses", len(houses) == 7, f"{len(houses)}")
            ok(checks, "match_sample_count", match.get("sample_count") == samples,
               f"{match.get('sample_count')} vs states {samples}")
            ok(checks, "match_build_orders",
               all(h.get("build_order") is not None for h in houses), "")
        except Exception as exc:  # noqa: BLE001
            ok(checks, "match_parse", False, str(exc))
    else:
        ok(checks, "match_parse", False, "missing extract/match.json")

    # ---------------- CSV ----------------
    expected_csv = {
        "matches.csv": 1, "houses.csv": 7, "house_timeline.csv": 100,
        "build_orders.csv": 10,
    }
    for name, min_rows in expected_csv.items():
        p = run_dir / "csv" / name
        if not p.is_file():
            ok(checks, f"csv_{name}", False, "missing")
            continue
        with open(p, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        ok(checks, f"csv_{name}", len(rows) - 1 >= min_rows,
           f"{len(rows) - 1} data rows")

    # ---------------- SQLite ----------------
    db_path = run_dir / "observer.sqlite"
    if db_path.is_file():
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            tables = [r[0] for r in con.execute(
                "select name from sqlite_master where type='table'").fetchall()]
            counts = {}
            for t in tables:
                counts[t] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
            con.close()
            info["sqlite"] = {"size": db_path.stat().st_size, "tables": counts}
            ok(checks, "sqlite_tables", len(tables) >= 3, f"{counts}")
        except Exception as exc:  # noqa: BLE001
            ok(checks, "sqlite_tables", False, str(exc))
    else:
        ok(checks, "sqlite_tables", False, "missing observer.sqlite")

    # ---------------- report / config ----------------
    report = run_dir / "MATCH_REPORT.md"
    ok(checks, "match_report", report.is_file() and report.stat().st_size > 500,
       f"{report.stat().st_size if report.is_file() else 0} B")
    spawn_ini = run_dir / "config" / "spawn.ini"
    if spawn_ini.is_file():
        text = spawn_ini.read_text(encoding="utf-8", errors="replace")
        info["spawn_ini"] = {"spectator": "IsSpectator=Yes" in text,
                             "player_count": "PlayerCount=8" in text,
                             "ai_players": "AIPlayers=7" in text}
        ok(checks, "config_spectator", info["spawn_ini"]["spectator"]
           and info["spawn_ini"]["player_count"]
           and info["spawn_ini"]["ai_players"], json.dumps(info["spawn_ini"]))
    else:
        ok(checks, "config_spectator", False, "missing config/spawn.ini")
    spawnmap = run_dir / "config" / "spawnmap.ini"
    if spawnmap.is_file() and map_src is not None:
        same = sha256(spawnmap) == info["files"]["raw/map_source.map"]["sha256"]
        ok(checks, "config_map_matches_raw", same,
           f"spawnmap={spawnmap.stat().st_size} B")
    else:
        ok(checks, "config_map_matches_raw", False, "missing spawnmap/map_source")

    # ---------------- mod install record (optional) ----------------
    mi = run_dir / "MOD_INSTALLED.json"
    if mi.is_file():
        try:
            m = json.loads(mi.read_text(encoding="utf-8-sig"))
            info["mod_installed"] = m
            ok(checks, "mod_installed_record", True,
               f"mode={m.get('mode')} files={len(m.get('files') or [])}")
        except Exception as exc:  # noqa: BLE001
            ok(checks, "mod_installed_record", False, str(exc))

    failed = [c for c in checks if not c["pass"]]
    info["checks"] = checks
    info["pass"] = not failed
    info["failed_count"] = len(failed)
    info["failed"] = failed
    return info


def md(info: dict) -> str:
    lines = [
        f"# 数据完整性审计：{info['run_dir']}", "",
        f"- 总体：**{'PASS' if info['pass'] else 'FAIL'}**（失败 {info['failed_count']} 项）",
        f"- 文件数 {info['total_files']}，总大小 {info['total_size'] / 1e6:.2f} MB",
    ]
    ss = info.get("sample_stats") or {}
    if ss:
        lines += [
            "", "## 采样连续性", "",
            f"- 样本数：{ss.get('samples')}（frame {ss.get('frame_first')} → "
            f"{ss.get('frame_last')}）",
            f"- 采样间隔：min {ss.get('interval_min')} / median "
            f"{ss.get('interval_median')} / max {ss.get('interval_max')} frames",
            f"- 对象数：min {ss.get('objects_min')} / median "
            f"{ss.get('objects_median')} / max {ss.get('objects_max')}",
            f"- 每样本 house 数：{ss.get('house_counts')}；observer 标记样本："
            f"{ss.get('observer_flags_samples')}/{ss.get('samples')}",
            f"- 对象指针缺失行：{ss.get('pointer_missing_rows')}",
            f"- 采集时间戳：{ss.get('first_capture_ts')} → {ss.get('last_capture_ts')}",
        ]
    if info.get("match"):
        lines += ["", "## 对局提取", "",
                  f"- participants：{info['match'].get('participants')}",
                  f"- fps≈{info['match'].get('fps')}，sample_count="
                  f"{info['match'].get('sample_count')}",
                  f"- 淘汰顺序：{info['match'].get('eliminations')}；winner="
                  f"{info['match'].get('winner')}；end_reason={info['match'].get('end_reason')}"]
    if info.get("object_types"):
        ot = info["object_types"]
        lines += ["", "## 对象类型表", "",
                  f"- {ot.get('count')} types；cost {ot.get('with_cost')}、strength "
                  f"{ot.get('with_strength')}、armor {ot.get('with_armor')}"]
    if info.get("map_soa"):
        ms = info["map_soa"]
        lines += ["", "## 地图数据", "",
                  f"- runtime {ms.get('width')}×{ms.get('height')}；SoA keys: "
                  f"{ms.get('keys')}"]
    if info.get("sqlite"):
        lines += ["", "## SQLite", "", f"- 表行数：{info['sqlite'].get('tables')}"]
    lines += ["", "## 检查项", "", "| 检查 | 结果 | 详情 |", "|---|---|---|"]
    for c in info["checks"]:
        lines.append(f"| {c['check']} | {'PASS' if c['pass'] else '**FAIL**'} | "
                     f"{c['detail']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args(argv)
    run_dir = pathlib.Path(args.run_dir)
    info = audit(run_dir)
    (run_dir / "DATA_COMPLETENESS.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / "DATA_COMPLETENESS.md").write_text(md(info), encoding="utf-8")
    print(f"PASS={info['pass']} failed={info['failed_count']} "
          f"samples={(info.get('sample_stats') or {}).get('samples')}")
    for f in info["failed"]:
        print("  FAIL:", f["check"], "|", f["detail"])
    print("wrote", run_dir / "DATA_COMPLETENESS.md")
    return 0 if info["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
