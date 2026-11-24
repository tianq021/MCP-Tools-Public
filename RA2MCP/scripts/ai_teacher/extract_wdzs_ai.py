"""Collect the 无敌之师 (wdzs) mod's built-in AI definition data into JSON.

Sources (read-only):
  E:/hj/Utilities/aimd.ini          - AI task forces / scripts / team types / triggers
  E:/hj/wdzs_TEST/mpgodstoymd.ini   - "God's Toy" multiplayer mode overrides
  E:/hj/wdzs_TEST/RulesMD.ini       - active rules ([General] AI/economy keys, [AI])

Outputs:
  reports/ai-teacher/wdzs-godstoy/ai_data.json
  reports/ai-teacher/wdzs-godstoy/summary.md
"""
import json
import pathlib
import re
import sys

UTIL = pathlib.Path(r"E:\hj\Utilities")
MOD = pathlib.Path(r"E:\hj\wdzs_TEST")
OUT = pathlib.Path(r"E:\RA2MCP\reports\ai-teacher\wdzs-godstoy")

ID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[A-Za-z0-9]$")


def read_ini(path):
    """Return {section: {key: value}} preserving order, plus list sections."""
    sections = {}
    current = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        m = re.match(r"^\[(.+?)\]\s*$", line)
        if m:
            current = m.group(1)
            sections[current] = []
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        sections[current].append((key.strip(), value.strip()))
    return sections


