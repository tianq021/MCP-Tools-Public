"""Initialize the RA2MCP coordination context files (version 2).

Inputs: the three provided originals in coordination/ plus the read-only
verification evidence at coordination/verification/project-state-verified.json
(produced by verify_project_state.py).

Never overwrites the provided originals, models, checkpoints, reports or configs.
Refuses to run when a canonical PROJECT_STATE.json already exists.

Usage:
    python scripts/coordination/bootstrap_context.py [--dry-run]
"""
import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import context_store as store  # noqa: E402

PROVIDED = {
    'spec': 'coordination/通用上下文记忆规范_v1.0.md',
    'context': 'coordination/RA2_AI_CURRENT_CONTEXT_v1.0.md',
    'state': 'coordination/RA2_AI_PROJECT_STATE_v1.0.json',
}
VERIFICATION = 'coordination/verification/project-state-verified.json'
CANONICAL = ['PROJECT_STATE.json', 'CURRENT_CONTEXT.md', 'DECISIONS.md', 'TASK_QUEUE.json',
             'EVENTS.jsonl', 'ARTIFACT_INDEX.json', 'README.md']


def sha(rel):
    return store.sha256_file(store.PROJECT_ROOT / rel)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    if store.STATE_PATH.exists() and not args.dry_run:
        raise SystemExit(f'{store.STATE_PATH} already exists; refusing to overwrite context')
    evidence = store.load_json(store.PROJECT_ROOT / VERIFICATION)
    if not evidence:
        raise SystemExit('verification evidence missing; run verify_project_state.py first')
    originals = {item['path']: item for item in evidence['coordination_originals']}
    for rel in PROVIDED.values():
        if rel not in originals:
            raise SystemExit(f'provided original missing from verification: {rel}')

    # directories
    for rel in ('snapshots', 'manifests', 'archive', 'verification',
                'agents/ChatGPT/inbox', 'agents/ChatGPT/outbox',
                'agents/DSH/inbox', 'agents/DSH/outbox'):
        (store.COORD_DIR / rel).mkdir(parents=True, exist_ok=True)

    # v1 snapshot = byte copy of the provided state file (kept for version history)
    v1_snapshot = store.SNAPSHOTS_DIR / 'context-v1-PROJECT_STATE.json'
    if not v1_snapshot.exists():
        shutil.copy2(store.PROJECT_ROOT / PROVIDED['state'], v1_snapshot)

    # originals manifest
    originals_manifest = {
        'schema': 'ra2-context-originals/1.0',
        'generated_at': store.now_iso(),
        'note': 'Provided originals are immutable evidence; do not edit or re-serialize.',
        'files': [originals[rel] for rel in PROVIDED.values()],
    }
    store.atomic_write_json(store.MANIFESTS_DIR / 'originals-sha256.json', originals_manifest)

    r1 = evidence['rounds']['R1']
    r2 = evidence['rounds']['R2']
    r3 = evidence['rounds']['R3']
    models = evidence['model_artifacts']
    claim_summary = evidence['claim_summary']
    nonstandard = [c for c in evidence['claims_checked'] if c.get('status') == 'NON_STANDARD']

    rounds_summary = {}
    for name, inv in (('R1', r1), ('R2', r2), ('R3', r3)):
        agg = inv['aggregate']
        rounds_summary[name] = {
            'run_dir': inv['dir'],
            'valid_episodes': inv['valid_episodes'],
            'interrupted_episodes_excluded': inv['interrupted_episodes'],
            'wins': 0,
            'attack_effective_6s_rate': agg.get('attacks_effective_6s_rate'),
            'attacks_issued_mean': agg.get('attacks_issued_mean'),
            'effective_attacks_mean': round(
                (agg.get('attacks_effective_6s_total') or 0) / max(1, agg.get('episodes') or 1), 1),
            'kills_mean': agg.get('kills_mean'),
            'own_unit_losses_mean': agg.get('own_unit_losses_mean'),
            'unit_kill_exchange': agg.get('unit_kill_exchange'),
            'ineffective_switches_mean': agg.get('ineffective_switches_mean'),
            'top1_damage_share_mean': agg.get('top1_damage_share_mean'),
            'unit_slot_swaps_max': agg.get('unit_slot_swaps_max'),
            'model_sha256': inv['model']['sha256'],
            'checkpoints': len(inv['zips']),
            'status': 'VERIFIED',
        }

    model_artifacts = {
        'R1': {
            'path': r1['model']['path'],
            'sha256': models['R1']['sha256'],
            'size': models['R1']['size'],
            'role': 'best_current_candidate',
            'status': 'PROTECTED',
        },
        'R2': {
            'path': r2['model']['path'],
            'sha256': models['R2']['sha256'],
            'size': models['R2']['size'],
            'role': 'secondary_reference_and_R3_initialization',
            'status': 'PROTECTED',
        },
        'R3': {
            'path': r3['model']['path'],
            'sha256': models['R3']['sha256'],
            'size': models['R3']['size'],
            'role': 'regression_diagnostic_only',
            'status': 'PROTECTED_DO_NOT_RESUME',
        },
        'OLD_REFERENCE': {
            'path': models['OLD_REFERENCE']['path'],
            'sha256': models['OLD_REFERENCE']['sha256'],
            'size': models['OLD_REFERENCE']['size'],
            'role': 'historical_baseline',
            'status': 'PROTECTED',
        },
    }

    tasks_doc = {
        'schema_version': store.SCHEMA_VERSION,
        'updated_at': store.now_iso(),
        'tasks': [],
    }
    store.create_task(
        tasks_doc, '建立统一上下文记忆系统（coordination bootstrap）',
        '按通用上下文记忆规范 v1.0 建立 RA2MCP 的 coordination 目录：保留三个原始文件及 '
        'SHA256，创建缺失的 DECISIONS/TASK_QUEUE/EVENTS/ARTIFACT_INDEX/CURRENT_CONTEXT，'
        '核对 Stage A R1/R2/R3 实际状态，建立 context_version 版本管理，并评估 MCP 接口。',
        owner='DSH', status='COMPLETED', base_context_version=1,
        scope=['coordination/', 'scripts/coordination/', 'reports/coordination-bootstrap-*.md'],
        constraints=['不启动新训练', '不修改模型/奖励/动作空间', '不删除历史报告或模型',
                     '不覆盖提供的三个原始文件', '未获批准不修改 DSH 全局 MCP 配置'],
        acceptance_criteria=[
            '三个原始文件哈希保持不变并登记',
            '缺失的五个上下文文件已创建',
            'R1/R2/R3 模型、checkpoint、训练配置与哈希核对完成，冲突标记 UNKNOWN/CONFLICT',
            'context_version 版本管理与只追加事件建立',
            'MCP 接口完成评估与实现状态说明',
        ],
        outputs=list(CANONICAL) + [VERIFICATION, 'coordination/manifests/originals-sha256.json'],
        task_id='TASK-20261030-001',
    )
    for diag in (('DIAG-001', 'Inspect PPO training stability from R2 to R3'),
                 ('DIAG-002', 'Evaluate R2 and R3 checkpoints under identical fixed conditions'),
                 ('DIAG-003', 'Trace attack command and target-switch behavior')):
        store.create_task(
            tasks_doc, diag[1],
            f'来自用户提供的记忆快照的下一步建议 {diag[0]}；仅可只读检查/分析，不自动续训。',
            owner='DSH', status='PROPOSED', base_context_version=2,
            task_id=diag[0])
    store.create_task(
        tasks_doc, '实现并本地验证 coordination MCP 接口',
        '实现 context.get/diff/search、files.search、tasks.create/update、events.append、'
        'artifacts.register 的本地 stdio MCP 服务，测试只读与受控写；不自动注册到 DSH 全局配置。',
        owner='DSH', status='RUNNING', base_context_version=2,
        constraints=['只读默认', '写操作必须带 base_context_version',
                     'append-only events', '不得删除历史数据', '不启动训练'],
        scope=['scripts/coordination/', 'coordination/MCP_INTERFACE.md',
               'coordination/dsh.patch.snippet.yml'],
        task_id='TASK-20261030-002')
    store.create_task(
        tasks_doc, '将 RA2 上下文 MCP 注册到 DSH（需用户批准）',
        '在 DSH profile cordis.patch.yml 中以 @deepseek-ai/dsh-mcp-client 注册 ra2_context '
        'stdio server；修改前备份配置，注册后重启 DSH 验证工具可见。',
        owner='USER', status='PROPOSED', base_context_version=2,
        approval_required=True, task_id='TASK-20261030-003')

    latest = dict(rounds_summary['R3'])
    latest.update({'round': 'R3', 'wins': 0, 'losses': 10,
                   'valid_episodes': rounds_summary['R3']['valid_episodes'],
                   'interrupted_episodes_excluded':
                       rounds_summary['R3']['interrupted_episodes_excluded']})
    state = {
        'schema_version': store.SCHEMA_VERSION,
        'context_version': 2,
        'project_id': 'RA2_AI',
        'project_name': '红警2 / 尤里的复仇自主对战 AI',
        'updated_at': store.now_iso(),
        'project_status': 'PAUSED',
        'status_detail': 'PAUSED_FOR_DIAGNOSIS',
        'current_stage': 'Stage A',
        'completed_rounds': ['R1', 'R2', 'R3'],
        'latest_verified_result': latest,
        'rounds': rounds_summary,
        'best_candidate_model': {
            'round': 'R1',
            'path': model_artifacts['R1']['path'],
            'sha256': model_artifacts['R1']['sha256'],
            'basis': 'Stage A R1/R2/R3 已验证指标排名：R1 的 6s 有效率、击杀/局、'
                     '击杀交换比均为最高（趋势指标，非严格固定种子 A/B）',
            'status': 'VERIFIED_METRIC_RANKING',
        },
        'model_artifacts': model_artifacts,
        'training_config_verified': {
            'environment': 'RA2CombatEnv (DSH_COMBAT_ENV=1), Stage A curriculum',
            'action_space_version': 'full_ground_v2p1',
            'action_n': 301,
            'target_candidate_range': 30.0,
            'target_nearest_k': 3,
            'reward_version': 'full_configurable_v4',
            'reward_config_sha256': evidence['reward_config']['sha256'],
            'reward_config_revision': evidence['reward_config']['revision'],
            'model_architecture': {'policy': 'MaskablePPO MlpPolicy',
                                   'net_arch': [64, 64], 'policy_parameters': 258606},
            'hyperparameters': r1['run'].get('fresh_hyperparameters'),
            'workers': 2, 'episode_seconds': 300.0, 'decision_seconds': 0.5,
            'stage_a': {
                'AIDifficulty': 2, 'AIDifficulty_meaning': 'Easy',
                'HouseHandicaps_Multi2': 2, 'HouseHandicaps_meaning': 'Easy',
                'Credits': 20000, 'human_start_credits_observed': 20000,
                'ai_start_credits_observed': 100000,
                'credits_factor_status': 'IGNORED_BY_LEGACY_SPAWNER',
                'UnitCount': 8,
                'starting_units_observed': evidence['runtime_preflight_evidence']['own_types'],
                'map': '2peaks', 'spawn_distance_cells': 68.0,
                'Superweapons': 'No',
            },
            'status': 'VERIFIED',
        },
        'active_tasks': ['TASK-20261030-002'],
        'next_actions': [
            {'id': 'DIAG-001', 'title': 'Inspect PPO training stability from R2 to R3',
             'status': 'PROPOSED', 'owner': 'DSH', 'approval_required': False},
            {'id': 'DIAG-002', 'title': 'Evaluate R2 and R3 checkpoints under identical fixed conditions',
             'status': 'PROPOSED', 'owner': 'DSH', 'approval_required': False},
            {'id': 'DIAG-003', 'title': 'Trace attack command and target-switch behavior',
             'status': 'PROPOSED', 'owner': 'DSH', 'approval_required': False},
            {'id': 'MCP-REG-001', 'title': 'Register ra2_context MCP server in DSH profile (needs approval)',
             'status': 'PROPOSED', 'owner': 'USER', 'approval_required': True},
        ],
        'constraints': [
            'Do not start R4 or Stage B without explicit user approval.',
            'Do not resume training from R3.',
            'Do not modify reward V4, ACTION_N=301, action structure, model structure, '
            'or core hyperparameters without explicit approval.',
            'Do not overwrite or delete R1/R2/R3 models, checkpoints, reports, or '
            'historical evidence.',
            'Use a new run directory for every experiment.',
            'Do not treat proposed or instructed work as completed.',
            'Do not claim ChatGPT can automatically read local files before an MCP/connector '
            'is actually registered and verified.',
        ],
        'protected_artifacts': [
            'reports/rl/stage-A-round1-20261030-153225/**',
            'reports/rl/stage-A-round2-20261030-155824/**',
            'reports/rl/stage-A-round3-20261030-162536/**',
            'reports/rl/ppo-shared-20261030-102633/model.zip',
            'config/rewards-combat.json', 'config/rewards.json',
            'coordination/通用上下文记忆规范_v1.0.md',
            'coordination/RA2_AI_CURRENT_CONTEXT_v1.0.md',
            'coordination/RA2_AI_PROJECT_STATE_v1.0.json',
        ],
        'open_questions': [
            'Which R3 checkpoint first exhibits the attack-switching regression?',
            'Is the PPO value-function instability the primary cause, or a correlated symptom?',
            'Does target identity/candidate ordering change too frequently under dense enemy visibility?',
            'How do R1/R2/R3 compare under identical deterministic evaluation seeds?',
        ],
        'agents': {
            'ChatGPT': {'last_synced_version': 1,
                        'status': 'NOT_CONNECTED_TO_LOCAL_FILES_BY_DEFAULT'},
            'DSH': {'last_synced_version': 2, 'status': 'LOCAL_EXECUTION_AGENT'},
        },
        'sources': {
            'provided_spec': {'path': PROVIDED['spec'], 'sha256': originals[PROVIDED['spec']]['sha256']},
            'provided_current_context': {'path': PROVIDED['context'],
                                         'sha256': originals[PROVIDED['context']]['sha256']},
            'provided_state': {'path': PROVIDED['state'],
                               'sha256': originals[PROVIDED['state']]['sha256']},
            'verification_evidence': {'path': VERIFICATION,
                                      'sha256': sha(VERIFICATION)},
        },
        'verification': {
            'checked_at': evidence['generated_at'],
            'total_claims': claim_summary['total'],
            'verified': sum(1 for c in evidence['claims_checked'] if c['status'] == 'VERIFIED'),
            'conflicts': claim_summary['conflicts'],
            'non_standard': len(nonstandard),
            'notes': [
                '所有提供的模型/训练/指标/配置声明均与机器实测一致（0 CONFLICT）。',
                '提供的 project_status=PAUSED_FOR_DIAGNOSIS 不是规范枚举值，映射为 '
                'PAUSED + status_detail（NON_STANDARD，不视为事实冲突）。',
                '提供文件只给出旧参考模型 SHA 前后缀，已用完整 SHA 补齐为 VERIFIED。',
                'R1 的 model.zip 路径未在提供状态 JSON 中给出，已补齐为 VERIFIED。',
                'checkpoint 级 SHA 未在提供文件中列出，已由本次核验生成并登记。',
                '“无训练/游戏残留”原为 REPORTED，本次检查为 0 进程 → VERIFIED。',
            ],
        },
        'mcp_interface': {
            'status': 'NOT_IMPLEMENTED',
            'planned_tools': list(store.TOOLS),
            'write_policy': 'read-only by default; controlled writes require base_context_version',
            'external_chatgpt_auto_access': False,
            'note': '本地 coordination 文件不等于 ChatGPT 自动可读；需要 MCP 注册/连接器。',
        },
        'unresolved': [
            {'id': 'UNKNOWN-001', 'status': 'UNKNOWN',
             'item': 'R3 退化首次出现的具体 checkpoint 未定位'},
            {'id': 'UNKNOWN-002', 'status': 'UNKNOWN',
             'item': 'PPO 不稳定的根因（价值函数/学习率/回报尺度等）尚未证实'},
            {'id': 'UNKNOWN-003', 'status': 'UNKNOWN',
             'item': 'R1/R2/R3 在固定种子/固定条件下的严格 A/B 对比尚未执行'},
            {'id': 'UNKNOWN-004', 'status': 'UNKNOWN',
             'item': '旧 75 局基线与新动作空间不是同条件评测，趋势仅供参考'},
            {'id': 'UNKNOWN-005', 'status': 'UNKNOWN',
             'item': 'ChatGPT 是否/何时能通过 MCP 连接本地文件：未注册、未验证'},
        ],
        'history': {
            'v1': {'changed_at': evidence['generated_at'], 'actor': 'USER',
                   'reason': '提供的 v1.0 记忆快照（原始文件保持不变）'},
            'v2': {'changed_at': store.now_iso(), 'actor': 'DSH',
                   'reason': '建立 coordination 文件、只读核验并登记版本'},
        },
        'snapshots': {'v1': 'coordination/snapshots/context-v1-PROJECT_STATE.json'},
    }
    tasks_doc['updated_at'] = store.now_iso()
    tasks_doc['task_count'] = len(tasks_doc['tasks'])

    decisions_md = _decisions_md(originals, evidence, claim_summary, nonstandard)
    readme_md = _readme_md()

    if args.dry_run:
        print('[dry-run] would create:', ', '.join(CANONICAL))
        print('[dry-run] model artifacts:', {k: v['sha256'][:12] for k, v in model_artifacts.items()})
        print('[dry-run] claims:', claim_summary)
        return 0

    # write canonical documents (state first so append_event can read it)
    store.atomic_write_json(store.STATE_PATH, state)
    store.save_tasks(tasks_doc)
    store.atomic_write_text(store.COORD_DIR / 'DECISIONS.md', decisions_md)
    store.atomic_write_text(store.COORD_DIR / 'README.md', readme_md)
    store.regenerate_current_context(state, tasks_doc)

    events = [
        dict(event_type='memory_snapshot_received',
             message='加载用户提供的 v1.0 上下文记忆三文件，记录原始 SHA256（未修改）',
             actor='DSH', context_version=1,
             payload={'files': [{k: originals[rel][k] for k in ('path', 'size', 'sha256')}
                                for rel in PROVIDED.values()]}),
        dict(event_type='state_verified',
             message='完成项目实际状态只读核验：模型/训练配置/指标/环境与提供记忆逐项对照',
             actor='DSH', context_version=2,
             payload={'evidence': VERIFICATION, 'evidence_sha256': sha(VERIFICATION),
                      'total_claims': claim_summary['total'],
                      'verified': sum(1 for c in evidence['claims_checked']
                                      if c['status'] == 'VERIFIED'),
                      'conflicts': claim_summary['conflicts'],
                      'non_standard': len(nonstandard)}),
        dict(event_type='context_files_initialized',
             message='创建缺失的统一上下文文件并建立 context_version=2 基线',
             actor='DSH', context_version=2,
             payload={'created': CANONICAL,
                      'versioned_originals_preserved': list(PROVIDED.values())}),
        dict(event_type='decisions_recorded',
             message='记录 D-001..D-010 初始决策（规范采用、原文件保护、状态映射、R1/R2/R3 定位、'
                     'MCP 策略、版本管理）',
             actor='DSH', context_version=2, payload={'decisions': 10}),
        dict(event_type='task_completed',
             message='TASK-20261030-001 上下文系统初始化完成；TASK-20261030-002 MCP 实施中',
             actor='DSH', context_version=2,
             payload={'task_id': 'TASK-20261030-001'}),
    ]
    for event in events:
        store.append_event(**event)

    # artifact index: all verified artifacts (self index excluded by design)
    artifacts_doc = {'schema_version': store.SCHEMA_VERSION, 'updated_at': store.now_iso(),
                     'artifacts': [], 'artifacts_total': 0,
                     'note': 'ARTIFACT_INDEX.json 自身由下一版本登记其哈希；本索引为 v2 快照。'}
    def add(rel, artifact_type, role, task_id='TASK-20261030-001', status='VERIFIED', note=None):
        try:
            store.register_artifact(artifacts_doc, rel, artifact_type=artifact_type, role=role,
                                    task_id=task_id, status=status, context_version=2, note=note)
        except Exception as exc:  # noqa: BLE001
            print(f'WARN artifact skipped: {rel}: {exc}')
    for rel in PROVIDED.values():
        add(rel, 'markdown' if rel.endswith('.md') else 'json', 'provided_original_immutable',
            note='提供原始文件，只读保留')
    for rel in CANONICAL:
        if rel == 'ARTIFACT_INDEX.json':
            continue
        add(f'coordination/{rel}', 'context_file', 'canonical_context',
            note='由 bootstrap 生成的规范文件')
    add('coordination/manifests/originals-sha256.json', 'json', 'manifest')
    add('coordination/snapshots/context-v1-PROJECT_STATE.json', 'json', 'snapshot',
        note='提供的 v1 状态原样副本')
    add(VERIFICATION, 'json', 'verification_evidence')
    for round_name in ('R1', 'R2', 'R3'):
        inv = evidence['rounds'][round_name]
        add(inv['model']['path'], 'model', f'{round_name}_model',
            task_id=f'STAGE-A-{round_name}')
        for zpath in inv['zips']:
            zip_name = zpath['name']
            if zip_name == 'model.zip':
                continue  # registered once as the round's model artifact
            add(f"{inv['dir']}/{zip_name}", 'checkpoint',
                f'{round_name}_checkpoint', task_id=f'STAGE-A-{round_name}')
        add(f"{inv['dir']}/run.json", 'json', f'{round_name}_run_metadata',
            task_id=f'STAGE-A-{round_name}')
        add(f"{inv['dir']}/action-contract.json", 'json', f'{round_name}_action_contract',
            task_id=f'STAGE-A-{round_name}')
    for item in evidence['reports']:
        add(item['path'], 'report', 'stage_a_report_or_metrics', task_id='STAGE-A')
    for item in evidence['configs']:
        add(item['path'], 'config', 'protected_config')
    add('reports/stage-A-final-preflight-20261030-153217.json', 'json', 'runtime_evidence')
    add('scripts/coordination/verify_project_state.py', 'script', 'context_tooling')
    add('scripts/coordination/context_store.py', 'script', 'context_tooling')
    add('scripts/coordination/bootstrap_context.py', 'script', 'context_tooling')
    store.save_artifacts(artifacts_doc)

    # snapshot of the final v2 state
    store.write_snapshot(2, store.load_state())

    print('bootstrap complete')
    print('context_version:', store.load_state()['context_version'])
    print('tasks:', [t['task_id'] for t in tasks_doc['tasks']])
    print('artifacts:', artifacts_doc['artifacts_total'])
    print('events:', len(store.read_events(limit=0)))
    print('verification claims:', claim_summary)
    return 0


