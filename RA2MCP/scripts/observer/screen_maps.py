"""Offline screening of 8-player maps: water fraction + start spread (no game launch).

Uses the checked-in minimap preview PNGs (``test-game/Maps/**/*.png``) and the static
map index (``reports/map-data-2026-11-01/maps_index.json``) to rank 8-start maps by
"how much water" and how spread out the 8 spawns are.  This is a heuristic filter only;
the final gate must be the runtime map SoA connectivity check done by the live collector.

Usage:
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts\\observer\\screen_maps.py \
      --out reports/observer-collect-2026-11-03/MAP_SCREENING.md

Requires Pillow (available in F:\\conda\\envs\\python_mmx_LLM).
"""
from __future__ import annotations

import argparse
import colorsys
import json
import math
import pathlib

ROOT = pathlib.Path(r"E:\RA2MCP")
MAP_INDEX = ROOT / "reports/map-data-2026-11-01/maps_index.json"
MAP_ROOTS = [ROOT / "test-game/Maps", ROOT / "test-game-wdzs/map"]


def water_fraction(png_path: pathlib.Path) -> dict:
    from PIL import Image

    image = Image.open(png_path)
    if image.mode in ("P", "PA"):
        image = image.convert("RGBA")
    image = image.convert("RGB")
    width, height = image.size
    small = image.resize((max(1, width // 4), max(1, height // 4)))
    pixels = list(small.getdata())
    n = len(pixels)
    water = green = brown = dark = 0
    for r, g, b in pixels:
        if max(r, g, b) < 40:
            dark += 1
            continue
        hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        deg = hue * 360
        if 175 <= deg <= 265 and sat > 0.25 and val > 0.15:
            water += 1
        elif 70 <= deg <= 170 and sat > 0.15:
            green += 1
        elif 15 <= deg <= 65 and sat > 0.12:
            brown += 1
    return {"water_pct": round(water / n * 100, 1),
            "green_pct": round(green / n * 100, 1),
            "brown_pct": round(brown / n * 100, 1),
            "dark_pct": round(dark / n * 100, 1),
            "preview_size": [width, height]}


def start_stats(starts: list[dict]) -> dict:
    if not starts:
        return {"start_count": 0, "spread": 0.0, "centroid": None}
    xs = [s["x"] for s in starts]
    ys = [s["y"] for s in starts]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = math.sqrt(sum((x - cx) ** 2 + (y - cy) ** 2 for x, y in zip(xs, ys)) / len(xs))
    return {"start_count": len(starts),
            "spread": round(spread, 1),
            "centroid": [round(cx, 1), round(cy, 1)]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "reports/observer-collect-2026-11-03/MAP_SCREENING.md"))
    parser.add_argument("--max-water", type=float, default=8.0)
    args = parser.parse_args(argv)

    index = json.loads(MAP_INDEX.read_text(encoding="utf-8"))
    rows = []
    for entry in index["maps"]:
        starts = entry.get("starts_0_7") or []
        if len(starts) < 8:
            continue
        map_path = pathlib.Path(entry["path"])
        png = map_path.with_suffix(".png")
        if not png.is_file():
            continue
        stats = water_fraction(png)
        stats.update(start_stats(starts))
        stats.update({"name": entry["name"], "theater": entry.get("theater"),
                      "path": str(map_path), "png": str(png)})
        rows.append(stats)

    rows.sort(key=lambda r: (r["water_pct"], -r["spread"]))
    land = [r for r in rows if r["water_pct"] <= args.max_water]

    lines = [
        "# 8 人图离线筛选（预览图水体比例 + 出生点分布，2026-11-03）",
        "",
        "> 目的：P1 冒烟误选 `8_mayflower_ffa`（海图/群岛，地面 AI 无法交战）后，离线筛出**陆地连通**的 8 出生点地图。",
        "> 方法：读取 `test-game/Maps/**/*.png` 预览图，按 HSV 蓝色水体像素占比 + 出生点分布排序。",
        "> 局限：**预览图启发式**（雪地图可能把白雪误判为水体），最终必须用运行时 `map_data_soa` 的 land_type 做 BFS 连通性校验（live collector 已加此门禁）。",
        f"> 生成时间：{__import__('time').strftime('%Y-%m-%dT%H:%M:%S%z')}；共分析 {len(rows)} 张 8 出生点地图。",
        "",
        f"## 陆地候选（water% ≤ {args.max_water:.1f}，按水体占比升序）",
        "",
        "| 排名 | 地图 | 剧院 | 水体% | 绿地% | 沙土% | 出生点数 | 出生点离散度 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rank, row in enumerate(land, start=1):
        lines.append(
            f"| {rank} | `{row['name']}` | {row['theater']} | {row['water_pct']} | "
            f"{row['green_pct']} | {row['brown_pct']} | {row['start_count']} | {row['spread']} |")

    lines += ["", "## 被排除的高水体/海图（示例）", "",
              "| 地图 | 水体% | 说明 |", "|---|---|---|"]
    for row in rows:
        if row["water_pct"] > 35:
            lines.append(f"| `{row['name']}` | {row['water_pct']} | 高水体/群岛，地面 AI 可能无法交战 |")

    lines += [
        "",
        "## 推荐（待运行时连通性门禁确认）",
        "",
        "1. `8_valley_of_the_exiles.map`（沙漠、8 起点对称环、预览无水体；中央高地+斜坡，适合 FFA 交战）",
        "2. `8_cliffside_slide.map`（沙漠悬崖、无水体；通道多、接触快）",
        "3. `8_invasion.map`（城市道路、几乎无水体；移动快、交战早）",
        "4. `8_dont_step_on_the_crocodile.map` / `8_corwick_forest.map`（备选陆地）",
        "",
        "> 已排除：`8_mayflower_ffa`（水体约 39%，**P1 实测 60 秒零交战**）、`8_andalusia`（运河/岛屿，多水体）、"
        "`8_octo_bay`/`8_malaboo_bay`/`8_oceania`/`8_storm`/`8_the_eye`/`8_polyanthus` 等按水体比例排除或待定（雪图需人工复核）。",
        "",
        "## 机器可读结果",
        "",
        "```json",
        json.dumps([{k: r[k] for k in ("name", "water_pct", "green_pct", "brown_pct",
                                       "start_count", "spread", "theater")}
                    for r in rows], ensure_ascii=False, indent=1),
        "```",
    ]
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(rows)} maps, {len(land)} land candidates)")
    for row in land[:10]:
        print(f"  {row['name']:42s} water={row['water_pct']:5.1f}% spread={row['spread']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
