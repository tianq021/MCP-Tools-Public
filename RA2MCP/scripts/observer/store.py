"""SQLite persistence + CSV export for observer match data.

The raw recording (JSONL.gz) stays the source of truth; this database stores the
derived, queryable tables plus provenance hashes.  A single batch database can hold
many matches: ``observer.sqlite``.

Tables
------
matches          one row per recording/match
houses           one row per participant house per match
house_samples    long time series: frame/money/object/unit/building counts per house
build_orders     first-seen building per house (type, frame, cell)
"""
from __future__ import annotations

import csv
import json
import pathlib
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    source_sha256 TEXT,
    source_path TEXT,
    source_label TEXT,
    map_name TEXT,
    seed INTEGER,
    frame_start INTEGER,
    frame_end INTEGER,
    wall_seconds REAL,
    sim_seconds REAL,
    fps_estimate REAL,
    sample_count INTEGER,
    end_reason TEXT,
    winner_house INTEGER,
    winner_name TEXT,
    first_defeated_house INTEGER,
    first_defeated_name TEXT,
    first_defeat_frame INTEGER,
    first_defeat_sim_s REAL,
    participants_json TEXT,
    elimination_order_json TEXT,
    transient_winner_json TEXT,
    schema TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS houses (
    match_id TEXT,
    house_index INTEGER,
    name TEXT,
    defeat_frame INTEGER,
    defeat_sim_s REAL,
    elimination_rank INTEGER,
    final_defeated INTEGER,
    final_is_winner INTEGER,
    final_is_loser INTEGER,
    final_money INTEGER,
    final_power_output INTEGER,
    final_power_drain INTEGER,
    final_objects INTEGER,
    final_units INTEGER,
    final_buildings INTEGER,
    peak_units INTEGER,
    peak_objects INTEGER,
    first_attack_frame INTEGER,
    first_attack_sim_s REAL,
    offensive_samples INTEGER,
    build_count INTEGER,
    is_winner INTEGER,
    is_first_defeated INTEGER,
    PRIMARY KEY (match_id, house_index)
);

CREATE TABLE IF NOT EXISTS house_samples (
    match_id TEXT,
    sample_idx INTEGER,
    frame INTEGER,
    sim_s REAL,
    house_index INTEGER,
    money INTEGER,
    power_out INTEGER,
    power_drain INTEGER,
    objects INTEGER,
    units INTEGER,
    buildings INTEGER,
    PRIMARY KEY (match_id, sample_idx, house_index)
);

CREATE TABLE IF NOT EXISTS build_orders (
    match_id TEXT,
    house_index INTEGER,
    seq INTEGER,
    type_name TEXT,
    first_seen_frame INTEGER,
    first_seen_sim_s REAL,
    cell_x INTEGER,
    cell_y INTEGER,
    classification TEXT,
    PRIMARY KEY (match_id, house_index, seq)
);

CREATE INDEX IF NOT EXISTS idx_houses_match ON houses(match_id);
CREATE INDEX IF NOT EXISTS idx_samples_match ON house_samples(match_id, frame);
CREATE INDEX IF NOT EXISTS idx_build_match ON build_orders(match_id, house_index, seq);
"""


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    db_path = pathlib.Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_match(conn: sqlite3.Connection, result: dict) -> None:
    """Insert one extracted match (idempotent: replaces the same match_id)."""
    mid = result["match_id"]
    conn.execute("DELETE FROM matches WHERE match_id=?", (mid,))
    conn.execute("DELETE FROM houses WHERE match_id=?", (mid,))
    conn.execute("DELETE FROM house_samples WHERE match_id=?", (mid,))
    conn.execute("DELETE FROM build_orders WHERE match_id=?", (mid,))

    conn.execute(
        """INSERT INTO matches (match_id, source_sha256, source_path, source_label, map_name,
            seed, frame_start, frame_end, wall_seconds, sim_seconds, fps_estimate, sample_count,
            end_reason, winner_house, winner_name, first_defeated_house, first_defeated_name,
            first_defeat_frame, first_defeat_sim_s, participants_json, elimination_order_json,
            transient_winner_json, schema)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            mid, result.get("source_sha256"), result.get("source_path"),
            result.get("source_label"), result.get("map_name"), result.get("seed"),
            result.get("frame_start"), result.get("frame_end"), result.get("wall_seconds"),
            result.get("sim_seconds"), result.get("fps_estimate"), result.get("sample_count"),
            result.get("end_reason"), result.get("winner_house"), result.get("winner_name"),
            result.get("first_defeated_house"), result.get("first_defeated_name"),
            result.get("first_defeat_frame"), result.get("first_defeat_sim_s"),
            json.dumps(result.get("participants"), ensure_ascii=False),
            json.dumps(result.get("elimination_order"), ensure_ascii=False),
            json.dumps(result.get("transient_winner_flags"), ensure_ascii=False),
            result.get("schema"),
        ),
    )

    for row in result.get("houses", []):
        conn.execute(
            """INSERT OR REPLACE INTO houses (match_id, house_index, name, defeat_frame,
                defeat_sim_s, elimination_rank, final_defeated, final_is_winner, final_is_loser,
                final_money, final_power_output, final_power_drain, final_objects, final_units,
                final_buildings, peak_units, peak_objects, first_attack_frame, first_attack_sim_s,
                offensive_samples, build_count, is_winner, is_first_defeated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, row["house_index"], row["name"], row.get("defeat_frame"),
                row.get("defeat_sim_s"), row.get("elimination_rank"),
                _int(row.get("final_defeated")), _int(row.get("final_is_winner")),
                _int(row.get("final_is_loser")), row.get("final_money"),
                row.get("final_power_output"), row.get("final_power_drain"),
                row.get("final_objects"), row.get("final_units"), row.get("final_buildings"),
                row.get("peak_units"), row.get("peak_objects"), row.get("first_attack_frame"),
                row.get("first_attack_sim_s"), row.get("offensive_samples"),
                row.get("build_count"), _int(row.get("is_winner")),
                _int(row.get("is_first_defeated")),
            ),
        )
        for seq, build in enumerate(row.get("build_order", [])):
            conn.execute(
                """INSERT OR REPLACE INTO build_orders (match_id, house_index, seq, type_name,
                    first_seen_frame, first_seen_sim_s, cell_x, cell_y, classification)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    mid, row["house_index"], seq, build["type_name"],
                    build["first_seen_frame"], build["first_seen_sim_s"],
                    build["cell"][0] if build.get("cell") else None,
                    build["cell"][1] if build.get("cell") else None,
                    build.get("classification"),
                ),
            )

    for sample in result.get("timeline", []):
        for house_index, bucket in sample.get("houses", {}).items():
            conn.execute(
                """INSERT OR REPLACE INTO house_samples (match_id, sample_idx, frame, sim_s,
                    house_index, money, power_out, power_drain, objects, units, buildings)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mid, sample["sample_idx"], sample["frame"], sample["sim_s"], int(house_index),
                    bucket.get("money"), bucket.get("power_out"), bucket.get("power_drain"),
                    bucket.get("objects"), bucket.get("units"), bucket.get("buildings"),
                ),
            )
    conn.commit()


def _int(value):
    if value is None:
        return None
    return 1 if value else 0


def export_csv(conn: sqlite3.Connection, out_dir: pathlib.Path) -> list[str]:
    """Export analysis-friendly CSVs (UTF-8 BOM so Excel opens them correctly)."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    queries = {
        "matches.csv": "SELECT * FROM matches ORDER BY match_id",
        "houses.csv": "SELECT * FROM houses ORDER BY match_id, house_index",
        "house_timeline.csv": ("SELECT match_id, sample_idx, frame, sim_s, house_index, money, "
                               "power_out, power_drain, objects, units, buildings "
                               "FROM house_samples ORDER BY match_id, frame, house_index"),
        "build_orders.csv": ("SELECT * FROM build_orders ORDER BY match_id, house_index, seq"),
    }
    for filename, sql in queries.items():
        rows = conn.execute(sql).fetchall()
        path = out_dir / filename
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            if rows:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(row))
            else:
                fh.write("")
        written.append(str(path))
    return written


