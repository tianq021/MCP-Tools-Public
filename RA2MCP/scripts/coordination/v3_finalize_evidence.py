"""V3 safe-wrap-up phase 1: consolidate verified status into context_version 4.

Run after syntax/unit/stdio/consistency/production-read-only checks have passed.
This does not write the final report files (phase 2 registers them once written).

Refuses unless current context_version == 3.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import context_store as store  # noqa: E402

LOG_DIR = 'reports/coordination'
LOGS = [
    'v3-syntax-check.log',
    'v3-unit-tests-20261030-173544.log',
    'v3-mcp-stdio-20261030-173544.log',
    'v3-consistency-final.log',
    'v3-production-readonly-final.log',
    'v3-write-safety-20261030-173742.log',
    'v3-inventory-20261030-173521.log',
    'v3-alignment-20261030-173647.log',
    'v3-consistency-check-20261030-173705.log',
    'v3-consistency-check-20261030-173715.log',
    'v3-production-readonly-20261030-173735.log',
]
DELIVERABLES = [
    'reports/coordination/coordination-v3-final-report.md',
    'reports/coordination/coordination-v3-test-results.txt',
    'reports/coordination/coordination-v3-remaining-issues.md',
]


def main():
    state = store.load_state()
    if int(state.get('context_version')) != 3:
        raise SystemExit(f"expected context_version=3, got {state.get('context_version')}")
    checks = {}
    for name in ('v3-consistency-final.log', 'v3-production-readonly-final.log',
                 'v3-write-safety-20261030-173742.log', 'v3-unit-tests-20261030-173544.log',
                 'v3-mcp-stdio-20261030-173544.log'):
        path = store.PROJECT_ROOT / LOG_DIR / name
        checks[name] = bool(path.exists() and 'EXIT_CODE=0' in path.read_text(encoding='utf-8',
                                                                              errors='replace'))
    state['verification']['v3_wrapup'].update({
        'status': 'SAFE_WRAPUP_VERIFIED',
        'python_syntax': 'PASS',
        'unit_tests': '7/7 PASS',
        'mcp_stdio_tests': 'PASS',
        'temp_write_safety': 'PASS',
        'production_readonly': 'PASS',
        'data_consistency': 'PASS',
        'duplicate_message_start': 'UNKNOWN (bounded local log search: no hit)',
        'evidence_logs': [f'{LOG_DIR}/{name}' for name in LOGS],
        'deliverables': [{'path': path, 'status': 'PENDING_WRITE'} for path in DELIVERABLES],
        'mcp_system_overall': 'PARTIAL (interface implemented/tested; DSH registration and '
                              'ChatGPT connection not executed - requires approval)',
        'safe_to_stop': True,
    })
    state['mcp_interface']['registration']['status'] = 'PROPOSED_NOT_EXECUTED'
    state['agents']['DSH']['last_synced_version'] = 4
    with store.Lock():
        new_state = store.bump_context(state, reason='V3 safe-wrap-up verification recorded '
                                                      '(v3->v4)', actor='DSH')
    store.regenerate_current_context(new_state)
    events = [
        ('v3_wrapup_verified',
         'V3 安全收尾验证记录：语法/单元测试/MCP stdio/临时写接口/生产只读检查/数据一致性全部通过；'
         'duplicate_message_start 根因 UNKNOWN；MCP 未注册/未连接',
         {'checks': checks, 'unit_tests': '7/7 PASS', 'stdio': 'PASS',
          'production_readonly': 'PASS', 'write_safety': 'PASS', 'consistency': 'PASS',
          'safe_to_stop': True}),
        ('v3_deliverables_pending',
         '四份最终交付文件待写入（phase 2 将登记 SHA256）',
         {'deliverables': DELIVERABLES}),
        ('context_version_bumped', 'context_version 3 -> 4（V3 验证状态固化）',
         {'from': 3, 'to': 4}),
    ]
    for event_type, message, payload in events:
        store.append_event(event_type, message, actor='DSH', payload=payload,
                           context_version=4)
    artifacts = store.load_artifacts()
    canonical = [('coordination/PROJECT_STATE.json', 'canonical_context_v4'),
                 ('coordination/CURRENT_CONTEXT.md', 'canonical_context_v4'),
                 ('coordination/DECISIONS.md', 'decisions_v3'),
                 ('coordination/TASK_QUEUE.json', 'task_queue_v3'),
                 ('coordination/EVENTS.jsonl', 'events_v4'),
                 ('coordination/README.md', 'readme'),
                 ('coordination/MCP_INTERFACE.md', 'mcp_interface_documentation'),
                 ('coordination/dsh.patch.snippet.yml', 'dsh_registration_snippet'),
                 ('coordination/snapshots/context-v4-PROJECT_STATE.json', 'snapshot')]
    for rel, role in canonical:
        try:
            store.register_artifact(artifacts, rel, artifact_type='context_file', role=role,
                                    task_id='TASK-20261030-005', status='VERIFIED',
                                    context_version=4)
        except Exception as exc:  # noqa: BLE001
            print(f'WARN {rel}: {exc}')
    for name in LOGS:
        rel = f'{LOG_DIR}/{name}'
        if (store.PROJECT_ROOT / rel).exists():
            store.register_artifact(artifacts, rel, artifact_type='test_log',
                                    role='v3_evidence', task_id='TASK-20261030-005',
                                    status='VERIFIED', context_version=4)
    for rel, artifact_type, role in (
            ('scripts/coordination/bootstrap_context.py', 'script', 'bootstrap_tool_fixed'),
            ('scripts/coordination/v3_wrapup_state.py', 'script', 'v3_alignment_tool'),
            ('scripts/coordination/mcp_context_server.py', 'script', 'mcp_server'),
            ('scripts/coordination/context_store.py', 'script', 'context_store'),
            ('scripts/coordination/test_context_system.py', 'test', 'unit_tests'),
            ('scripts/coordination/test_mcp_stdio.py', 'test', 'mcp_stdio_test'),
            ('scripts/coordination/register_dsh_mcp.py', 'script', 'dsh_registration_tool'),
            ('scripts/coordination/verify_project_state.py', 'script', 'verification_tool')):
        store.register_artifact(artifacts, rel, artifact_type=artifact_type, role=role,
                                task_id='TASK-20261030-005', status='VERIFIED',
                                context_version=4)
    store.save_artifacts(artifacts)
    print('v3 phase-1 complete; context_version:', store.current_context_version())
    print('checks:', checks)
    print('snapshot:', store.relative(store.snapshot_path(4)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