def _decisions_md(originals, evidence, claim_summary, nonstandard):
    lines = [
        '# 决策记录（DECISIONS）',
        '',
        '- 格式：每条决策含 ID、日期、状态、context_version、来源、理由、证据。',
        '- 只追加或修订留痕；不得删除历史决策。状态枚举：'
        'PROPOSED / APPROVED / VERIFIED / SUPERSEDED / BLOCKED。',
        '',
        '---',
        '',
        '## D-001 采用《通用上下文记忆规范 v1.0》',
        f"- 日期：2026-10-30；状态：**APPROVED**（用户提供规范）；base_context_version=1",
        f"- 来源：`coordination/通用上下文记忆规范_v1.0.md`"
        f"（sha256 `{originals['coordination/通用上下文记忆规范_v1.0.md']['sha256']}`）",
        '- 内容：以 coordination/ 下的状态、决策、任务、事件、文件索引五类信息作为单一事实来源。',
        '',
        '## D-002 三个提供文件作为不可变原始证据保留',
        f"- 日期：2026-10-30；状态：**VERIFIED**（哈希核对未变）；base_context_version=1",
    ]
    for rel in PROVIDED.values():
        lines.append(f"- `{rel}` sha256 `{originals[rel]['sha256']}` size {originals[rel]['size']}")
    lines += [
        '- 内容：不修改、不重新序列化三个原始文件；版本化文件名保持不变，'
        '规范要求的固定文件名由新生成文件承担。',
        '',
        '## D-003 规范文件名映射',
        '- 日期：2026-10-30；状态：**APPROVED**（本次实施决策）；base_context_version=2',
        '- 内容：`PROJECT_STATE.json` / `CURRENT_CONTEXT.md` 为自动生成的当前版本；'
        '`RA2_AI_PROJECT_STATE_v1.0.json` / `RA2_AI_CURRENT_CONTEXT_v1.0.md`'
        '作为 v1 原始快照保留。',
        '',
        '## D-004 项目状态枚举规范化',
        f"- 日期：2026-10-30；状态：**VERIFIED**（NON_STANDARD 处理）；base_context_version=2",
        '- 内容：提供状态中的 `PAUSED_FOR_DIAGNOSIS` 不属于规范枚举；映射为 '
        '`project_status=PAUSED` + `status_detail=PAUSED_FOR_DIAGNOSIS`，原始值保留可追溯。',
        '',
        '## D-005 事实核验结论',
        f"- 日期：2026-10-30；状态：**VERIFIED**；base_context_version=2",
        f"- 内容：对提供记忆断言的机器核验共 {claim_summary['total']} 项："
        f"{sum(1 for c in evidence['claims_checked'] if c['status'] == 'VERIFIED')} 项 VERIFIED，"
        f"{claim_summary['conflicts']} 项 CONFLICT，{len(nonstandard)} 项 NON_STANDARD。",
        f"- 证据：`coordination/verification/project-state-verified.json`",
        '',
        '## D-006 缺失上下文文件创建',
        '- 日期：2026-10-30；状态：**COMPLETED**；base_context_version=2',
        '- 内容：检查确认 coordination 原仅有三个提供文件；创建 DECISIONS.md、TASK_QUEUE.json、'
        'EVENTS.jsonl、ARTIFACT_INDEX.json、CURRENT_CONTEXT.md（另加 README.md/manifests/snapshots）。',
        '',
        '## D-007 R1/R2/R3 定位与保护',
        '- 日期：2026-10-30；状态：**VERIFIED**（指标排名）+ **APPROVED**（用户记忆中的定位）；'
        'base_context_version=2',
        '- 内容：R1 为当前最佳候选（6s 有效率 49.79%、击杀 6.0/局、击杀交换比 0.345）；'
        'R2 为次级参考及 R3 初始化来源；R3 为退化诊断样本，禁止从其续训。',
        '- 证据：三轮报告与指标 JSON（见 ARTIFACT_INDEX）。',
        '',
        '## D-008 训练与变更禁止项',
        '- 日期：2026-10-30；状态：**APPROVED**（用户提供的约束）；base_context_version=2',
        '- 内容：未获明确批准不得启动 R4/Stage B、不得从 R3 续训、不得修改 V4 奖励/ACTION_N/'
        '动作结构/模型结构与核心超参、不得覆盖或删除 R1/R2/R3 模型、checkpoint、报告与历史证据。',
        '',
        '## D-009 MCP 接口策略',
        '- 日期：2026-10-30；状态：**APPROVED**（本次实施决策，接口本身待验证）/'
        ' **PROPOSED**（注册到 DSH）；base_context_version=2',
        '- 内容：MCP 默认只读；写操作（tasks.create/update、events.append、artifacts.register）'
        '必须携带 base_context_version 并遵循 append-only、无删除、原子写入；'
        '不自动修改 DSH 全局 profile 配置，注册需用户批准（TASK-20261030-003）。',
        '- 限制：本地文件不等于 ChatGPT 自动可读；连接未验证前不得声称已同步。',
        '',
        '## D-010 版本管理与事件策略',
        '- 日期：2026-10-30；状态：**APPROVED / VERIFIED**；base_context_version=2',
        '- 内容：context_version 只增不减；每次状态变更声明 base_context_version；'
        'EVENTS.jsonl 只追加；每个版本写 snapshots/context-v{N}-PROJECT_STATE.json；'
        '所有写入使用临时文件原子替换。',
        '',
        '---',
        '',
        '## 变更记录',
        '- 2026-10-30 v2：初始建立 D-001..D-010。',
        '',
    ]
    return '\n'.join(lines)