def match_report(result: dict) -> str:
    """Human-readable Markdown report for one match."""
    f0 = result.get("frame_start")
    lines = [
        f"# 观察者对局提取报告 `{result['match_id']}`",
        "",
        f"- 来源：`{result.get('source_path')}`",
        f"- 来源 SHA256：`{result.get('source_sha256')}`",
        f"- 地图：`{result.get('map_name')}`；种子：`{result.get('seed')}`",
        f"- 帧范围：{f0} → {result.get('frame_end')}；"
        f"模拟时长 {result.get('sim_seconds')} s；墙钟 {result.get('wall_seconds')} s；"
        f"fps≈{result.get('fps_estimate')}",
        f"- 面板 house：{result.get('house_names')}",
        f"- 参战 house：{result.get('participants')}；观察者：{result.get('observer_indices')}",
        f"- 结束原因：**{result.get('end_reason')}**",
        f"- 胜者：**{result.get('winner_name')}**（house {result.get('winner_house')}）",
        f"- 首个被击败：**{result.get('first_defeated_name')}**"
        f"（house {result.get('first_defeated_house')}，"
        f"frame {result.get('first_defeat_frame')}，"
        f"≈{result.get('first_defeat_sim_s')} s）",
        "",
        "## 淘汰顺序",
        "",
        "| rank | house | name | defeat frame | defeat sim s |",
        "|---|---|---|---|---|",
    ]
    for item in result.get("elimination_order", []):
        lines.append(
            f"| {item['elimination_rank']} | {item['house_index']} | {item['name']} | "
            f"{item['defeat_frame']} | {item.get('defeat_sim_s', '')} |")
    lines += ["", "## 各 house 汇总", "",
              "| house | name | 结束状态 | 资金(末) | 对象(末) | 单位(末) | 建筑(末) | 峰值单位 | "
              "首进攻(s) | 建筑数 | 淘汰名次 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in result.get("houses", []):
        status = "winner" if row.get("is_winner") else (
            "defeated" if row.get("final_defeated") else "survivor")
        lines.append(
            f"| {row['house_index']} | {row['name']} | {status} | {row.get('final_money')} | "
            f"{row.get('final_objects')} | {row.get('final_units')} | {row.get('final_buildings')} | "
            f"{row.get('peak_units')} | {row.get('first_attack_sim_s')} | {row.get('build_count')} | "
            f"{row.get('elimination_rank')} |")
    lines += ["", "## 首个被击败 house 的建造顺序", ""]
    first = next((r for r in result.get("houses", []) if r.get("is_first_defeated")), None)
    if first and first.get("build_order"):
        for item in first["build_order"]:
            lines.append(f"- {item['first_seen_sim_s']:>7} s  {item['type_name']}  @{item['cell']}")
    else:
        lines.append("（无）")
    lines += ["", "## 数据质量提示", "",
              f"- 瞬时 `is_winner` 假信号：`{result.get('transient_winner_flags')}`",
              "- 胜负判定以『结束时唯一未 defeated 的参战 house』为准；"
              "瞬时 `is_winner/is_loser` 仅作交叉校验。",
              "- 建造/单位分类基于位置移动启发式（有名称关键词兜底），"
              "精确分类需 live collector 记录对象地址与类型指针。"]
    return "\n".join(lines) + "\n"
