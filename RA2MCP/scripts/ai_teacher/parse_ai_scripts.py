"""Parse RA2/YR AI definition scripts into structured JSON + readable reference.

Sources (read-only):
  E:/hj/Utilities/aimd.ini                            - 无敌之师 mod AI library (God's Toy)
  E:/hj/Yuris Revenge/Game/INI/Game Options/AI/Extreme AI.ini - strong AI preset (standard YR)

Outputs:
  reports/ai-teacher/scripts-parse/ai_scripts.json
  reports/ai-teacher/scripts-parse/ai-script-reference.md
  reports/ai-teacher/scripts-parse/learnability.md
"""
import json
import pathlib
import re
from collections import Counter, defaultdict

ROOT = pathlib.Path(r"E:\RA2MCP")
OUT = ROOT / "reports/ai-teacher/scripts-parse"
AIMD = pathlib.Path(r"E:\hj\Utilities\aimd.ini")
EXTREME = pathlib.Path(r"E:\hj\Yuris Revenge\Game\INI\Game Options\AI\Extreme AI.ini")

MISSION_NAMES = {
    0: "Attack Target Type", 1: "Attack Waypoint", 2: "Go Berzerk", 3: "Move To Waypoint",
    4: "Move Into Celltag", 5: "Guard Area", 6: "Jump To Script Action", 7: "Force Player Win",
    8: "Unload", 9: "Deploy", 10: "Follow Friendlies", 11: "Assign New Mission",
    12: "Set Global", 13: "Play Idle Anim", 14: "Load Onto Transport", 15: "Spy On Structure",
    16: "Patrol To Waypoint", 17: "Change Script", 18: "Change Team", 19: "Panic",
    20: "Change House", 21: "Scatter", 22: "Afraid & Run To Shroud", 23: "Force Player Loss",
    24: "Play EVA Speech", 25: "Play Sound", 26: "Play Movie", 27: "Play Theme",
    28: "Reduce Ore", 29: "Begin Production", 30: "Force Sale", 31: "Suicide",
    32: "Start Weather Storm", 33: "End Weather Storm", 34: "Center Map On Team",
    35: "Shroud Map", 36: "Reveal Map", 37: "Delete Team Members", 38: "Clear Global",
    39: "Set Local", 40: "Clear Local", 41: "Unpanic", 42: "Change Facing",
    43: "Wait Until Fully Loaded", 44: "Unload TRUCKB", 45: "Load TRUCKA",
    46: "Attack Enemy Structure", 47: "Move To Enemy Structure", 48: "Scout",
    49: "Repeat Until Success", 50: "Flash", 51: "Play Animation", 52: "Display Talk Bubble",
    53: "Gather", 54: "Regroup", 55: "Activate Iron Curtain", 56: "ChronoSphere TaskForce",
    57: "ChronoWarp TaskForce", 58: "Move To Friendly Structure", 59: "Attack Structure At Waypoint",
    60: "Enter Grinder", 61: "Occupy Tank Bunker", 62: "Enter Bio Reactor",
    63: "Occupy Battle Bunker", 64: "Garrison Structure",
}

# High-level macro intent inferred from mission id (for teaching/BC mapping).
MISSION_INTENT = {
    0: "ATTACK", 1: "ATTACK", 3: "MOVE", 4: "MOVE", 5: "GUARD", 9: "DEPLOY",
    11: "SET_MISSION", 16: "MOVE", 46: "ATTACK", 47: "MOVE", 48: "SCOUT",
    53: "GATHER", 54: "REGROUP", 58: "MOVE", 59: "ATTACK", 60: "REGROUP",
    61: "REGROUP", 62: "REGROUP", 63: "REGROUP", 64: "GARRISON",
}