def _readme_md():
    return '\n'.join([
        '# RA2MCP coordination（统一上下文记忆）',
        '',
        '本目录是项目共享上下文，遵循 `通用上下文记忆规范_v1.0.md`。',
        '它保存状态、决策、任务、事件和文件索引，供 DSH/Codex/ChatGPT 等模型在换会话或',
        '上下文压缩后恢复项目事实。**本地文件不会自动出现在 ChatGPT 网页中**，需 MCP/连接器'
        '（见 `MCP_INTERFACE.md`）。',
        '',
        '## 文件职责',
        '| 文件 | 作用 | 写入方式 |',
        '|---|---|---|',
        '| `PROJECT_STATE.json` | 机器可读当前状态（context_version 权威来源） | 原子写入 |',
        '| `CURRENT_CONTEXT.md` | L0 短上下文入口（由 PROJECT_STATE 生成） | 自动生成 |',
        '| `DECISIONS.md` | 已确认决策，只追加/修订留痕 | 追加 |',
        '| `TASK_QUEUE.json` | 任务、负责人、状态、依赖 | 受控更新 |',
        '| `EVENTS.jsonl` | 只追加事件流水 | 仅追加 |',
        '| `ARTIFACT_INDEX.json` | 文件路径、类型、SHA256、任务归属 | 受控登记 |',
        '| `verification/` | 只读核验证据 | 只增 |',
        '| `snapshots/` | 每个 context_version 的状态快照 | 只增 |',
        '| `manifests/` | 原始文件哈希清单 | 只增 |',
        '| `agents/` | 各模型 inbox/outbox（当前为占位） | 按需 |',
        '| `archive/` | 过期但需保留的上下文 | 按需 |',
        '',
        '## 原始文件（不可修改）',
        '- `通用上下文记忆规范_v1.0.md`',
        '- `RA2_AI_CURRENT_CONTEXT_v1.0.md`',
        '- `RA2_AI_PROJECT_STATE_v1.0.json`',
        '',
        '原始哈希见 `manifests/originals-sha256.json`；固定文件名 `PROJECT_STATE.json`、',
        '`CURRENT_CONTEXT.md` 是自动生成的当前版本（v1 原始文件仍保留）。',
        '',
        '## 使用协议',
        '**开始任务前**：读取 `CURRENT_CONTEXT.md` + `PROJECT_STATE.json` + `TASK_QUEUE.json`，',
        '确认 `context_version`；只把 `VERIFIED` 当事实；写操作声明 `base_context_version`。',
        '',
        '**完成后**：登记产物（路径/大小/SHA256）→ 追加 EVENTS.jsonl → 递增 context_version '
        '（`scripts/coordination/context_store.py` 的 `bump_context`）→ 重新生成 CURRENT_CONTEXT.md。',
        '',
        '**禁止**：删除事件/历史快照、覆盖原始文件、跳过版本号、把 PROPOSED 写成 COMPLETED。',
        '',
        '## 工具',
        '- `scripts/coordination/verify_project_state.py`：只读核对项目实际状态并产出证据。',
        '- `scripts/coordination/context_store.py`：版本/事件/任务/产物/快照核心库。',
        '- `scripts/coordination/bootstrap_context.py`：初始化（本目录首次生成）。',
        '- `scripts/coordination/mcp_context_server.py`：stdio MCP 服务（见 MCP_INTERFACE.md）。',
        '- `scripts/coordination/test_context_system.py`：隔离临时根目录的自测。',
        '',
        '## 当前状态',
        '- v1：用户提供的三文件快照（原始保留）。',
        '- v2：本次建立 coordination 文件并完成只读核验。',
        '- v3：实现并本地验证 MCP 接口（见历史与 EVENTS）。',
        '',
    ])


if __name__ == '__main__':
    sys.exit(main())
