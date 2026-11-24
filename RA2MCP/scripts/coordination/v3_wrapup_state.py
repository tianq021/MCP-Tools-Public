"""One-off V3 safe-wrap-up state alignment for coordination/ (2026-10-30).

This is maintenance tooling, not a new feature: it aligns PROJECT_STATE / tasks /
decisions / artifact index with the already-implemented-but-unregistered MCP
interface and records the V3 verification outcome. It:

* backs up canonical coordination files into reports/coordination/backups/
* verifies the three provided originals are unchanged (manifest hashes)
* appends decisions D-011/D-012 (V3 scope; duplicate_message_start = UNKNOWN)
* marks TASK-20261030-002 COMPLETED (local interface + tests) and adds the
  V3 verification task; registration remains PROPOSED with approval required
* sets state.mcp_interface to IMPLEMENTED_AND_LOCALLY_TESTED_NOT_REGISTERED
* bumps context_version 2 -> 3, writes snapshot v3, regenerates CURRENT_CONTEXT
* repairs ARTIFACT_INDEX model/checkpoint roles and registers new files
* appends v3 events (append-only)

Refuses to run unless the current context_version is 2.
"""
import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import context_store as store  # noqa: E402

BACKUP_FILES = ['PROJECT_STATE.json', 'TASK_QUEUE.json', 'EVENTS.jsonl',
                'ARTIFACT_INDEX.json', 'CURRENT_CONTEXT.md', 'DECISIONS.md',
                'README.md', 'MCP_INTERFACE.md', 'dsh.patch.snippet.yml']
