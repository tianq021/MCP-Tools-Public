"""离线 XP 加成计算与能力说明。

用户要求：原版 XP 不变，只额外发 bonus：
  bonus = floor(base_xp * extra_percent / 100)
  total = base_xp + bonus

本模块只做离线计算/预览/能力查询，不写真实 XP/等级/存档，也不在运行中游戏发 XP。
数据：`data/xp_models.json`
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from . import DATA_DIR

XP_MODELS_FILE = DATA_DIR / "xp_models.json"
DEFAULT_LEVEL_CAP = 80
PREVIEW_BANNER = (
    "===== PREVIEW ONLY / 未实机验证 =====\n"
    "这是离线 XP 计算与运行时方案说明；没有写真实 XP/等级/存档，也没有在运行中游戏发 XP。\n"
)

_cache: Optional[dict[str, Any]] = None


def load_models(refresh: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is None or refresh:
        if not XP_MODELS_FILE.is_file():
            raise FileNotFoundError(f"缺少 XP 模型数据: {XP_MODELS_FILE}")
        _cache = json.loads(XP_MODELS_FILE.read_text(encoding="utf-8"))
    return _cache


def _validate_base_xp(base_xp: Any) -> int:
    if isinstance(base_xp, bool) or not isinstance(base_xp, int):
        raise ValueError("base_xp 必须是整数（游戏结算经验；不是 TotalExpPoints 之类的中间量）")
    if base_xp < 0:
        raise ValueError("base_xp 不能为负数")
    return base_xp


def _validate_percent(extra_percent: Any) -> float:
    if isinstance(extra_percent, bool) or not isinstance(extra_percent, (int, float)):
        raise ValueError("extra_percent 必须是数字（例如 50 表示 +50%）")
    value = float(extra_percent)
    if not math.isfinite(value):
        raise ValueError("extra_percent 必须是有限数字")
    if value < 0:
        raise ValueError("extra_percent 不能为负数（不能扣 XP）")
    return value


def bonus(base_xp: int, extra_percent: float) -> int:
    """额外奖励 XP = floor(base_xp * extra_percent / 100)。"""
    base = _validate_base_xp(base_xp)
    pct = _validate_percent(extra_percent)
    return int(math.floor(base * pct / 100.0))


def calculate(
    base_xp: int,
    extra_percent: float,
    *,
    current_level: Optional[int] = None,
    level_cap: int = DEFAULT_LEVEL_CAP,
    apply_at_cap: bool = False,
) -> dict[str, Any]:
    """计算 bonus/total，并给出满级/等级上限/输入边界信息。

    不计算真实升级级数（不知道 1-80 每级 XP 曲线），level_gain 固定为 None。
    """
    base = _validate_base_xp(base_xp)
    pct = _validate_percent(extra_percent)
    if isinstance(level_cap, bool) or not isinstance(level_cap, int) or level_cap <= 0:
        raise ValueError("level_cap 必须是正整数")
    if current_level is not None:
        if isinstance(current_level, bool) or not isinstance(current_level, int) or current_level < 0:
            raise ValueError("current_level 必须是非负整数或 None")
    b = int(math.floor(base * pct / 100.0))
    total = base + b
    at_cap = current_level is not None and current_level >= level_cap
    applies = (not at_cap) or bool(apply_at_cap)
    notes: list[str] = []
    if base == 0:
        notes.append("base_xp=0：bonus/total 都为 0，不应发 XP。")
    if pct == 0:
        notes.append("extra_percent=0：只保留原 XP。")
    if pct > 1000:
        notes.append(f"extra_percent={pct:g} 非常大：检查是否误输入（本工具不限制上限，但实机需谨慎）。")
    if at_cap:
        notes.append(
            f"当前等级 {current_level} >= 上限 {level_cap}：默认不应用 bonus（applies=False）；"
            "满级后游戏是否仍记录 XP 未知；OP 不靠 XP 升级。"
        )
    if applies and not at_cap:
        notes.append("level_gain 未计算：本工具不知道每级所需 XP 曲线；只给 bonus/total。")
    return {
        "base_xp": base,
        "extra_percent": pct,
        "bonus": b,
        "total": total,
        "current_level": current_level,
        "level_cap": level_cap,
        "at_cap": at_cap,
        "applies": applies,
        "level_gain": None,
        "formula": "bonus = floor(base_xp * extra_percent / 100); total = base_xp + bonus",
        "notes": notes,
    }


def table(bases: list[int], extra_percent: float) -> list[dict[str, Any]]:
    return [calculate(int(b), extra_percent) for b in bases]


def capabilities() -> dict[str, Any]:
    data = load_models()
    return {
        "formula": data["formula"],
        "base_semantics": data["base_semantics"],
        "coverage": data["coverage"],
        "runtime_hooks": data["runtime_hooks"],
        "mods": data["mods"],
        "level_cap": data["level_cap"],
        "stacking": data["stacking"],
        "recursion_guards": data["recursion_guards"],
        "verification_steps": data.get("verification_steps", []),
        "disclaimer": data["disclaimer"],
        "note": (
            "这是能力/证据说明，不是可执行成品；运行时 hook 需先用只读诊断 Mod 反射/打印确认 "
            "ExpEarn 参数与最终 XP 语义。2 参/3 参调用不能直接说明 SDK 版本不同，第三参可能可选/默认。"
        ),
    }


def preview(base_xp: int = 1000, extra_percent: float = 50) -> dict[str, Any]:
    res = calculate(base_xp, extra_percent)
    data = load_models()
    text = "\n".join(
        [
            PREVIEW_BANNER.rstrip(),
            f"示例：base_xp={res['base_xp']}, extra_percent={res['extra_percent']:g}%",
            f"bonus = floor({res['base_xp']} * {res['extra_percent']:g} / 100) = {res['bonus']}",
            f"total = {res['base_xp']} + {res['bonus']} = {res['total']}",
            "",
            "运行时前提（未验证）：必须拿到“最终结算的 base_xp”；TotalExpPoints 不是最终 base。",
            "推荐只在 ExpEarn/最终结算层加一次 bonus；若同时 hook AwardCombatExperience 会重复，需去重。",
            "在 hook 内再调 ExpEarn 会重入 hook：需 reentrancy 标志/DoInjectedCallNext/调用原函数，防递归。",
            "2/3 参调用不能直接说明 SDK 版本不同；第三参可能可选/默认，需反射或诊断 Mod 确认。",
            "满级默认跳过；负 XP/负百分比直接拒绝；OP 不靠 XP。",
            "本预览不写任何文件，也不会在运行中游戏发 XP。",
        ]
    )
    return {
        "calculation": res,
        "text": text,
        "banner": "PREVIEW ONLY",
        "note": data["preview"]["note"],
        "base_required": data["preview"]["base_required"],
    }
