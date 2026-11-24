#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
predict.py — 莫里斯黑市位置预测模块

输入日期（YYYY-MM-DD），输出：
  - 周序号 week_number（以 2025-09-12 为 Week 1 起始，7 天为一窗）
  - 轮次 round 与循环内位置 position（默认按 53 周循环，循环长度未验证，结果带警告）
  - 若该 position 在数据库中有观测支撑的地点，输出地点详情；否则输出 UNKNOWN

注意：
  - 每周刷新发生在窗口内周三/周四 19:00 ET 附近（来源间有 1 天歧义，见 README）。
    本模块按"整日属于哪个 7 天窗"预测，窗口切换日给出的结果在 19:00 ET 前后可能跨窗。
  - 位置刷新（周三 ET）与库存/冷却（购买后 30 分钟）是两回事，本模块只处理位置刷新。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

WEEK1_START = date(2025, 9, 12)          # BL4 发售日，Week 1 窗口起点
DEFAULT_CYCLE = 53                        # 社区声称值，未验证


def load_db() -> tuple[dict, dict]:
    with open(DATA / "locations.json", encoding="utf-8") as f:
        loc_db = json.load(f)
    with open(DATA / "weeks_observed.json", encoding="utf-8") as f:
        weeks = json.load(f)
    return loc_db, weeks


def week_of(d: date) -> int:
    """日期 -> 周序号（Week 1 窗口 = 2025-09-12 ~ 2025-09-18）。"""
    if d < WEEK1_START:
        raise ValueError(f"{d} 早于 BL4 发售日 {WEEK1_START}，无莫里斯黑市")
    return (d - WEEK1_START).days // 7 + 1


def round_and_position(week_number: int, cycle: int = DEFAULT_CYCLE) -> tuple[int, int]:
    rnd = (week_number - 1) // cycle + 1
    pos = ((week_number - 1) % cycle) + 1
    return rnd, pos


def refresh_note(d: date) -> str:
    """窗口内的刷新点提示（周三/周四 19:00 ET 歧义）。"""
    wd = d.weekday()  # Mon=0 ... Sun=6
    if wd in (2, 3):  # Wed 或 Thu
        return (f"⚠ {d} 是刷新窗口边缘日：刷新约在 19:00 ET（来源间周三/周四有 1 天歧义），"
                f"当天预测可能跨窗，建议按实际游戏内显示核对。")
    return ""


def predict(d: date, cycle: int = DEFAULT_CYCLE) -> dict:
    loc_db, weeks = load_db()
    week_number = week_of(d)
    rnd, pos = round_and_position(week_number, cycle)

    cycle_info = loc_db.get("cycle", {})
    verified_pos = {e["position"]: e["location_key"]
                    for e in cycle_info.get("entries", []) if e.get("status") == "verified"}
    verified_len = cycle_info.get("verified_length_weeks")

    entry = verified_pos.get(pos)
    loc = next((l for l in loc_db["locations"] if l["key"] == entry), None) if entry else None

    # 该周的直接观测（若有）
    direct = [w for w in weeks.get("weeks", []) if w.get("week_number") == week_number
              and w.get("status") == "verified"]

    return {
        "date": d.isoformat(),
        "week_number": week_number,
        "window_start": (WEEK1_START + timedelta(days=(week_number - 1) * 7)).isoformat(),
        "window_end": (WEEK1_START + timedelta(days=week_number * 7 - 1)).isoformat(),
        "round": rnd,
        "position_in_cycle": pos,
        "cycle_assumed": cycle,
        "cycle_verified": verified_len is not None,
        "predicted_location_key": entry,
        "predicted_location": {
            "en": loc["en"], "jp": loc["jp"], "region": loc["region"],
            "district": loc["district"],
        } if loc else None,
        "direct_observation": direct[0]["location_key"] if direct else None,
        "warning": (
            "" if verified_len is not None else
            f"循环长度 {cycle} 周未经验证：目前仅验证 Week 1-9。"
            f"Round 2 及以后的 position 映射是外推值。"
        ),
        "refresh_note": refresh_note(d),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    try:
        d = datetime.strptime(argv[0], "%Y-%m-%d").date()
    except ValueError:
        print(f"错误：日期格式应为 YYYY-MM-DD，收到 {argv[0]!r}")
        return 2
    r = predict(d)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
