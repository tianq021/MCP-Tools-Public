"""按「产区 → 区域筒仓」自动路由结晶，不依赖写死的地块 ID。

数据全部实时读游戏：
  sr2_plots         所有地块的类型 / 坐标（存档模型，未加载地块也有坐标）
  sr2_corrals       每个围栏里养的史莱姆
  sr2_slime_diet    史莱姆产物（Largo 直接给两种结晶）
  sr2_storage_list  地块筒仓的 storage_key（写入目标）

规则：
  1) 某种结晶的「产区」= 产出它的围栏 / 焚化炉 / 池塘所在的地块；
  2) 该产区的「区域筒仓」= 水平距离最近的 SILO 地块；
  3) 找不到产区的结晶不做猜测，返回 None，调用方保持原兜底逻辑。

用法：
    python -B tool/zone_router.py            # 打印自动识别出的映射
    from zone_router import build_home_map   # 代码里直接取映射
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
from bridge_client import BridgeError, call  # noqa: E402


class RouterError(Exception):
    pass


def _rpc(name: str, args: dict | None = None) -> dict:
    response = call(name, args or {})
    if not response.get("ok"):
        error = response.get("error") or {}
        raise RouterError(f"{error.get('code', 'bridge_error')} {error.get('message', '')}".strip())
    data = response.get("data") or {}
    if data.get("available") is False:
        raise RouterError(str(data.get("reason", "unavailable")))
    return data


def _pages(name: str, args: dict | None = None, limit: int = 100) -> list[dict]:
    """按 next_offset 翻页取全部条目。"""
    rows: list[dict] = []
    offset: int | None = 0
    seen: set[int] = set()
    base = dict(args or {})
    while offset is not None and offset not in seen:
        seen.add(offset)
        data = _rpc(name, {**base, "limit": limit, "offset": offset})
        rows.extend(data.get("items") or [])
        offset = data.get("next_offset")
    return rows


def _short(value: str) -> str:
    return (value or "").split(".")[-1]


CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "zone_router_cache.json"
)


def _save_cache(payload: dict) -> None:
    """记住「地块 → 筒仓 key」和「结晶 → 产区筒仓」，玩家离开牧场后仍能按产区路由。"""
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"keys": {}, "homes": {}}
    if not isinstance(data, dict):
        return {"keys": {}, "homes": {}}
    if "keys" in data or "homes" in data:
        return {"keys": dict(data.get("keys") or {}), "homes": dict(data.get("homes") or {})}
    # 兼容旧格式：整个文件就是 {plot_id: key}
    return {"keys": {str(k): str(v) for k, v in data.items()}, "homes": {}}


def _distance(a: dict, b: dict) -> float:
    """水平距离：区域归属只看平面距离，忽略高度差。"""
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["z"]) - float(b["z"]))


def build_home_map() -> dict:
    """返回 {'homes': {结晶短名: 筒仓 storage_key}, 'regions': {结晶: 产区地块}, ...}。"""
    plots = _pages("sr2_plots")
    storages = _pages("sr2_storage_list")
    corrals = _pages("sr2_corrals")
    slimes = _pages("sr2_slime_diet")

    plot_by_id = {p.get("id"): p for p in plots if p.get("id")}
    # 筒仓：坐标取地块表（未加载也有坐标），写入 key 取 storage_list；
    # 玩家在别的场景（如灰色迷宫）时 storage_list 只剩场景容器，这时用上次缓存
    # 的「地块 → 筒仓 key」映射继续按产区路由。
    key_by_plot: dict[str, str] = {}
    for store in storages:
        if store.get("kind") != "plot_silo" or not store.get("storage_key"):
            continue
        plot_id = str(store["storage_key"]).split("/")[0].replace("plot:", "")
        key_by_plot[plot_id] = store["storage_key"]
    cache = _load_cache()
    if not key_by_plot:
        key_by_plot = cache["keys"]
    silos: list[dict] = []
    for plot_id, store_key in key_by_plot.items():
        plot = plot_by_id.get(plot_id)
        if not plot or not plot.get("position"):
            continue
        silos.append({"plot_id": plot_id, "key": store_key, "position": plot["position"]})
    if not silos:
        # 玩家在别的场景（灰色迷宫等）时，地块坐标是空的：用上次在牧场算好的
        # 「结晶 → 产区筒仓」映射继续按产区路由。
        if cache.get("homes"):
            return {
                "homes": cache["homes"],
                "regions": {},
                "silos": {},
                "corral_count": 0,
                "notes": ["使用缓存的产区映射（当前场景没有地块坐标）"],
                "cached": True,
            }
        raise RouterError("没有可用的地块筒仓")

    # 史莱姆 → 产物结晶
    products: dict[str, list[str]] = {}
    for row in slimes:
        stem = _short(str(row.get("id") or ""))
        if not stem:
            continue
        products[stem] = [_short(p) for p in (row.get("products") or [])]

    def nearest_silo(position: dict) -> dict:
        return min(silos, key=lambda s: _distance(s["position"], position))

    homes: dict[str, str] = {}
    regions: dict[str, str] = {}
    notes: list[str] = []

    # 1) 围栏：按里面养的史莱姆产物分配
    corral_ids = {c.get("id") for c in corrals}
    for corral in corrals:
        plot_id = corral.get("id")
        plot = plot_by_id.get(plot_id or "")
        if not plot or not plot.get("position"):
            continue
        silo = nearest_silo(plot["position"])
        produced: set[str] = set()
        for slime_id in (corral.get("slimes") or {}):
            stem = _short(slime_id)
            produced.update(products.get(stem) or [])
        for plort in produced:
            if plort.endswith("Plort"):
                homes.setdefault(plort, silo["key"])
                regions.setdefault(plort, plot_id)
        if not produced:
            notes.append(f"围栏 {plot_id} 没识别出产物")

    # 2) 焚化炉 / 池塘这类固定产区
    fixed = {"INCINERATOR": "FirePlort", "POND": "PuddlePlort"}
    for plot in plots:
        plort = fixed.get(plot.get("type") or "")
        if not plort or not plot.get("position"):
            continue
        silo = nearest_silo(plot["position"])
        homes.setdefault(plort, silo["key"])
        regions.setdefault(plort, plot.get("id"))

    if homes:
        _save_cache({"keys": key_by_plot, "homes": homes})
    return {
        "homes": homes,
        "regions": regions,
        "silos": {s["key"]: s for s in silos},
        "corral_count": len(corral_ids),
        "notes": notes,
    }


def home_map(silent: bool = True) -> dict[str, str]:
    """只要 {结晶短名: 筒仓 key}；读不到时返回空 dict（调用方走兜底）。"""
    try:
        return build_home_map()["homes"]
    except (RouterError, BridgeError, KeyError, ValueError):
        return {}


def main() -> int:
    try:
        info = build_home_map()
    except (RouterError, BridgeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    label = {s["key"]: s["plot_id"] for s in info["silos"].values()}
    print(f"自动识别到 {len(info['homes'])} 种结晶的产区：")
    for plort, key in sorted(info["homes"].items()):
        print(f"  {plort:<16} → {label.get(key, key)}  ({info['regions'].get(plort)})")
    for note in info["notes"]:
        print("提示：", note)
    print(json.dumps({"silos": len(info["silos"]), "corrals": info["corral_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
