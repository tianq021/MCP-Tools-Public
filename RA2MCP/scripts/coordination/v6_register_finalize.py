"""Finalize the ra2_context MCP production registration into context_version 9.

Assumes the registration was already applied to the DSH profile and verified
through the real MCP client (tasks.update/events.append/artifacts.register), so
current context_version == 8. This records the registration/verification state,
adds D-013, updates tasks/next-actions/unresolved lists, bumps to v9 and
registers evidence artifacts.

Refuses unless current context_version == 8.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import context_store as store  # noqa: E402

PROFILE = 'F:/deepseekA/dsh-home-020-20261007/profiles/web/cordis.patch.yml'
PROFILE_BEFORE = '3098dbd0abebaa87b9bb637654830c7b9b59f46ee26c7ae13c3eed657ade5ffb'
PROFILE_AFTER = 'c7450b5283698a40bcc1aa3ea3c2b8af1ad6be71a78fde2c95665e677a116115'
REGISTER_LOG = 'reports/coordination/v6-mcp-register-20261030-174230.log'
REPORT = 'reports/coordination/coordination-mcp-registration-verified-20261030.md'
PROJECT_BACKUP = ('reports/coordination/backups/'
                  'dsh-cordis.patch.yml.before-ra2-context-20261030-174225.bak')
TOOLS = ['context.get', 'context.diff', 'context.search', 'files.search',
         'tasks.create', 'tasks.update', 'events.append', 'artifacts.register']


def main():
    state = store.load_state()
    if int(state.get('context_version')) != 8:
        raise SystemExit(f'expected context_version=8, got {state.get("context_version")}')

    # decisions (append-only)
    decisions_path = store.COORD_DIR / 'DECISIONS.md'
    decisions = decisions_path.read_text(encoding='utf-8')
    decisions += (
        '\n## D-013 `ra2_context` MCP 生产注册（2026-10-30）\n'
        '- 日期：2026-10-30；状态：**APPROVED**（用户指令「继续」）+ **VERIFIED**；'
        'base_context_version=5\n'
        '- 内容：用户批准后，将 `ra2_context` stdio MCP 注册到 DSH profile '
        f'`{PROFILE}`。注册前备份（sha256 `{PROFILE_BEFORE}`），注册后 sha256 '
        f'`{PROFILE_AFTER}`；DSH profile 热加载生效，当前会话可见 8 个工具。\n'
        '- 验证：真实 DSH MCP 客户端完成 context.get（只读）、tasks.update、'
        'events.append、artifacts.register（受控写，context_version 5→8）。\n'
        '- 边界：ChatGPT 侧连接器仍未建立，`external_chatgpt_auto_access=false`；'
        '不得声称 ChatGPT 已自动读取本地文件。\n'
        '- 回退：用上述 profile 备份覆盖并重启/重载 DSH；保留 coordination 证据。\n'
        '\n## 变更记录\n'
        '- 2026-10-30 v9：追加 D-013（MCP 生产注册与验证）。\n'
    )
    decisions_path.write_text(decisions, encoding='utf-8')

    # tasks: MCP registration is already COMPLETED via the real MCP write path.
    tasks = store.load_tasks()
    store.create_task(
        tasks, 'ChatGPT 侧连接本地 coordination（需用户批准）',
        '在 ChatGPT 侧配置可访问本机 coordination 的 MCP/连接器，或建立受控导出/同步流程；'
        '在连接验证前不得声称 ChatGPT 已自动读取本地文件。',
        owner='USER', status='PROPOSED', base_context_version=8,
        approval_required=True, task_id='CHATGPT-CONN-001',
        acceptance_criteria=['ChatGPT 侧可读取 CURRENT_CONTEXT.md/PROJECT_STATE.json',
                             'external_chatgpt_auto_access 经验证后更新为 true',
                             '读取范围与权限可审计、不暴露写权限'])
    store.save_tasks(tasks)

    # state
    state['mcp_interface']['status'] = 'REGISTERED_AND_VERIFIED_LIVE_DSH'
    state['mcp_interface']['registration'] = {
        'status': 'REGISTERED_AND_VERIFIED',
        'approved_by': 'USER (message: 继续)',
        'approved_at': '2026-10-30T17:42:00+0800',
        'applied_at': '2026-10-30T17:42:30+0800',
        'profile_patch': PROFILE,
        'profile_sha256_before': PROFILE_BEFORE,
        'profile_sha256_after': PROFILE_AFTER,
        'backup_next_to_profile': PROFILE + '.before-ra2-context-20261030-174230.bak',
        'backup_in_project': PROJECT_BACKUP,
        'dsh_hot_reload': True,
        'tools_visible': TOOLS,
        'client_tool_name_mapping_note': 'DSH exposes tools with hash suffixes '
                                         '(mcp__ra2_context__<tool>_<hash>)',
        'read_verification': 'context.get OK via real DSH MCP client',
        'write_verification': ['tasks.update TASK-20261030-003 COMPLETED (5->6)',
                               'events.append mcp_production_registration_verified (6->7)',
                               'artifacts.register profile backup (7->8)'],
        'external_chatgpt_auto_access': False,
        'rollback': 'restore the backed-up cordis.patch.yml and restart/reload DSH',
    }
    state['mcp_interface']['external_chatgpt_auto_access'] = False
    state['mcp_interface']['note'] = ('DSH 已注册并验证（8 工具，读写链路真实客户端可用）；'
                                      'ChatGPT 侧连接器仍未建立。')
    state['agents']['DSH']['last_synced_version'] = 9
    state['verification']['mcp_registration'] = {
        'checked_at': store.now_iso(),
        'profile_sha256_before': PROFILE_BEFORE,
        'profile_sha256_after': PROFILE_AFTER,
        'dsh_hot_reload': True,
        'read_verification': 'PASS',
        'write_verification': 'PASS (3 controlled writes, context_version 5->8)',
        'chatgpt_connection': 'NOT_ESTABLISHED',
        'evidence': [REGISTER_LOG, PROJECT_BACKUP, REPORT],
    }
    for action in state.get('next_actions') or []:
        if action.get('id') == 'MCP-REG-001':
            action['status'] = 'COMPLETED'
            action['completed_at'] = '2026-10-30T17:43:09+0800'
    if not any(a.get('id') == 'CHATGPT-CONN-001' for a in state.get('next_actions') or []):
        state['next_actions'].append({
            'id': 'CHATGPT-CONN-001',
            'title': 'Connect ChatGPT to local coordination (needs approval)',
            'status': 'PROPOSED', 'owner': 'USER', 'approval_required': True})
    unresolved = list(state.get('unresolved') or [])
    for item in unresolved:
        if item.get('id') == 'UNKNOWN-007':
            item['status'] = 'SUPERSEDED'
            item['item'] = ('ra2_context MCP 已在 DSH 注册并验证（v9）；'
                            'ChatGPT 侧连接仍待处理（见 UNKNOWN-008）')
    if not any(i.get('id') == 'UNKNOWN-008' for i in unresolved):
        unresolved.append({'id': 'UNKNOWN-008', 'status': 'UNKNOWN',
                           'item': 'ChatGPT 侧 MCP/连接器未建立，无法自动读取本地 '
                                   'coordination；需要用户批准与配置'})
    state['unresolved'] = unresolved
    state['active_tasks'] = []

    with store.Lock():
        new_state = store.bump_context(state,
                                       reason='ra2_context MCP registered in DSH and '
                                              'verified via real MCP client (v8->v9)',
                                       actor='DSH')
    new_state['agents']['DSH']['last_synced_version'] = 9
    store.save_state(new_state)
    store.regenerate_current_context(new_state)

    store.append_event(
        'mcp_registration_recorded',
        'ra2_context MCP 生产注册状态写入上下文：DSH 已注册/热加载/真实客户端读写验证；'
        'ChatGPT 未连接，新增 CHATGPT-CONN-001（PROPOSED）',
        actor='DSH',
        payload={'profile_patch': PROFILE,
                 'profile_sha256_before': PROFILE_BEFORE,
                 'profile_sha256_after': PROFILE_AFTER,
                 'context_version': 9,
                 'chatgpt_connection': 'NOT_ESTABLISHED'},
        context_version=9)

    artifacts = store.load_artifacts()
    registrations = [
        ('coordination/PROJECT_STATE.json', 'context_file', 'canonical_context_v9'),
        ('coordination/CURRENT_CONTEXT.md', 'context_file', 'canonical_context_v9'),
        ('coordination/DECISIONS.md', 'context_file', 'decisions_v9'),
        ('coordination/TASK_QUEUE.json', 'context_file', 'task_queue_v9'),
        ('coordination/EVENTS.jsonl', 'context_file', 'events_v9'),
        ('coordination/MCP_INTERFACE.md', 'markdown', 'mcp_interface_documentation_v9'),
        ('coordination/snapshots/context-v9-PROJECT_STATE.json', 'json', 'snapshot'),
        (REPORT, 'markdown', 'mcp_registration_verification_report'),
        (REGISTER_LOG, 'test_log', 'mcp_registration_log'),
        (PROJECT_BACKUP, 'config_backup', 'dsh_profile_pre_registration_backup'),
    ]
    for rel, artifact_type, role in registrations:
        store.register_artifact(artifacts, rel, artifact_type=artifact_type, role=role,
                                task_id='TASK-20261030-003', status='VERIFIED',
                                context_version=9)
    store.save_artifacts(artifacts)

    final = store.load_state()
    print('v6 registration finalize complete; context_version:', final['context_version'])
    print('mcp status:', final['mcp_interface']['status'])
    print('registration status:', final['mcp_interface']['registration']['status'])
    print('chatgpt auto access:', final['mcp_interface']['registration']['external_chatgpt_auto_access'])
    print('tasks:', [(t['task_id'], t['status']) for t in store.load_tasks()['tasks']])
    print('events:', len(store.read_events(limit=0)))
    print('artifacts:', store.load_artifacts().get('artifacts_total'))
    print('report sha256:', store.sha256_file(store.PROJECT_ROOT / REPORT))
    print('profile backup sha256:', store.sha256_file(store.PROJECT_ROOT / PROJECT_BACKUP))
    return 0


if __name__ == '__main__':
    sys.exit(main())