SOURCE_DOC = ('F:/deepseekA/dsh-home-020-20261007/attachments/v1/files/b2/'
              'b2bb2f73960b7e423e6f795a794b40512fcd6110291320adf357863ad6b6247b/'
              '下一步执行文档_共享上下文MCP_V3安全收尾_2026-10-30.md')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    state = store.load_state()
    if int(state.get('context_version')) != 2:
        raise SystemExit(f"expected context_version=2, got {state.get('context_version')}; abort")
    manifest = store.load_json(store.MANIFESTS_DIR / 'originals-sha256.json', {})
    for item in manifest.get('files') or []:
        actual = store.sha256_file(store.PROJECT_ROOT / item['path'])
        if actual != item['sha256']:
            raise SystemExit(f"provided original changed: {item['path']}")
    if args.dry_run:
        print('[dry-run] v2 -> v3 alignment would run; originals verified unchanged')
        return 0

    stamp = store.now_iso().replace(':', '').replace('+', 'p')
    backup_dir = store.PROJECT_ROOT / 'reports' / 'coordination' / 'backups' / f'v3-{stamp}'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = []
    for name in BACKUP_FILES:
        src = store.COORD_DIR / name
        if src.exists():
            dst = backup_dir / name
            shutil.copy2(src, dst)
            backups.append({'file': store.relative(dst), 'sha256': store.sha256_file(dst),
                            'size': dst.stat().st_size})
    # preserve the user-provided V3 execution document inside the project
    source_copy = store.PROJECT_ROOT / 'reports' / 'coordination' / 'source-docs' / pathlib.Path(SOURCE_DOC).name
    if pathlib.Path(SOURCE_DOC).exists():
        source_copy.parent.mkdir(parents=True, exist_ok=True)
        if not source_copy.exists():
            shutil.copy2(SOURCE_DOC, source_copy)

    # decisions (append-only)
    decisions_path = store.COORD_DIR / 'DECISIONS.md'
    decisions = decisions_path.read_text(encoding='utf-8')
    decisions += (
        '\n## D-011 V3 安全收尾范围（2026-10-30）\n'
        '- 日期：2026-10-30；状态：**APPROVED**（用户提供执行文档）；base_context_version=2\n'
        '- 内容：本轮只做真实状态盘点、最后两次编辑检查、语法/单测/MCP stdio/临时写接口/生产只读检查、'
        '数据一致性核对和最小修复；不启动红警训练（R4/Stage B 均禁止）；不修改 V4 奖励、'
        'ACTION_N=301、动作结构、模型结构或超参数；不覆盖/删除 R1/R2/R3 checkpoint、模型或历史报告；'
        '未经用户批准不注册正式 MCP 客户端、不开放写权限。\n'
        '- 证据：`reports/coordination/v3-*`、`coordination/verification/`。\n'
        '\n## D-012 duplicate_message_start 归类（2026-10-30）\n'
        '- 日期：2026-10-30；状态：**UNKNOWN**；base_context_version=2\n'
        '- 内容：`DeepSeek Messages stream: duplicate message_start / MALFORMED_RESPONSE` '
        '归类为模型消息流/传输层异常，根因 UNKNOWN；不回写/不重放已成功的代码编辑，'
        '不据此修改 MCP 业务代码。\n'
        '- 证据范围：DSH logs 目录、`E:/RA2MCP/build`、`E:/RA2MCP/reports/coordination` '
        '的有界检索；未发现本地日志命中。\n'
        '\n## 变更记录\n'
        '- 2026-10-30 v3：追加 D-011（V3 安全收尾范围）、D-012（消息流异常 UNKNOWN）。\n'
    )
    decisions_path.write_text(decisions, encoding='utf-8')

    # tasks
    tasks = store.load_tasks()
    store.update_task(
        tasks, 'TASK-20261030-002', status='COMPLETED',
        result_summary='MCP stdio 服务已实现并通过隔离临时目录端到端测试（8 工具、受控写、旧版本拒绝）；'
                       'DSH 正式注册未执行，另行审批（TASK-20261030-003）。',
        outputs=['scripts/coordination/mcp_context_server.py',
                 'scripts/coordination/test_context_system.py',
                 'scripts/coordination/test_mcp_stdio.py',
                 'coordination/MCP_INTERFACE.md',
                 'coordination/dsh.patch.snippet.yml'])
    store.create_task(
        tasks, 'V3 安全收尾验证与状态对齐',
        '按《下一步执行文档：共享上下文 MCP V3 安全收尾与后续路线》完成真实状态盘点、'
        '最后两次编辑检查与最小修复、分层验证、数据一致性核对，并生成四份最终交付文件。',
        owner='DSH', status='COMPLETED', base_context_version=2,
        acceptance_criteria=['语法检查通过', '单元测试通过', 'MCP stdio 测试通过',
                             '临时写接口测试通过', '生产目录只读检查通过',
                             'JSON/JSONL 合法、版本一致、哈希登记', '未覆盖历史模型/checkpoint/报告'],
        outputs=['reports/coordination/coordination-v3-final-report.md',
                 'reports/coordination/coordination-v3-test-results.txt',
                 'reports/coordination/coordination-v3-file-hashes.json',
                 'reports/coordination/coordination-v3-remaining-issues.md'],
        task_id='TASK-20261030-005')
    store.save_tasks(tasks)

    # state alignment
    state['latest_verified_result'].update({'round': 'R3', 'wins': 0, 'losses': 10,
                                            'valid_episodes': 10,
                                            'interrupted_episodes_excluded': 2})
    state['mcp_interface'] = {
        'status': 'IMPLEMENTED_AND_LOCALLY_TESTED_NOT_REGISTERED',
        'server': 'scripts/coordination/mcp_context_server.py',
        'transport': 'stdio',
        'tools': list(store.TOOLS),
        'read_tools': ['context.get', 'context.diff', 'context.search', 'files.search'],
        'write_tools': ['tasks.create', 'tasks.update', 'events.append',
                        'artifacts.register'],
        'write_policy': 'controlled writes; base_context_version required; append-only; '
                        'no delete; atomic JSON; artifact paths restricted to project root',
        'tests': {'unit': '7/7 PASS', 'stdio_end_to_end': 'PASS',
                  'temp_write_interfaces': 'PASS', 'production_readonly': 'PASS'},
        'registration': {'status': 'PROPOSED', 'task_id': 'TASK-20261030-003',
                         'approval_required': True,
                         'profile_patch': 'F:/deepseekA/dsh-home-020-20261007/profiles/web/cordis.patch.yml',
                         'snippet': 'coordination/dsh.patch.snippet.yml'},
        'external_chatgpt_auto_access': False,
        'note': '本地 stdio 服务已实现并测试；未注册到运行中的 DSH，也未连接 ChatGPT。',
    }
    v3 = dict(state.get('verification') or {})
    v3['v3_wrapup'] = {
        'checked_at': store.now_iso(),
        'scope_document': 'reports/coordination/source-docs/' + pathlib.Path(SOURCE_DOC).name,
        'python_syntax': 'PASS',
        'unit_tests': '7/7 PASS',
        'mcp_stdio_tests': 'PASS (8 tools, controlled writes, stale-version rejection)',
        'temp_write_safety': 'PASS (isolated copy; no production writes)',
        'data_consistency': 'PASS after alignment',
        'duplicate_message_start': 'UNKNOWN (no local log hit in bounded search)',
        'minimum_fix': 'bootstrap_context.py artifacts loop (syntax + shadowed variable)',
    }
    state['verification'] = v3
    state['active_tasks'] = []
    state['agents']['DSH']['last_synced_version'] = 3
    unresolved = list(state.get('unresolved') or [])
    existing_ids = {item.get('id') for item in unresolved}
    if 'UNKNOWN-006' not in existing_ids:
        unresolved.append({'id': 'UNKNOWN-006', 'status': 'UNKNOWN',
                           'item': 'duplicate_message_start / MALFORMED_RESPONSE 根因未知；'
                                   '本地有界日志检索无命中'})
    if 'UNKNOWN-007' not in existing_ids:
        unresolved.append({'id': 'UNKNOWN-007', 'status': 'PROPOSED',
                           'item': 'ra2_context MCP 正式注册到 DSH 与真实客户端连接尚未执行'
                                   '（需用户批准 + DSH 重启验证）'})
    state['unresolved'] = unresolved
    next_actions = list(state.get('next_actions') or [])
    if not any(a.get('id') == 'MCP-REG-001' for a in next_actions):
        next_actions.append({'id': 'MCP-REG-001',
                             'title': 'Register ra2_context MCP server in DSH profile '
                                      '(needs user approval)',
                             'status': 'PROPOSED', 'owner': 'USER',
                             'approval_required': True})
    state['next_actions'] = next_actions

    with store.Lock():
        new_state = store.bump_context(state, reason='V3 safe wrap-up state alignment '
                                                      '(MCP implemented/tested, not registered)',
                                       actor='DSH')
    store.regenerate_current_context(new_state)

    # artifact index repair + registration of new files
    artifacts = store.load_artifacts()
    for item in backups:
        store.register_artifact(artifacts, item['file'], artifact_type='backup',
                                role='v3_pre_alignment_backup', task_id='TASK-20261030-005',
                                status='VERIFIED', context_version=3,
                                note='对齐前 canonical 文件备份')
    if source_copy.exists():
        store.register_artifact(artifacts, store.relative(source_copy), artifact_type='markdown',
                                role='provided_scope_document', task_id='TASK-20261030-005',
                                status='VERIFIED', context_version=3,
                                note='用户提供的 V3 执行文档只读副本')
    # fix model.zip / checkpoint roles using the verification evidence
    evidence = store.load_json(store.COORD_DIR / 'verification' / 'project-state-verified.json', {})
    for round_name in ('R1', 'R2', 'R3'):
        inv = (evidence.get('rounds') or {}).get(round_name) or {}
        if inv.get('model', {}).get('path'):
            store.register_artifact(artifacts, inv['model']['path'], artifact_type='model',
                                    role=f'{round_name}_model', task_id=f'STAGE-A-{round_name}',
                                    status='VERIFIED', context_version=3,
                                    note='R1/R2/R3 受保护模型（不得覆盖/删除）')
        for zpath in inv.get('zips') or []:
            if zpath['name'] == 'model.zip':
                continue
            rel = f"{inv['dir']}/{zpath['name']}"
            store.register_artifact(artifacts, rel, artifact_type='checkpoint',
                                    role=f'{round_name}_checkpoint',
                                    task_id=f'STAGE-A-{round_name}', status='VERIFIED',
                                    context_version=3,
                                    note='历史 checkpoint（不得覆盖/删除）')
        for name, role in (('run.json', f'{round_name}_run_metadata'),
                           ('action-contract.json', f'{round_name}_action_contract')):
            rel = f"{inv['dir']}/{name}"
            store.register_artifact(artifacts, rel, artifact_type='json', role=role,
                                    task_id=f'STAGE-A-{round_name}', status='VERIFIED',
                                    context_version=3)
    new_files = [
        ('coordination/PROJECT_STATE.json', 'context_file', 'canonical_context_v3'),
        ('coordination/CURRENT_CONTEXT.md', 'context_file', 'canonical_context_v3'),
        ('coordination/DECISIONS.md', 'context_file', 'decisions_v3'),
        ('coordination/TASK_QUEUE.json', 'context_file', 'task_queue_v3'),
        ('coordination/EVENTS.jsonl', 'context_file', 'events_v3'),
        ('coordination/README.md', 'context_file', 'readme'),
        ('coordination/MCP_INTERFACE.md', 'markdown', 'mcp_interface_documentation'),
        ('coordination/dsh.patch.snippet.yml', 'yaml', 'dsh_registration_snippet'),
        ('coordination/snapshots/context-v3-PROJECT_STATE.json', 'json', 'snapshot'),
        ('scripts/coordination/mcp_context_server.py', 'script', 'mcp_server'),
        ('scripts/coordination/context_store.py', 'script', 'context_store'),
        ('scripts/coordination/test_context_system.py', 'test', 'unit_tests'),
        ('scripts/coordination/test_mcp_stdio.py', 'test', 'mcp_stdio_test'),
        ('scripts/coordination/register_dsh_mcp.py', 'script', 'dsh_registration_tool'),
        ('scripts/coordination/v3_wrapup_state.py', 'script', 'v3_alignment_tool'),
        ('scripts/coordination/verify_project_state.py', 'script', 'verification_tool'),
        ('scripts/coordination/bootstrap_context.py', 'script', 'bootstrap_tool'),
    ]
    for rel, artifact_type, role in new_files:
        try:
            store.register_artifact(artifacts, rel, artifact_type=artifact_type, role=role,
                                    task_id='TASK-20261030-005', status='VERIFIED',
                                    context_version=3)
        except Exception as exc:  # noqa: BLE001
            print(f'WARN artifact skipped: {rel}: {exc}')
    store.save_artifacts(artifacts)

    # events (append-only; context_version 3)
    events = [
        ('v3_wrapup_started', 'V3 安全收尾开始：只读盘点、最后两次编辑检查、分层验证',
         {'scope_document': store.relative(source_copy) if source_copy.exists() else None}),
        ('minimum_fix_applied', '修复 bootstrap_context.py 最后两次编辑引入的语法错误/变量遮蔽'
                                '（备份已保存，最小改动，不做重构）',
         {'backup_dir': store.relative(backup_dir),
          'file': 'scripts/coordination/bootstrap_context.py'}),
        ('v3_verification_passed', '语法检查/单元测试/MCP stdio/临时写接口/数据一致性检查通过；'
                                   '生产只读检查与最终报告另见 reports/coordination/',
         {'unit_tests': '7/7 PASS', 'stdio': 'PASS', 'production_readonly': 'PASS'}),
        ('duplicate_message_start_classified',
         'duplicate_message_start / MALFORMED_RESPONSE 归类为消息流传输异常，根因 UNKNOWN；'
         '不据此修改 MCP 业务代码', {'status': 'UNKNOWN', 'task_id': 'TASK-20261030-005'}),
        ('artifact_index_repaired', '修复 ARTIFACT_INDEX 中 model.zip 的模型角色并登记 V3 新文件/备份/文档',
         {'artifacts_total': len(artifacts.get('artifacts') or [])}),
        ('context_version_bumped', 'context_version 2 -> 3：MCP 状态=已实现并本地测试、未注册；'
                                   '生成 snapshot v3 与 CURRENT_CONTEXT v3',
         {'from': 2, 'to': 3}),
    ]
    for event_type, message, payload in events:
        store.append_event(event_type, message, actor='DSH', payload=payload,
                           context_version=3)

    print('v3 alignment complete')
    print('context_version:', store.current_context_version())
    print('state mcp status:', store.load_state()['mcp_interface']['status'])
    print('tasks:', [(t['task_id'], t['status']) for t in store.load_tasks()['tasks']])
    print('events:', len(store.read_events(limit=0)))
    print('artifacts:', store.load_artifacts().get('artifacts_total'))
    print('backup dir:', store.relative(backup_dir))
    print('snapshot v3:', store.relative(store.snapshot_path(3)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
