#!/usr/bin/env python
"""One-off state alignment: Route A = PRIMARY, M6 = FROZEN/AUXILIARY (2026-11-01).

Maintenance tooling (same pattern as v3_wrapup_state.py): aligns
PROJECT_STATE.json / TASK_QUEUE.json / CURRENT_CONTEXT.md with the user's
strategic decision. Append-only: bumps context_version, writes snapshot,
keeps history. Does NOT touch models, rewards, or training data.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(r"E:\RA2MCP")
sys.path.insert(0, str(ROOT / "scripts" / "coordination"))
import context_store as store  # noqa: E402

EXPECTED_CONTEXT_VERSION = 235
STAMP = time.strftime("%Y%m%d-%H%M%S")
BACKUP_DIR = ROOT / "reports" / "coordination" / "backups" / f"route-a-align-{STAMP}"
BACKUP_FILES = ["PROJECT_STATE.json", "TASK_QUEUE.json", "EVENTS.jsonl",
                "ARTIFACT_INDEX.json", "CURRENT_CONTEXT.md"]


def main() -> int:
    state = store.load_state()
    version = int(state.get("context_version") or 0)
    if version != EXPECTED_CONTEXT_VERSION:
        raise SystemExit(f"expected context_version={EXPECTED_CONTEXT_VERSION}, got {version}; abort")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for name in BACKUP_FILES:
        src = ROOT / "coordination" / name
        if src.exists():
            shutil.copy2(src, BACKUP_DIR / name)

    tasks = store.load_tasks()
    task015 = store.find_task(tasks, "TASK-20261031-015")
    if task015:
        task015["status"] = "BLOCKED"
        task015["updated_at"] = store.now_iso()
        task015["result_summary"] = (
            "【2026-11-01 状态对齐】静态审计/监控/安全门已就绪（docs 00-03），但真实对照实验因系统提交内存不足"
            "（explorer 15.5GB + 项目外进程）长期 BLOCKED；项目已转入路线 A M1 准备，该任务保留为 BLOCKED 待后续"
            "资源条件满足或用户重新批准，不视为已完成。"
        )
    store.save_tasks(tasks)

    old_next = list(state.get("next_actions") or [])
    state["project_status"] = "RUNNING"
    state["status_detail"] = "ROUTE_A_PRIMARY__M6_FROZEN_AUXILIARY__M1_PREP"
    state["current_stage"] = "M1"
    state["mainline"] = {
        "primary": "route_a_original_yr",
        "declared_at": store.now_iso(),
        "decision": "用户 2026-11-01 战略决定：路线 A（原版尤里的复仇）恢复为唯一主线",
        "roadmap": ["M0", "M1", "M2", "M3", "M4", "M5"],
        "current_position": "M0 最终收尾（PARTIAL，已验收）+ M1 方案设计（待批准）",
    }
    state["auxiliary"] = {
        "m6_vera20k": {
            "status": "FROZEN_AUXILIARY",
            "frozen_at": "2026-11-01T10:30+0800",
            "freeze_manifest": "reports/m6-freeze-2026-11-01/M6_FREEZE_MANIFEST.json",
            "freeze_report": "reports/m6-freeze-2026-11-01/M6_FREEZE_REPORT.md",
            "git_tag": "m6-s2-frozen @ b0754c925192c02df01be6a2697e877a88b6cc0e",
            "integrity": "499 files / 299,525,696 B / verify_m6_freeze.py 0 mismatch",
            "prohibitions": [
                "exp003 / exp004 及新 PPO 长时间训练",
                "大规模奖励调整 / 动作观测契约变更",
                "produce/place 回放确定性修复与全栈功能扩展",
                "删除/覆盖/移动任何冻结文件",
            ],
            "reactivation": "需用户另行批准 + 提交启用方案（目的/范围/预算/验收/回退）",
        }
    }
    state["next_actions"] = [
        {"id": "M1-APPROVAL-001", "title": "审批 M1 技术设计报告与下一阶段实施计划",
         "status": "PROPOSED", "owner": "USER", "approval_required": True},
        {"id": "M0-CLOSE-001", "title": "M0 收尾补齐：trace_parser _pos(x/y→cell) 修复、checkpoint 级评测、同条件重复评测方差、公平资金 N≥24",
         "status": "PROPOSED", "owner": "DSH", "approval_required": False},
        {"id": "M1-001", "title": "观测 Token 化契约 v3（单位/建筑/资源/地图/敌方可见/生产队列/任务/历史）+ 离线单测",
         "status": "PROPOSED", "owner": "DSH", "approval_required": True},
        {"id": "M1-002", "title": "历史记忆结构选型与原型（GRU/LSTM/Transformer 对比，RTX 4060 8GB 预算内）",
         "status": "PROPOSED", "owner": "DSH", "approval_required": True},
        {"id": "M1-003", "title": "BC 数据链与采集方案（teacher/人类示范、Obs→Action→Result→NextObs、格式与追溯）",
         "status": "PROPOSED", "owner": "DSH", "approval_required": True},
        {"id": "M1-004", "title": "BC 预训练 + 冻结评测（分级能力验收 1-10，先基础操作后战术）",
         "status": "PROPOSED", "owner": "DSH", "approval_required": True},
    ]
    state["backlog_actions"] = old_next + list(state.get("backlog_actions") or [])
    state["active_tasks"] = []
    constraints = list(state.get("constraints") or [])
    for item in [
        "路线 A（原版尤里的复仇）为唯一主线（PRIMARY）；M6 VERA20K 已冻结为辅助底座"
        "（FROZEN_AUXILIARY），以 reports/m6-freeze-2026-11-01/ 清单为准。",
        "M6 重新启用或任何新 PPO 长训需用户另行批准（冻结清单校验通过 + 启用方案）。",
        "M1 涉及新模型结构、观测/动作契约变更、BC 正式训练，均需先提交方案并经用户批准后实施。",
        "M0 遗留缺口（_pos 指标修复、checkpoint 级评测、重复评测方差、公平资金）按 M0-CLOSE-001 处理，"
        "不得以缩小验收口径方式通过。",
    ]:
        if item not in constraints:
            constraints.append(item)
    state["constraints"] = constraints

    state = store.bump_context(
        state,
        reason="route_a_primary__m6_frozen_auxiliary__m1_prep (user strategic decision 2026-11-01)",
        actor="DSH",
    )
    store.regenerate_current_context(state, tasks)

    print("aligned context_version ->", state["context_version"])
    print("project_status:", state["project_status"], "| detail:", state["status_detail"],
          "| stage:", state["current_stage"])
    print("backup:", BACKUP_DIR)
    print("task015 status:", (store.find_task(tasks, "TASK-20261031-015") or {}).get("status"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
