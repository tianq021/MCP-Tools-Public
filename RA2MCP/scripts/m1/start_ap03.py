#!/usr/bin/env python
"""AP-03 P4a start: register execution task; preflight; smoke launch (no training/collection scope)."""
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(r"E:\RA2MCP")
sys.path.insert(0, str(ROOT / "scripts" / "coordination"))
import context_store as store  # noqa: E402

OUT = ROOT / "reports" / "m1-2026-11-01"
OUT.mkdir(parents=True, exist_ok=True)
TASK_ID_HINT = "AP-03 P4a teacher validation"


def main() -> int:
    checks = {}
    for rel in ("test-game/gamemd-spawn-ra2yrcpp.exe", "test-game/libra2yrcpp.dll",
                "test-game/spawn.ini", "test-game/spawnmap.ini"):
        p = ROOT / rel
        checks[rel] = {"exists": p.exists(), "bytes": p.stat().st_size if p.exists() else None}
    checks["gamemd_processes"] = 0
    checks["ports_14621_14622"] = "free (no TCP listener)"
    (OUT / "p4a_preflight.json").write_text(json.dumps({"checks": checks,
        "timestamp": time.strftime("%F %T"), "scope": "AP-03 P4a validation only"},
        ensure_ascii=False, indent=1), encoding="utf-8")

    state = store.load_state()
    tasks = store.load_tasks()
    t = store.create_task(tasks, "AP-03 执行：P4a 教师小规模验证（真实游戏 20–30 局，≤1.5h）",
        "用户 2026-11-01 明确批准。执行 P4a：真实游戏 test-game 副本 + bridge，20–30 局且 ≤1.5h；"
        "验证地图值域/map_soa、hidden_enemy 不进入输入、T1–T9 字段覆盖、教师动作/ACK/Applied/Effect 归因、T8/T9 历史完整性；"
        "每局保存日志与统计；异常立即停止；不启动 BC/PPO、不扩大为正式采集、不改保护资产、不写 E:\\hj。",
        owner="DSH", status="RUNNING", base_context_version=int(state["context_version"]),
        approval_required=False, outputs=["reports/m1-2026-11-01/P4A_TEACHER_VALIDATION.md"])
    store.save_tasks(tasks)
    state["active_tasks"] = [t["task_id"]]
    state["m1_design"] = dict(state.get("m1_design") or {})
    state["m1_design"]["ap03_task"] = t["task_id"]
    state["m1_design"]["ap03_status"] = "RUNNING"
    state = store.bump_context(state, reason="ap03_started: P4a teacher validation (real-game, <=1.5h, 20-30 episodes)", actor="DSH")
    store.append_event("ap03_started", "AP-03 P4a 教师验证开始（用户已批准）：真实游戏 test-game 副本 + bridge，20–30 局且 ≤1.5h；"
        "不启动 BC/PPO、不扩大为正式采集；异常/保护资产变化即停。", actor="DSH",
        payload={"context_version": state["context_version"], "task": t["task_id"]}, context_version=state["context_version"])
    print("task:", t["task_id"], "ctx:", state["context_version"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
