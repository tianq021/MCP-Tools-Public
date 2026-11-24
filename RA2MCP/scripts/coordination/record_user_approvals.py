#!/usr/bin/env python
"""Record user-authorized approvals AP-01/AP-06a/AP-03 (explicit user decision; AP-02 deferred)."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(r"E:\RA2MCP")
sys.path.insert(0, str(ROOT / "scripts" / "coordination"))
import context_store as store  # noqa: E402

EXPECTED = 246
NOW = store.now_iso()


def main() -> int:
    state = store.load_state()
    if int(state.get("context_version") or 0) != EXPECTED:
        raise SystemExit("context mismatch")
    version = EXPECTED

    tasks = store.load_tasks()
    t1 = store.create_task(
        tasks,
        "AP-06a 执行：M0 收尾非游戏工程（P0-1/4/5/6/7mock/8/9/10/11/12/13/14）",
        "用户 2026-11-01 明确授权 AP-06a。范围仅非游戏工程/只读/文档/mock：P0-1、P0-4、P0-5、P0-6、P0-7（仅 mock/dry-run）、"
        "P0-8、P0-9、P0-10、P0-11、P0-12（预检，无真实对局）、P0-13、P0-14；不含 P0-2/P0-3（AP-06b，未批准）；"
        "不启动游戏/bridge/采集/训练；不改保护资产。",
        owner="DSH", status="APPROVED", base_context_version=version, approval_required=False,
        outputs=["reports/m0-close-2026-11-01/M0_CLOSE_REPORT.md"],
    )
    t2 = store.create_task(
        tasks,
        "AP-03 执行：P4a 教师小规模验证（真实游戏 20–30 局，≤1.5h wall）",
        "用户 2026-11-01 明确授权 AP-03。执行前需用户另行下达启动指令（将启动原版游戏 test-game 副本与 bridge）；"
        "范围：20–30 局、≤1.5h wall、记录 ACK/applied/结局分布/12 宏覆盖计数/map_soa 值域扫描/隐藏敌回归/MCV→CY；"
        "不采集正式数据、不训练、不改保护资产、不写 E:\\hj。",
        owner="DSH", status="APPROVED", base_context_version=version, approval_required=False,
        outputs=["reports/m1-2026-11-01/P4A_TEACHER_VALIDATION.md"],
    )
    store.save_tasks(tasks)

    state = store.load_state()
    md = dict(state.get("m1_design") or {})
    md.update({
        "approval_items": md.get("approval_items") or [],
        "approval_status_detail": {
            "AP-01": {"status": "APPROVED", "by": "user", "at": NOW,
                      "scope": "design baseline v1.6.1 (no execution)"},
            "AP-02": {"status": "PROPOSED", "note": "deferred by user; restricted P2 start approval to be handled separately"},
            "AP-03": {"status": "APPROVED", "by": "user", "at": NOW,
                      "scope": "P4a teacher validation (real game, <=1.5h); execution requires explicit start instruction"},
            "AP-06a": {"status": "APPROVED", "by": "user", "at": NOW,
                       "scope": "M0-close non-game engineering (P0-1/4/5/6/7mock/8/9/10/11/12/13/14)"},
            "AP-06b": {"status": "PROPOSED"}, "AP-07": {"status": "BLOCKED"},
            "AP-04": {"status": "PROPOSED"}, "AP-05": {"status": "PROPOSED"},
            "AP-08": {"status": "PROPOSED"}, "AP-09": {"status": "PROPOSED"},
            "AP-10": {"status": "PROPOSED", "note": "P3 entry; gated on G-P2"},
        },
        "approval_status": "AP-01/AP-06a/AP-03 APPROVED (user, 2026-11-01); AP-02 deferred; others PROPOSED",
        "approved_tasks": [t1["task_id"], t2["task_id"]],
    })
    state["m1_design"] = md
    state["active_tasks"] = [t1["task_id"], t2["task_id"]]
    state = store.bump_context(state, reason="user authorized AP-01/AP-06a/AP-03; AP-02 deferred; approvals recorded per user decision", actor="DSH")
    store.append_event(
        "approvals_granted_by_user",
        "用户授权：AP-01（v1.6.1 设计基线）、AP-06a（M0 收尾非游戏工程）、AP-03（P4a 教师验证）状态记为 APPROVED（用户决定，非自行批准）；"
        "AP-02 由用户要求稍后单独处理，保持 PROPOSED；AP-10/AP-04/AP-05/AP-06b/AP-08/AP-09 保持 PROPOSED；AP-07 BLOCKED。"
        "已建任务并等待执行指令；未启动游戏/bridge/采集/训练；保护资产未变。",
        actor="DSH",
        payload={"context_version": state["context_version"],
                 "approved": {"AP-01": "user", "AP-06a": "user", "AP-03": "user"},
                 "deferred": ["AP-02"], "proposed_unchanged": ["AP-04", "AP-05", "AP-06b", "AP-07", "AP-08", "AP-09", "AP-10"],
                 "tasks": [t1["task_id"], t2["task_id"]],
                 "execution_started": False},
        context_version=state["context_version"],
    )
    store.regenerate_current_context(state, tasks)
    print("context_version ->", state["context_version"])
    print("approved tasks:", t1["task_id"], t2["task_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
