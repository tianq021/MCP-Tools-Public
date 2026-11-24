"""技能效果扩展能力表与补丁/模组预览。

数据文件：`data/skill_effect_capabilities.json`

定位：**证据表 + 预览生成器**，不是“已经生效”的魔改功能。
所有内容按证据分级（L1 调参 / L2 追加属性 / L3 移植 / L4 新事件逻辑），
预览输出明确标注 PREVIEW ONLY，未在游戏里验证。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import DATA_DIR

CAPABILITIES_FILE = DATA_DIR / "skill_effect_capabilities.json"

GRADE_ORDER = ["L1", "L2", "L3", "L4"]

PREVIEW_BANNER = (
    "===== PREVIEW ONLY / 未实机验证 =====\n"
    "下面是“可以做/建议怎么做”的补丁或模组预览，不代表已经生效；\n"
    "本轮没有启动游戏、没有安装 SDK 模组、没有写真实存档。\n"
)

_cache: Optional[dict[str, Any]] = None


def load_capabilities(refresh: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is None or refresh:
        if not CAPABILITIES_FILE.is_file():
            raise FileNotFoundError(f"缺少能力表数据: {CAPABILITIES_FILE}")
        _cache = json.loads(CAPABILITIES_FILE.read_text(encoding="utf-8"))
    return _cache


def grades() -> dict[str, Any]:
    return load_capabilities()["grades"]


def evidence() -> dict[str, Any]:
    return load_capabilities()["evidence"]


def effects() -> list[dict[str, Any]]:
    return load_capabilities()["effects"]


def summary() -> dict[str, Any]:
    data = load_capabilities()
    by_grade: dict[str, int] = {g: 0 for g in GRADE_ORDER}
    for e in data["effects"]:
        for g in e.get("grades") or []:
            by_grade[g] = by_grade.get(g, 0) + 1
    return {
        "version": data.get("version"),
        "updated": data.get("updated"),
        "effect_count": len(data["effects"]),
        "grades": {g: grades()[g]["name"] for g in GRADE_ORDER},
        "effect_count_by_grade": by_grade,
        "evidence_count": len(data["evidence"]),
        "disclaimer": data.get("disclaimer"),
    }


def list_effects(query: str = "", grade: str = "") -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    g = (grade or "").strip().upper()
    out = []
    for e in effects():
        if g and g not in (e.get("grades") or []):
            continue
        if q:
            hay = " ".join(
                [e.get("id", ""), e.get("name", "")]
                + list(e.get("aliases") or [])
                + list(e.get("attributes") or [])
                + list(e.get("hooks") or [])
            ).lower()
            if q not in hay:
                continue
        out.append(e)
    return out


def get_effect(effect_id: str) -> dict[str, Any]:
    key = (effect_id or "").strip().lower()
    for e in effects():
        if key in (e.get("id", "").lower(), e.get("name", "").lower()) or any(
            key == a.lower() for a in (e.get("aliases") or [])
        ):
            return e
    for e in effects():
        if key and (key in e.get("id", "").lower() or key in e.get("name", "").lower()):
            return e
    raise ValueError(f"能力表里找不到效果 {effect_id!r}（可用 skill fx list 查看）")


def _format_attribute(effect: dict[str, Any], attr: str) -> str:
    data = load_capabilities()
    app = (data.get("attribute_applicability") or {}).get(attr) or {}
    status = app.get("status", "unverified")
    label = {
        "bl2_confirmed": "BL2 确认",
        "tps_only": "TPS-only，BL2 未确认",
        "not_found": "未验证",
    }.get(status, "未验证")
    return f"- `{attr}` [{label}]"


def _hotfix_line(effect: dict[str, Any], attr: str, index: int) -> str:
    """生成一条热修 set 预览（占位技能对象 + 数组索引）。"""
    return (
        f'set <SkillDefinition 对象路径> SkillEffectDefinitions[{index}] '
        f"(AttributeToModify=AttributeDefinition'{attr}',"
        f"bIncludeDuelingTargets=False,bIncludeSelfAsTarget=False,bOnlyEffectTargetsInRange=False,"
        f"bExcludeNonPlayerCharacters=False,EffectTarget=TARGET_Self,TargetInstanceDataName=,"
        f"TargetCriteria=CRITERIA_None,ModifierType=MT_PreAdd,"
        f"BaseModifierValue=(BaseValueConstant=<数值 占位>,BaseValueAttribute=None,"
        f"InitializationDefinition=None,BaseValueScaleConstant=1.000000),"
        f"GradeToStartApplyingEffect=1,PerGradeUpgradeInterval=1,"
        f"PerGradeUpgrade=(BaseValueConstant=<数值 占位>,BaseValueAttribute=None,"
        f"InitializationDefinition=None,BaseValueScaleConstant=1.000000),BonusUpgradeList=)"
    )


def _sdk_skill_attribute_snippet(effect: dict[str, Any]) -> str:
    attrs = effect.get("attributes") or []
    if not attrs:
        return ""
    lines = [
        "# PythonSDK 伪代码（PREVIEW）：给现有技能追加属性效果",
        "from Mods.Structs import SkillEffectData, AttributeInitializationData",
        "skill = unrealsdk.FindObject('SkillDefinition', '<技能对象路径>')",
        "new_effects = []",
        "for attr_name in %r:" % (attrs,),
        "    new_effects.append(SkillEffectData(",
        "        AttributeToModify=unrealsdk.FindObject('AttributeDefinition', attr_name),",
        "        ModifierType=<EModifierType.MT_PreAdd 或 MT_Scale>,",
        "        BaseModifierValue=AttributeInitializationData(BaseValueConstant=<数值 占位>, BaseValueScaleConstant=1.0),",
        "        EffectTarget=<EEffectTarget.TARGET_Self>,",
        "        GradeToStartApplyingEffect=1,",
        "        PerGradeUpgradeInterval=1,",
        "    ))",
        "# 注意：要保留原有 SkillEffectDefinitions，不要直接覆盖丢失原效果",
        "skill.SkillEffectDefinitions = list(skill.SkillEffectDefinitions) + new_effects",
    ]
    return "\n".join(lines)


def _sdk_hook_snippet(effect: dict[str, Any]) -> str:
    hooks = effect.get("hooks") or []
    if not hooks:
        return ""
    lines = [
        "# PythonSDK 伪代码（PREVIEW）：用游戏事件实现新逻辑",
        "from unrealsdk import hooks",
    ]
    for h in hooks:
        name = h.split("（")[0].strip()
        if not name.startswith("Willow") and not name.startswith("Engine"):
            continue
        lines += [
            "@hooks.Hook('%s')" % name,
            "def _on_event(caller, function, params):",
            "    # TODO: 判断目标/伤害来源/距离，执行回血/回弹药/叠 buff 等",
            "    return True",
            "",
        ]
    return "\n".join(lines).rstrip()


def _hotfix_snippet(effect: dict[str, Any]) -> str:
    attrs = effect.get("attributes") or []
    if not attrs:
        return ""
    lines = [
        "# BLCMM / hotfix 预览：把整条 SkillEffectDefinitions 数组改掉（示例）",
        "# 警告：set 数组会把原有条目覆盖，实际使用前必须把原数组内容一起带上",
        "<code profiles=\"default\">set <SkillDefinition 对象路径> SkillEffectDefinitions ((...原有条目...),(...新条目...))</code>",
        "",
    ]
    for i, attr in enumerate(attrs[:4], 1):
        lines.append("# 追加条目示例 %d：" % i)
        lines.append(_hotfix_line(effect, attr, i))
    return "\n".join(lines)


def build_plan(effect_id: str) -> dict[str, Any]:
    """返回结构化计划：证据、依赖、限制、可实现度、预览文本。"""
    e = get_effect(effect_id)
    ev = evidence()
    ev_details = []
    for ref in e.get("evidence") or []:
        item = ev.get(ref)
        if item:
            ev_details.append({"ref": ref, **item})
    previews = []
    code_hotfix = _hotfix_snippet(e)
    if code_hotfix:
        previews.append({"kind": "hotfix", "text": code_hotfix})
    code_sdk = _sdk_skill_attribute_snippet(e)
    if code_sdk:
        previews.append({"kind": "sdk", "text": code_sdk})
    code_hook = _sdk_hook_snippet(e)
    if code_hook:
        previews.append({"kind": "hook", "text": code_hook})
    if not previews and e.get("preview"):
        # L1 调参 / L3 移植这类没有“属性列表”的效果，直接给出说明性预览
        previews.append({"kind": "note", "text": e["preview"]})
    elif e.get("preview"):
        previews.append({"kind": "note", "text": e["preview"]})
    data = load_capabilities()
    attr_status = {
        a: (data.get("attribute_applicability") or {}).get(a, {"status": "unverified"})
        for a in (e.get("attributes") or [])
    }
    return {
        "effect": e,
        "evidence": ev_details,
        "grades": [{"id": g, **grades()[g]} for g in (e.get("grades") or [])],
        "dependencies": [
            "数据层：BLCMM/hotfix 补丁（每次进游戏重新应用）或 PythonSDK 模组（运行时）",
            "需要正确的技能对象路径（如 GD_Soldier_Skills.Guerrilla.Sentry）",
            "需要属性对象存在（D_Attributes.*），或自己用 clone_attr 新建属性",
            "L3/L4 需要 PythonSDK，且需实机调试行为图/事件参数",
        ],
        "persistence": "存档只保存技能等级/点数；效果本身不写入存档，补丁/模组不加载就消失",
        "stacking_limits": e.get("stacking", ""),
        "implementability": e.get("implementability", ""),
        "status": e.get("status", ""),
        "previews": previews,
        "attribute_applicability": attr_status,
        "attribute_sources": data.get("attribute_sources", {}),
        "contradictions": e.get("contradictions") or [],
        "verification_needed": [
            "进游戏后技能面板是否显示/生效",
            "数值叠加、上下限、是否与其他来源冲突",
            "存档保存/重载后是否保持一致（效果本身不持久，等级会保留）",
            "跨职业/移植时技能树 UI 与技能管理器是否报错",
        ],
    }


def format_plan_text(effect_id: str) -> str:
    p = build_plan(effect_id)
    e = p["effect"]
    lines = [PREVIEW_BANNER]
    lines.append(f"## {e['name']}  (id={e['id']})")
    lines.append(f"分级: " + " / ".join(f"{g} {grades()[g]['name']}" for g in e.get("grades") or []))
    lines.append(f"可实现程度: {p['implementability']}")
    lines.append(f"状态: {p['status']}")
    lines.append("")
    if e.get("attributes"):
        lines.append("### 可用属性（原生 AttributeDefinition；方括号内是 BL2/TPS 适用性）")
        for a in e["attributes"]:
            lines.append(_format_attribute(e, a))
        lines.append("")
        if e.get("contradictions"):
            lines.append("### 属性适用性冲突/修正")
            for c in e["contradictions"]:
                lines.append(f"- {c}")
            lines.append("")
    if e.get("hooks"):
        lines.append("### 可用事件 Hook")
        for h in e["hooks"]:
            lines.append(f"- `{h}`")
        lines.append("")
    lines.append("### 原生对象/字段")
    lines.append(e.get("native", ""))
    lines.append("")
    lines.append("### 证据")
    for item in p["evidence"]:
        lines.append(f"- [{item['name']}]({item['url']})")
        lines.append(f"  {item.get('detail', '')}")
    lines.append("")
    lines.append("### 依赖与持久化")
    for d in p["dependencies"]:
        lines.append(f"- {d}")
    lines.append(f"- 持久化: {p['persistence']}")
    lines.append("")
    if p["stacking_limits"]:
        lines.append("### 叠加/上限（未验证部分）")
        lines.append(p["stacking_limits"])
        lines.append("")
    for pv in p["previews"]:
        lines.append(f"### 预览（{pv['kind']}）")
        lines.append("```")
        lines.append(pv["text"])
        lines.append("```")
        lines.append("")
    lines.append("### 实机验证清单（尚未做）")
    for v in p["verification_needed"]:
        lines.append(f"- [ ] {v}")
    return "\n".join(lines)