def read_ini(path):
    """Parse INI preserving every section in order (duplicate sections merged)."""
    sections = {}
    order = []
    current = None
    for raw in path.read_text(encoding="gbk", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        m = re.match(r"^\[(.+?)\]\s*$", line)
        if m:
            current = m.group(1)
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if current is None or "=" not in line:
            continue
        k, _, v = line.partition("=")
        sections[current].append((k.strip(), v.strip()))
    return sections, order


def lookup(sections, name):
    """[Index]=Id tables -> ordered list of ids.

    Supports both numeric keys (aimd.ini: 0=05C60C4C-G) and named keys
    (Extreme AI.ini: RAZERTASK0=RAZERTF0).
    """
    entries = []
    for k, v in sections.get(name, []):
        m = re.search(r"(\d+)$", k)
        if m:
            entries.append((int(m.group(1)), v))
    return [v for _, v in sorted(entries)]


def numbered(sections, section_id, limit=None):
    entries = {}
    for k, v in sections.get(section_id, []):
        if k.isdigit():
            entries[int(k)] = v
    items = [entries[i] for i in sorted(entries)]
    return items[:limit] if limit else items


def unit_composition(sections, tf_id):
    units = []
    for entry in numbered(sections, tf_id):
        parts = [p.strip() for p in entry.split(",")]
        if len(parts) >= 2 and parts[0].isdigit():
            units.append({"count": int(parts[0]), "type": parts[1]})
    return units


def script_actions(sections, script_id):
    actions = []
    for idx, entry in enumerate(numbered(sections, script_id)):
        parts = [p.strip() for p in entry.split(",")]
        try:
            mission = int(parts[0])
        except (ValueError, IndexError):
            mission = None
        param = parts[1] if len(parts) > 1 else ""
        actions.append({
            "step": idx, "mission": mission,
            "mission_name": MISSION_NAMES.get(mission, f"UNKNOWN({mission})"),
            "intent": MISSION_INTENT.get(mission, "OTHER"),
            "param": param,
        })
    return actions


def parse_trigger(key, value):
    parts = [p.strip() for p in value.split(",")]
    def num(i, default=0):
        try:
            return int(float(parts[i]))
        except (ValueError, IndexError):
            return default
    return {
        "id": key,
        "name": parts[0] if parts else "",
        "team_id": parts[1] if len(parts) > 1 else "",
        "owner": parts[2] if len(parts) > 2 else "",
        "tech_level": num(3),
        "condition_type": num(4),
        "condition_object": parts[5] if len(parts) > 5 else "",
        "weights": [parts[7], parts[8], parts[9]] if len(parts) > 9 else [],
        "raw": value,
    }


def parse_library(sections, label):
    tf_ids = lookup(sections, "TaskForces")
    sc_ids = lookup(sections, "ScriptTypes")
    team_ids = lookup(sections, "TeamTypes")

    taskforces = {}
    for i, tid in enumerate(tf_ids):
        d = dict(sections.get(tid, []))
        taskforces[tid] = {
            "index": i, "id": tid, "name": d.get("Name", ""),
            "group": d.get("Group", ""),
            "units": unit_composition(sections, tid),
        }
    scripts = {}
    for i, sid in enumerate(sc_ids):
        d = dict(sections.get(sid, []))
        scripts[sid] = {
            "index": i, "id": sid, "name": d.get("Name", ""),
            "actions": script_actions(sections, sid),
        }
    teams = {}
    for i, team_id in enumerate(team_ids):
        d = dict(sections.get(team_id, []))
        tf = d.get("TaskForce", "")
        sc = d.get("Script", "")
        tf_def = taskforces.get(tf, {})
        sc_def = scripts.get(sc, {})
        house = d.get("House", "<none>")
        teams[team_id] = {
            "index": i, "id": team_id, "name": d.get("Name", ""),
            "house": house,
            "taskforce_id": tf, "taskforce_name": tf_def.get("name", ""),
            "units": tf_def.get("units", []),
            "script_id": sc, "script_name": sc_def.get("name", ""),
            "script_actions": sc_def.get("actions", []),
            "max": d.get("Max", ""), "priority": d.get("Priority", ""),
            "tech_level": d.get("TechLevel", ""),
            "veteran_level": d.get("VeteranLevel", ""),
            "autocreate": d.get("Autocreate", ""),
        }
    triggers = [parse_trigger(k, v) for k, v in sections.get("AITriggerTypes", [])]
    return {
        "label": label,
        "counts": {"taskforces": len(taskforces), "scripts": len(scripts),
                   "teams": len(teams), "triggers": len(triggers)},
        "taskforces": taskforces, "scripts": scripts, "teams": teams, "triggers": triggers,
    }


def faction_of(team):
    text = (team["name"] + " " + team["taskforce_name"]).lower()
    if "allied" in text or "gi" in text or "prism" in text or "mirage" in text:
        return "Allied"
    if "yuri" in text or "initiate" in text or "brute" in text:
        return "Yuri"
    if "soviet" in text or "rhino" in text or "tesla" in text or "apoc" in text:
        return "Soviet"
    # fall back on unit names
    for u in team["units"]:
        t = u["type"].upper()
        if t.startswith("G"):
            return "Allied"
        if t.startswith("Y"):
            return "Yuri"
        if t.startswith("N"):
            return "Soviet"
    return "Unknown"


def ai_build_hints(sections):
    """Extreme AI.ini [AI] section = AI build preferences (what to build, in order)."""
    hints = {}
    for k, v in sections.get("AI", []):
        hints[k] = v
    return hints


def rules_ai_keys():
    path = pathlib.Path(r"E:\hj\wdzs_TEST\RulesMD.ini")
    if not path.exists():
        return {}
    sections, _ = read_ini(path)
    keys = {}
    interesting = re.compile(r"^AI|MultiplayerAICM|AIParallel|BuildLimitation", re.I)
    for k, v in sections.get("General", []):
        if interesting.search(k):
            keys[k] = v.split(";")[0].strip()
    return keys


def md_intent_stats(lib):
    intents = Counter()
    for sc in lib["scripts"].values():
        for a in sc["actions"]:
            intents[a["intent"]] += 1
    return intents


def build_markdown(libs, hints, rules_keys):
    lines = ["# RA2/YR AI 脚本解析参考", "",
             "由 `scripts/ai_teacher/parse_ai_scripts.py` 自动生成。", ""]
    for label, lib in libs.items():
        c = lib["counts"]
        lines += [f"## {label}", "",
                  f"- TaskForces: **{c['taskforces']}** / ScriptTypes: **{c['scripts']}** / "
                  f"TeamTypes: **{c['teams']}** / AITriggers: **{c['triggers']}**", ""]
        # faction summary
        by_fac = defaultdict(list)
        for t in lib["teams"].values():
            by_fac[faction_of(t)].append(t)
        for fac, teams in sorted(by_fac.items()):
            if fac == "Unknown":
                continue
            lines.append(f"### {fac} 队伍样例（{len(teams)} 支）")
            for t in teams[:6]:
                comp = ", ".join(f"{u['count']}x{u['type']}" for u in t["units"][:6])
                acts = " → ".join(a["mission_name"] for a in t["script_actions"][:5])
                lines.append(f"- **{t['name'] or t['id']}**: {comp} | 脚本: {acts}"
                             f" | Max={t['max']} 优先级={t['priority']}")
            lines.append("")
        intents = md_intent_stats(lib)
        lines.append(f"脚本动作意图分布: {dict(intents)}")
        lines.append("")
    if hints:
        lines += ["## Extreme AI 建造偏好（[AI] 段）", ""]
        for k, v in hints.items():
            lines.append(f"- `{k}` = {v}")
        lines.append("")
    if rules_keys:
        lines += ["## 上帝玩具 mod RulesMD.ini 的 AI 参数", ""]
        for k, v in rules_keys.items():
            lines.append(f"- `{k}` = {v}")
        lines.append("")
    return "\n".join(lines)


def build_learnability(libs, hints):
    lines = ["# 从 AI 脚本学策略：可行性分析", ""]
    for label, lib in libs.items():
        c = lib["counts"]
        intents = md_intent_stats(lib)
        n_teams = c["teams"]
        n_comps = len({tuple((u["count"], u["type"]) for u in t["units"]) for t in lib["teams"].values()})
        n_scripts = len({tuple(a["mission"] for a in t["script_actions"]) for t in lib["teams"].values()})
        lines += [f"## {label}", "",
                  f"- 可直接提取的“决策规则”：**{c['triggers']}** 条触发（状态→派出队伍）、"
                  f"**{n_comps}** 种不同兵种组合、**{n_scripts}** 种不同行动序列",
                  f"- 动作意图分布: {dict(intents)}", ""]
    lines += ["## 能学什么 / 不能学什么", "",
              "**能直接学（宏动作）**：",
              "1. 建造优先级：Extreme AI 的 `[AI]` 段直接给出 BuildConst/BuildPower/BuildRefinery 等顺序；",
              "2. 兵种组合：每个 TeamType 对应一个 TaskForce（数量+兵种），就是“该造什么”；",
              "3. 进攻/防守行为：ScriptTypes 给出 移动/攻击/守卫/集结 的动作序列；",
              "4. 出兵条件：AITriggerTypes 给出“资金/科技/敌方建筑 → 派哪支队伍”的规则。",
              "", "**不能直接学（需要补数据或写代码）**：",
              "1. 精确时序：脚本是“事件驱动”，没有每 0.5 秒的状态→动作标签；",
              "2. 微操：寻路、集火、撤退等由引擎硬编码，不在 INI 里；",
              "3. 上帝玩具的 40 倍经济会改变 AI 的优先级，直接照搬到标准经济会失真。", "",
              "## 建议的最小学习实验（1–2 天可验证）", "",
              "1. **模板化**：把上面解析出的 teams/scripts/triggers 转成 20–40 条“宏观决策规则”"
              "（例如：`矿厂数<2 且钱>500 → 造矿厂`；`有 2 兵营 → 训练 5 GI 小队 → 攻击敌方基地`）；",
              "2. **脚本教师**：用这些规则写 `scripted_policy.py`，在标准训练环境跑 3–5 局，"
              "同时用 RL env 记录 `(state, action)` 轨迹（这是模型真正需要的标签）；",
              "3. **BC 小测**：在 3–5 局轨迹（约 4–6k 样本）上训练一个小分类/序列模型预测动作；"
              "看验证集动作准确率是否明显高于随机（随机约 1/300，若 >20% 即证明“可学”）；",
              "4. 若可学 → 扩到 20–50 局教师数据做正式 BC；不可学 → 检查动作空间映射/状态特征。", ""]
    return "\n".join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    aimd, _ = read_ini(AIMD)
    libs = {}
    if AIMD.exists():
        libs["无敌之师 aimd.ini（上帝玩具 AI 库）"] = parse_library(aimd, "aimd")
    hints = {}
    if EXTREME.exists():
        ext, _ = read_ini(EXTREME)
        libs["Extreme AI.ini（标准 YR 强 AI 预设）"] = parse_library(ext, "extreme")
        hints = ai_build_hints(ext)
    rules_keys = rules_ai_keys()

    payload = {
        "libraries": libs,
        "extreme_ai_build_hints": hints,
        "godstoy_rules_ai_keys": rules_keys,
        "mission_names": MISSION_NAMES,
    }
    (OUT / "ai_scripts.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "ai-script-reference.md").write_text(
        build_markdown(libs, hints, rules_keys), encoding="utf-8")
    (OUT / "learnability.md").write_text(
        build_learnability(libs, hints), encoding="utf-8")

    for label, lib in libs.items():
        print(label, lib["counts"])
    print("extreme AI build hints:", len(hints))
    print("godstoy rules ai keys:", len(rules_keys))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