def build_lookup(section_entries):
    """Turn {'0': id, '1': id, ...} into ordered list of ids."""
    lookup = []
    for key, value in section_entries:
        if key.isdigit():
            lookup.append((int(key), value))
    return [v for _, v in sorted(lookup)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    aimd = read_ini(UTIL / "aimd.ini")

    task_ids = build_lookup(aimd.get("TaskForces", []))
    script_ids = build_lookup(aimd.get("ScriptTypes", []))
    team_ids = build_lookup(aimd.get("TeamTypes", []))

    def definition(section_id):
        if section_id not in aimd:
            return {}
        return {k: v for k, v in aimd[section_id]}

    def numbered(section_id):
        """Ordered numeric entries like 0=E1,5 or 0=0,1,2."""
        entries = {}
        for key, value in aimd.get(section_id, []):
            if key.isdigit():
                entries[int(key)] = value
        return [entries[i] for i in sorted(entries)]

    taskforces, scripts, teams = {}, {}, {}
    for i, tf_id in enumerate(task_ids):
        d = definition(tf_id)
        taskforces[i] = {
            "id": tf_id,
            "name": d.get("Name", ""),
            "group": d.get("Group", ""),
            "units": numbered(tf_id),
        }
    for i, sc_id in enumerate(script_ids):
        d = definition(sc_id)
        scripts[i] = {
            "id": sc_id,
            "name": d.get("Name", ""),
            "actions": numbered(sc_id),
        }
    for i, team_id in enumerate(team_ids):
        d = definition(team_id)
        teams[i] = {
            "id": team_id,
            "name": d.get("Name", ""),
            "taskforce_index": d.get("TaskForce", ""),
            "script_index": d.get("Script", ""),
            "house": d.get("House", ""),
            "max": d.get("Max", ""),
            "tech_level": d.get("TechLevel", ""),
            "veteran_level": d.get("VeteranLevel", ""),
            "priority": d.get("Priority", ""),
            "full": d,
        }

    # Base AI triggers from aimd.ini, and the mode-specific overrides.
    # Trigger ids look like "06000100-G" rather than starting with AITRIGGER.
    triggers = {}
    for key, value in aimd.get("AITriggerTypes", []):
        if key.upper().startswith("AITRIGGER") or ID_RE.match(key):
            triggers[key] = value
    mode = read_ini(MOD / "mpgodstoymd.ini")
    mode_triggers = dict(mode.get("AITriggerTypes", []))
    rules = read_ini(MOD / "RulesMD.ini")

    # Trigger line format:
    #   id=name,team_id,owner,tech_level,condition,... 
    # Group triggers by the team they send.
    team_by_id = {t["id"]: t for t in teams.values()}
    trigger_teams = {}
    for key, value in triggers.items():
        fields = [f.strip() for f in value.split(",")]
        team_id = fields[1] if len(fields) > 1 else ""
        team = team_by_id.get(team_id)
        name = team["name"] if team else team_id
        trigger_teams.setdefault(name, 0)
        trigger_teams[name] += 1

    def rules_general(patterns):
        out = {}
        for key, value in rules.get("General", []):
            if any(re.search(p, key, re.I) for p in patterns):
                out[key] = value
        return out

    mode_data = {
        "mode_name": "God's Toy",
        "file": str(MOD / "mpgodstoymd.ini"),
        "general": dict(mode.get("General", [])),
        "sections": {name: dict(entries) for name, entries in mode.items()},
        "trigger_overrides": mode_triggers,
    }
    rules_ai = {
        "general": rules_general([r"AI", r"Multiplayer", r"BuildLimitation"]),
        "ai_section": dict(rules.get("AI", [])),
    }

    data = {
        "source": {
            "aimd_ini": str(UTIL / "aimd.ini"),
            "mode_ini": str(MOD / "mpgodstoymd.ini"),
            "rules": str(MOD / "RulesMD.ini"),
        },
        "counts": {
            "taskforces": len(taskforces),
            "scripts": len(scripts),
            "teams": len(teams),
            "base_ai_triggers": len(triggers),
            "mode_ai_triggers": len(mode_triggers),
        },
        "godstoy": mode_data,
        "rules_ai": rules_ai,
        "base_ai_triggers": triggers,
        "taskforces": taskforces,
        "scripts": scripts,
        "teams": teams,
    }
    (OUT / "ai_data.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    # Human-readable summary.
    lines = []
    lines.append("# 无敌之师（wdzs）内置 AI 数据收集\n")
    lines.append(f"- AI 定义：`{UTIL / 'aimd.ini'}`")
    lines.append(f"- 上帝玩具模式：`{MOD / 'mpgodstoymd.ini'}`")
    lines.append(f"- 规则：`{MOD / 'RulesMD.ini'}`\n")
    lines.append("## 数量统计\n")
    for k, v in data["counts"].items():
        lines.append(f"- {k}: **{v}**")
    lines.append("\n## 上帝玩具模式（God's Toy）修改\n")
    for k, v in mode_data["general"].items():
        lines.append(f"- `{k}` = `{v}`")
    lines.append("\n模式自带 AITrigger 覆盖：")
    for k, v in mode_triggers.items():
        lines.append(f"- `{k}` = `{v[:120]}`")
    lines.append("\n上帝玩具模式还修改了 AI 的建造规划数量（AIBasePlanningCount）：")
    for name, entries in mode_data["sections"].items():
        if "AIBasePlanningCount" in entries:
            lines.append(f"- {name}: {entries['AIBasePlanningCount']}")
    lines.append("\n## 基础 AI 经济/限制参数（RulesMD.ini [General]）\n")
    for k, v in rules_ai["general"].items():
        lines.append(f"- `{k}` = `{v}`")

    # Sample teams with their taskforce composition and script.
    lines.append("\n## 队伍样例（TeamTypes → TaskForce / Script）\n")
    for idx in list(teams)[:12]:
        t = teams[idx]
        tf = taskforces.get(int(t["taskforce_index"])) if t["taskforce_index"].isdigit() else None
        sc = scripts.get(int(t["script_index"])) if t["script_index"].isdigit() else None
        lines.append(f"- **{t['name'] or t['id']}**  max={t['max']} house={t['house']}")
        if tf:
            lines.append(f"  - 单位：{', '.join(tf['units'][:12])}")
        if sc:
            lines.append(f"  - 脚本动作（前 12 步）：{', '.join(sc['actions'][:12])}")

    # Unit composition histogram across taskforces.
    from collections import Counter
    unit_counter = Counter()
    for tf in taskforces.values():
        for entry in tf["units"]:
            unit_counter[entry.split(",")[0]] += 1
    lines.append("\n## 任务部队出现的单位类型频次（前 30）\n")
    for unit, cnt in unit_counter.most_common(30):
        lines.append(f"- {unit}: {cnt}")

    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT / "ai_data.json")
    print("wrote", OUT / "summary.md")
    print("counts:", data["counts"])
    print("mode:", mode_data["general"])


if __name__ == "__main__":
    main()
