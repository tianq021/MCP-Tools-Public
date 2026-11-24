"""离线数值平衡模型：等级膨胀、UVHM 倍率、OP、Badass 独立因子与预览。

定位：只做离线曲线/对比/预览。不读存档、不写游戏目录、不应用运行中游戏。
数据：`data/balance_models.json`（来源与推断均写明）。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import DATA_DIR

MODELS_FILE = DATA_DIR / "balance_models.json"
PREVIEW_BANNER = (
    "===== PREVIEW ONLY / 未实机验证 =====\n"
    "这是离线数值模型与补丁/模组预览，不是最终 DPS 公式，也没有在运行中游戏应用。\n"
    "改 UniversalBalanceScaler 会全局影响玩家/敌人/物品等多处数值，不等于“只削弱敌人”。\n"
)

_cache: Optional[dict[str, Any]] = None


def load_models(refresh: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is None or refresh:
        if not MODELS_FILE.is_file():
            raise FileNotFoundError(f"缺少数值模型: {MODELS_FILE}")
        _cache = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    return _cache


def sources() -> dict[str, Any]:
    return load_models()["sources"]


def disclaimer() -> str:
    return load_models()["disclaimer"]


def _scenario_map() -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in load_models()["scenarios"]}


def curve(levels: list[int], const: float = 1.13, base_level: Optional[int] = None) -> list[dict[str, Any]]:
    """UniversalBalanceScaler 相对曲线：const^(level-base)，base 默认取配置里的 50。"""
    data = load_models()
    base = int(base_level if base_level is not None else data["levels"]["base"])
    return [
        {
            "level": int(lv),
            "base_level": base,
            "const": const,
            "relative": const ** (int(lv) - base),
        }
        for lv in levels
    ]


def compare(
    levels: Optional[list[int]] = None,
    include_op: bool = True,
    const_override: Optional[float] = None,
) -> dict[str, Any]:
    """比较各场景的敌人生命/护盾/伤害指数（以参考场景 level=55 为 1.0）。

    指数是模型值，不是实际血量/伤害；OP 行只是把 OP 当额外有效等级。
    """
    data = load_models()
    base = int(data["levels"]["base"])
    levels = [int(x) for x in (levels or data["levels"]["compare"])]
    scenarios = _scenario_map()
    ref = scenarios["reference_ucp"]
    ref_const = float(const_override if const_override is not None else ref["const"])
    ref_health0 = ref["enemy_health_mult"] * (ref_const ** (levels[0] - base))

    def row(level: int, op: int = 0) -> dict[str, Any]:
        eff = level + op
        result: dict[str, Any] = {"level": level, "op": op, "effective_level": eff, "scenarios": {}}
        for sid, sc in scenarios.items():
            c = float(const_override if (const_override is not None and sid == "reference_ucp") else sc["const"])
            flat = c ** (eff - base)
            health = float(sc["enemy_health_mult"]) * flat / ref_health0
            shield = float(sc["enemy_shield_mult"]) * flat / ref_health0
            dmg = float(sc["enemy_damage_mult"]) * flat / ref_health0
            result["scenarios"][sid] = {
                "enemy_health_index": health,
                "enemy_shield_index": shield,
                "enemy_damage_index": dmg,
                "enemy_health_regen": sc.get("enemy_health_regen"),
                "slag_amp": sc.get("slag_amp"),
            }
        return result

    rows = [row(lv, 0) for lv in levels]
    if include_op:
        op_max = max(data["levels"]["op"]) if data["levels"].get("op") else 0
        for op in (x for x in data["levels"]["op"] if x > 0):
            rows.append(row(80, op))
    return {
        "base_level": base,
        "normalized_at": {"scenario": "reference_ucp", "level": levels[0]},
        "rows": rows,
        "note": "enemy_*_index 是相对指数，不是实际生命/护盾/伤害；OP 按额外有效等级近似。",
        "badass_note": "Badass/账号加成是独立因子，未乘进本表；见 badass_report()。",
    }


def op_table(const: float = 1.13) -> list[dict[str, Any]]:
    data = load_models()
    base = int(data["levels"]["base"])
    out = []
    for op in data["levels"]["op"]:
        eff = 80 + op
        out.append(
            {
                "level": 80,
                "op": op,
                "effective_level": eff,
                "scaler_relative_vs_80": const ** op,
                "scaler_relative_vs_50": const ** (eff - base),
                "note": "仅按额外有效等级近似；不含未知 OP 额外血量/伤害加成，也不代表装备同步提升后的 TTK。",
            }
        )
    return out


def equipment_delta(deltas: Optional[list[int]] = None, const: float = 1.13) -> list[dict[str, Any]]:
    """装备等级差相对伤害因子 (const^d)，仅模型。"""
    deltas = deltas if deltas is not None else [-10, -5, 0, 5, 10]
    return [
        {
            "delta": int(d),
            "relative": const ** int(d),
            "note": "d=装备等级-敌人等级；未计入部件/技能/元素/暴击/抗性。",
        }
        for d in deltas
    ]


def badass_report() -> dict[str, Any]:
    data = load_models()["badass"]
    return {
        "values": data["values"],
        "factors": data["factors"],
        "simplified_two_factor_product": data["simplified_two_factor_product"],
        "note": data["note"],
        "simplified_note": data["simplified_note"],
        "sample": "1.282*1.257 仅为“无换弹、枪伤×射速独立乘算”的简化示意，不是实际 DPS。",
    }


def preview(const: float = 1.08) -> dict[str, Any]:
    """生成只读预览文本：UniversalBalanceScaler + PT3 参考字段（不应用）。"""
    data = load_models()
    ref = _scenario_map()["reference_ucp"]
    scaler_obj = data["universal_scaler"]["object"]
    lines = [
        PREVIEW_BANNER.rstrip(),
        "# 1) 全局缩放常量（参考 ScalingAdjuster 的写法）",
        f"set {scaler_obj} ConstantValue {const:.6f}",
        "",
        "# 2) 参考 PT3 倍率（UCP 口径；set 数组会覆盖整组，实际使用必须保留原数组所有字段）",
        "set GD_Playthrough3Tuning.Balance.BalanceMod_PT3 BalanceModifiers ((",
        f"  MinEffectiveLevel={int(ref['MinEffectiveLevel'] if 'MinEffectiveLevel' in ref else data['uvhm']['min_effective_level'])},",
        f"  EnemyHealthMultiplier={ref['enemy_health_mult']:.6f},",
        f"  EnemyShieldStrengthMultiplier={ref['enemy_shield_mult']:.6f},",
        f"  EnemyDamageMultiplier={ref['enemy_damage_mult']:.6f},",
        "  ... 其余字段必须保留原值 ...))",
        "",
        "# 3) 可选：玩家/OP 生命公式（来自 OverpowerLevelBonuses 的对象结构；未验证，默认不推荐直接改）",
        "# set GD_Balance_HealthAndDamage.HealthAndDamage.Init_PlayerHealth ValueFormula (...)",
        "",
        "# 警告：改 UniversalBalanceScaler 会同时影响玩家/敌人/物品；不要把它当成“只削敌人”。",
        "# 本预览不会写任何文件，也不会应用到运行中的游戏。",
    ]
    return {
        "const": const,
        "text": "\n".join(lines),
        "objects": {
            "universal_scaler": scaler_obj,
            "pt3_balance_mod": "GD_Playthrough3Tuning.Balance.BalanceMod_PT3",
            "player_health": data["uvhm"]["player_health_formula"],
        },
        "note": "PREVIEW ONLY：未实机验证，未写存档/配置/游戏目录，也没有应用到运行中游戏。",
    }


def format_compare(data: Optional[dict[str, Any]] = None) -> str:
    data = data or compare()
    ids = list(_scenario_map())
    header = ["level/OP", "eff"] + [sid for sid in ids]
    lines = ["场景索引（参考 reference_ucp 的 level=55 为 1.0；不是实际血量）"]
    lines.append(" | ".join(header))
    for row in data["rows"]:
        cells = [f"{row['level']}+OP{row['op']}", str(row["effective_level"])]
        for sid in ids:
            s = row["scenarios"][sid]
            cells.append(
                f"H{s['enemy_health_index']:.2f}/S{s['enemy_shield_index']:.2f}/D{s['enemy_damage_index']:.2f}"
            )
        lines.append(" | ".join(cells))
    lines.append("")
    lines.append(data["note"])
    lines.append(data["badass_note"])
    return "\n".join(lines)
