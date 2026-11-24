#!/usr/bin/env python3
"""DEPRECATED (2026-11-01)：旧 SYNC.md 单文件通道方案，已被 RFE-1.0 取代。

正式协议（master，永久以此为主）：项目根
    RA2MCP_记忆信息工程与文件交换约定_V1.0_优化草案.md
现行实现：scripts/coordination/exchange.py + coordination/exchange/FILE_GUIDE.md
本文件仅保留作历史存档，不得再作为执行依据，也不得再生成 coordination/SYNC.md。

---- 以下为废弃说明原文 ----
ChatGPT <-> DSH sync channel: single-file packet, consistency gate, ACK ingester.

Implements coordination/SYNC_CONVENTION_v1.md.

File contract (why single file)
-------------------------------
* 上行（DSH -> ChatGPT）：**只有** coordination/SYNC.md 一个文件。
  它自包含：协议摘要 + 版本/哈希 + 增量 + 硬事实 + 待决队列 + 决策清单 + 回执模板。
* 下行（ChatGPT -> DSH）：**只有一个 JSON 代码块**（不传文件），DSH 落盘为
  coordination/CHATGPT_INBOX.jsonl。
* 其余 coordination/*.json 与 reports/** 都是本地事实源/证据，不随轮发送。

Commands
--------
  build                     生成 coordination/SYNC.md（确定性视图，不递增版本）
  check [--json]            一致性门禁；FAIL 时退出码 1
  ingest --file X [--apply] 解析 ChatGPT 回执（默认 dry-run）
  selftest                  纯函数自检（不改任何文件）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import context_store as store  # noqa: E402

SYNC_PATH = store.COORD_DIR / 'SYNC.md'
INBOX_PATH = store.COORD_DIR / 'CHATGPT_INBOX.jsonl'
CLAIMS_PATH = store.COORD_DIR / 'CLAIMS.jsonl'

PACKET_SCHEMA = 'ra2mcp.sync/1'
ACK_SCHEMA = 'ra2mcp.ack/1'
GENERATOR = 'scripts/coordination/sync_packet.py'
MAX_DELTA = 10
MAX_DECISIONS = 24
REVIEW_OVERDUE_HOURS = 24

VERDICT_MAP = {'APPROVE': 'COMPLETED', 'REVISE': 'PROPOSED', 'REJECT': 'BLOCKED'}
NON_TERMINAL = ('PROPOSED', 'APPROVED', 'READY', 'RUNNING', 'REVIEW', 'BLOCKED')
META_TEMPLATE = '<!-- SYNC_META schema={schema} context_version={version} packet_id={packet_id} sha256={sha} generated_at={generated_at} -->'
META_RE = re.compile(r'<!-- SYNC_META (.*?) -->')


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def parse_iso(ts):
    if not ts:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S'):
        try:
            return _dt.datetime.strptime(str(ts), fmt)
        except ValueError:
            continue
    return None


def age_hours(ts):
    parsed = parse_iso(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=8)))
    now = _dt.datetime.now(_dt.timezone.utc)
    return round((now - parsed.astimezone(_dt.timezone.utc)).total_seconds() / 3600.0, 1)


def task_alias(task):
    """External (ChatGPT) task number in the title, e.g. 'TASK-099 ...' -> 'TASK-099'."""
    match = re.match(r'^(TASK-\d{2,4}[A-Z]?)\b', str(task.get('title') or ''))
    return match.group(1) if match else None


def alias_mismatch(task):
    alias = task_alias(task)
    if not alias:
        return None
    return alias if alias not in str(task.get('task_id') or '') else None


def load_events():
    return store.read_events(limit=0)


def append_jsonl(path: pathlib.Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + '\n')
        fh.flush()


def read_sync_meta(path=None):
    path = pathlib.Path(path or SYNC_PATH)
    if not path.exists():
        return None
    text = path.read_text(encoding='utf-8')
    match = META_RE.search(text)
    if not match:
        return None
    meta = {}
    for token in match.group(1).split():
        if '=' in token:
            key, val = token.split('=', 1)
            meta[key] = val
    return meta


# ---------------------------------------------------------------------------
# payload (deterministic derivation from coordination state)
# ---------------------------------------------------------------------------
def build_payload(state=None, tasks=None, events=None):
    state = state or store.load_state()
    tasks = tasks or store.load_tasks()
    events = events if events is not None else load_events()
    rows = tasks.get('tasks') or []
    version = int(state.get('context_version') or 0)

    sync_state = state.get('sync_channel') or {}
    chatgpt = (state.get('agents') or {}).get('ChatGPT') or {}
    last_acked = int(sync_state.get('last_ack_version')
                     or chatgpt.get('last_synced_version') or 0)

    # --- delta since last ack -------------------------------------------------
    history = state.get('history') or {}
    pending = []
    for key, meta in history.items():
        match = re.match(r'^v(\d+)$', str(key))
        if match and int(match.group(1)) > last_acked:
            pending.append((int(match.group(1)), meta or {}))
    pending.sort(key=lambda item: item[0])
    delta = [{'version': number,
              'changed_at': str((meta or {}).get('changed_at') or '')[:19],
              'actor': (meta or {}).get('actor'),
              'reason': str((meta or {}).get('reason') or '')[:150]}
             for number, meta in pending[-MAX_DELTA:]]

    # --- queues ---------------------------------------------------------------
    def slim(task, with_summary=False):
        item = {'task_id': task.get('task_id'), 'alias': task_alias(task),
                'title': str(task.get('title') or '')[:80],
                'owner': task.get('owner'), 'status': task.get('status')}
        if with_summary:
            item['summary'] = str(task.get('result_summary') or '')[:90]
            item['age_h'] = age_hours(task.get('updated_at') or task.get('created_at'))
            item['alias_mismatch'] = alias_mismatch(task)
        return item

    review = [slim(task, True) for task in rows if task.get('status') == 'REVIEW']
    proposed = [slim(task, True) for task in rows if task.get('status') == 'PROPOSED']
    blocked = [slim(task, True) for task in rows if task.get('status') == 'BLOCKED']
    running = [slim(task, True) for task in rows if task.get('status') in ('RUNNING', 'READY')]
    approved_unexecuted = [slim(task, True) for task in rows
                           if task.get('status') == 'APPROVED'
                           and not task.get('result_summary') and not task.get('updated_at')]
    status_counts = {}
    for task in rows:
        status_counts[task.get('status')] = status_counts.get(task.get('status'), 0) + 1

    # --- decisions requested --------------------------------------------------
    decisions = []
    for item in sorted(review, key=lambda row: -(row.get('age_h') or 0)):
        decisions.append({'decision_id': item['task_id'], 'kind': 'review_verdict',
                          'alias': item['alias'],
                          'question': f"评审并给 verdict：{item['title']}",
                          'needs': 'APPROVE | REVISE | REJECT (+理由)'})
    for item in proposed:
        decisions.append({'decision_id': item['task_id'], 'kind': 'proposal_recommend',
                          'alias': item['alias'],
                          'question': f"建议批准/驳回：{item['title']}",
                          'needs': 'RECOMMEND_APPROVE | RECOMMEND_REJECT | DEFER'})
    for unknown in (state.get('unresolved') or []):
        if unknown.get('status') == 'UNKNOWN':
            decisions.append({'decision_id': unknown.get('id'), 'kind': 'unknown',
                              'alias': None, 'question': str(unknown.get('item') or ''),
                              'needs': 'INFO | DECISION | DEFER'})
    decisions_shown = decisions[:MAX_DECISIONS]

    # --- facts / evidence -----------------------------------------------------
    models = state.get('model_artifacts') or {}
    model_rows = [{'name': name, 'path': meta.get('path'),
                   'sha256_16': str(meta.get('sha256') or '')[:16],
                   'status': meta.get('status')} for name, meta in models.items()]
    artifacts = store.load_artifacts()
    recent = sorted((item for item in artifacts.get('artifacts') or []),
                    key=lambda item: str(item.get('registered_at') or ''), reverse=True)[:5]
    evidence = [{'path': item.get('path'),
                 'sha256_16': str(item.get('sha256') or '')[:16]} for item in recent]

    latest = state.get('latest_verified_result') or {}
    best = state.get('best_candidate_model') or {}

    rules = [
        '只有本文件内容是事实；本文件之外的一律记为 claim，由 DSH 核验后才能用。',
        'ack_packet_version 必须等于本文件 context_version；旧版本回执按 STALE_ACK 处理，不推进状态机。',
        'decisions 只对 REVIEW 任务给 verdict：APPROVE / REVISE / REJECT + 一句理由。',
        'directives 是任务提案：DSH 建 PROPOSED 任务；真实运行/训练/改保护资产仍需用户批准，ChatGPT 不能代替批准。',
        '任何“更好/更高/准确率”必须带 指标定义 + N + 条件 + SHA；裸数字不得进结论。',
        '每轮只回一个 ```json 回执块，不要拆成多轮问答；不要要求 DSH 上传第二个文件。',
    ]

    payload = {
        'schema': PACKET_SCHEMA,
        'generator': GENERATOR,
        'context_version': version,
        'status': state.get('project_status'),
        'status_detail': state.get('status_detail'),
        'stage': state.get('current_stage'),
        'one_line': (f"{state.get('project_status')} / {state.get('status_detail')}"
                     f" / stage={state.get('current_stage')}"
                     f" / 最佳候选 {best.get('round')}"
                     f" / 最新轮 {latest.get('round')}（胜率 {latest.get('wins')}/{latest.get('valid_episodes')}）"),
        'last_acked_version': last_acked,
        'lag_versions': max(0, version - last_acked),
        'delta': delta,
        'delta_total': len(pending),
        'facts': {
            'best_candidate_model': {'round': best.get('round'),
                                     'sha256_16': str(best.get('sha256') or '')[:16],
                                     'basis': best.get('basis')},
            'latest_verified_result': {key: latest.get(key) for key in
                                       ('round', 'valid_episodes', 'wins',
                                        'attack_effective_6s_rate', 'kills_mean',
                                        'unit_kill_exchange')},
            'task_status_counts': status_counts,
            'model_artifacts': model_rows,
        },
        'queues': {'review': review, 'proposed': proposed, 'blocked': blocked,
                   'running': running, 'approved_unexecuted': approved_unexecuted},
        'unresolved': [item for item in (state.get('unresolved') or [])
                       if item.get('status') == 'UNKNOWN'],
        'decisions_requested': decisions_shown,
        'decisions_total': len(decisions),
        'rules': rules,
        'evidence': evidence,
    }
    payload['sha256'] = sha256_text(canonical_json(payload))
    payload['packet_id'] = f"PKT-v{version}-{payload['sha256'][:12]}"
    payload['generated_at'] = store.now_iso()

    # reply template (rendered separately to keep the file small)
    payload['reply_template'] = {
        'schema': ACK_SCHEMA,
        'ack_packet_version': version,
        'ack_packet_sha256': payload['sha256'],
        'reviewer': 'ChatGPT',
        'decisions': [{'task_id': item['decision_id'], 'verdict': '', 'reason': ''}
                      for item in decisions_shown if item['kind'] == 'review_verdict'],
        'directives': [{'alias': 'TASK-NNN', 'title': '', 'objective': '', 'owner': 'DSH',
                        'approval_required': True, 'priority': 'P1', 'deps': []}],
        'claims': [{'text': '', 'expected_evidence_path': ''}],
        'questions_to_dsh': [''],
    }
    return payload


# ---------------------------------------------------------------------------
# rendering (single file)
# ---------------------------------------------------------------------------
def render_reply_template(payload):
    template = payload['reply_template']
    lines = ['{']
    keys = list(template.keys())
    for index, key in enumerate(keys):
        value = template[key]
        comma = ',' if index < len(keys) - 1 else ''
        header = f'  "{key}":'
        if isinstance(value, list) and value and isinstance(value[0], dict) \
                and key in ('decisions', 'directives', 'claims'):
            lines.append(f'{header} [')
            for item_index, item in enumerate(value):
                item_json = json.dumps(item, ensure_ascii=False)
                item_comma = ',' if item_index < len(value) - 1 else ''
                lines.append(f'    {item_json}{item_comma}')
            lines.append(f'  ]{comma}')
        elif key == 'questions_to_dsh':
            lines.append(f'{header} ["{value[0] if value else ""}"]{comma}')
        else:
            lines.append(f'{header} {json.dumps(value, ensure_ascii=False)}{comma}')
    lines.append('}')
    return lines


def render_sync(payload):
    lines = []

    def line(text=''):
        lines.append(text)

    line(META_TEMPLATE.format(schema=payload['schema'],
                              version=payload['context_version'],
                              packet_id=payload['packet_id'],
                              sha=payload['sha256'],
                              generated_at=payload['generated_at']))
    line(f"# RA2MCP 同步文件 · {payload['packet_id']}")
    line()
    line('> **唯一上行文件**：每轮只上传这一个文件给 ChatGPT；下行只回一个 ```json 回执块。')
    line(f"> context_version **{payload['context_version']}** · ChatGPT 上次确认 v{payload['last_acked_version']}"
         f"（落后 **{payload['lag_versions']}** 版） · payload_sha256 `{payload['sha256']}`")
    line(f"> 生成 {payload['generated_at']} · `{payload['generator']}`")
    line(f"> 一句话：{payload['one_line']}")
    line()
    line('## 0. 规则（给 ChatGPT，6 条）')
    for rule in payload['rules']:
        line(f'- {rule}')
    line()
    line('## 1. 自上次确认以来的增量')
    if payload['delta']:
        if payload['delta_total'] > len(payload['delta']):
            line(f"> 共 {payload['delta_total']} 条，这里列最近 {len(payload['delta'])} 条；完整历史在本地 `EVENTS.jsonl`。")
        for item in payload['delta']:
            line(f"- v{item['version']} · {item['changed_at']} · {item['actor']} · {item['reason']}")
    else:
        line('- （无新变更：ChatGPT 已是最新版本）')
    line()
    line('## 2. 当前硬事实（只认这些）')
    facts = payload['facts']
    latest = facts['latest_verified_result']
    best = facts['best_candidate_model']
    line(f"- 最佳候选：{best.get('round')} · sha `{best.get('sha256_16')}…` · 依据：{best.get('basis')}")
    line(f"- 最新一轮：{latest.get('round')} · 有效局 {latest.get('valid_episodes')}"
         f" · 胜 {latest.get('wins')} · 6s 有效攻击率 {latest.get('attack_effective_6s_rate')}"
         f" · 击杀/局 {latest.get('kills_mean')} · 交换比 {latest.get('unit_kill_exchange')}")
    counts = ', '.join(f'{key}={val}' for key, val in sorted(facts['task_status_counts'].items()))
    line(f'- 任务状态计数：{counts}')
    line('- 保护模型（不得覆盖/删除）：')
    for model in facts['model_artifacts']:
        line(f"  - {model['name']} `{model['path']}` sha `{model['sha256_16']}…` ({model['status']})")
    line()
    line('## 3. 待决队列')
    queues = payload['queues']
    line(f"### 3.1 待评审 REVIEW（{len(queues['review'])}）—— 请逐条给 verdict，引用本地 task_id")
    for item in queues['review']:
        summary = f"｜{item['summary']}" if item.get('summary') else ''
        line(f"- `{item['task_id']}`（{item.get('age_h')}h）{item['title']}{summary}")
    if any(item.get('alias_mismatch') for item in queues['review']):
        line('> ⚠️ 部分任务标题含历史外部编号（TASK-0xx），仅作别名；回执一律用上面列表的本地 task_id。')
    line(f"### 3.2 待批准 PROPOSED（{len(queues['proposed'])}）")
    for item in queues['proposed']:
        line(f"- `{item['task_id']}` {item['title']}（owner={item.get('owner')}）")
    line(f"### 3.3 阻塞 BLOCKED（{len(queues['blocked'])}）")
    for item in queues['blocked']:
        line(f"- `{item['task_id']}` {item['title']} → 解阻：{item.get('summary') or '未写明'}")
    if queues['approved_unexecuted']:
        line(f"### 3.4 ⚠️ 异常：APPROVED 未执行（{len(queues['approved_unexecuted'])}）")
        for item in queues['approved_unexecuted']:
            line(f"- `{item['task_id']}` {item['title']}（疑似与 BLOCKED 重复，需裁决）")
    line()
    line('## 4. UNKNOWN（不得当事实使用）')
    for unknown in payload['unresolved']:
        line(f"- `{unknown.get('id')}` {unknown.get('item')}")
    line()
    line('## 5. 本轮请 ChatGPT 给出')
    queue_review = payload['queues']['review']
    line(f"> §3.1 的 {len(queue_review)} 条 REVIEW **逐条**给 verdict（回执模板已列全 task_id，不要漏）。")
    non_review = [item for item in payload['decisions_requested']
                  if item['kind'] != 'review_verdict']
    line(f"> 其它 {payload['decisions_total'] - len(queue_review)} 项中，本包列出 {len(non_review)} 项：")
    for index, item in enumerate(non_review, 1):
        line(f"{index}. `{item['decision_id']}` [{item['kind']}] {item['question']} → {item['needs']}")
    line()
    line('## 6. 回执（复制下面 ```json 块，填好整块贴回用户；不要改 task_id/版本/哈希）')
    line('```json')
    lines.extend(render_reply_template(payload))
    line('```')
    line()
    line('## 7. 证据索引（默认不要 DSH 上传；确需某份原文时，用户单独指定 1 份）')
    for item in payload['evidence'][:4]:
        line(f"- `{item['path']}` sha `{item['sha256_16']}…`")
    line()
    line('## 8. 文件约定（正式）')
    line('- **上行唯一**：`coordination/SYNC.md`（本文件，每轮重新生成，路径不变）。')
    line('- **下行唯一**：ChatGPT 的回执 JSON 块 → 用户粘贴 → DSH 落盘 `coordination/CHATGPT_INBOX.jsonl`。')
    line('- **不上传**：`PROJECT_STATE.json` / `TASK_QUEUE.json` / `EVENTS.jsonl` / `ARTIFACT_INDEX.json` / '
         '`CURRENT_CONTEXT.md` / `reports/**` / `handoff-*`（本地事实源与证据，ChatGPT 不需要齐全量）。')
    line('- **禁止**：多文件上传、手写同步包、把旧 handoff 文档当事实源。')
    line()
    return '\n'.join(lines) + '\n'


def build_sync(state=None, tasks=None, quiet=False):
    payload = build_payload(state=state, tasks=tasks)
    store.atomic_write_text(SYNC_PATH, render_sync(payload))
    if not quiet:
        size = SYNC_PATH.stat().st_size
        print(f"packet_id : {payload['packet_id']}")
        print(f"version   : v{payload['context_version']}")
        print(f"sha256    : {payload['sha256']}")
        print(f"file      : {store.relative(SYNC_PATH)} ({size} bytes)")
    return payload


# ---------------------------------------------------------------------------
# consistency gate
# ---------------------------------------------------------------------------
def run_checks():
    issues = []

    def add(level, code, detail):
        issues.append({'level': level, 'code': code, 'detail': detail})

    state = store.load_state()
    tasks = store.load_tasks()
    events = load_events()
    version = int(state.get('context_version') or 0)

    # 1) version consistency: state == L0 == SYNC.md == last event
    l0_version = None
    if store.CURRENT_CONTEXT_PATH.exists():
        match = re.search(r'context_version[：:]\s*\*\*(\d+)\*\*',
                          store.CURRENT_CONTEXT_PATH.read_text(encoding='utf-8'))
        l0_version = int(match.group(1)) if match else None
    if l0_version != version:
        add('FAIL', 'L0_VERSION', f'CURRENT_CONTEXT.md v{l0_version} != PROJECT_STATE v{version}')

    meta = read_sync_meta()
    if not meta:
        add('FAIL', 'SYNC_MISSING', 'coordination/SYNC.md 不存在或无 META；先运行 build')
    else:
        if int(meta.get('context_version') or -1) != version:
            add('FAIL', 'SYNC_VERSION',
                f"SYNC.md v{meta.get('context_version')} != PROJECT_STATE v{version}")
        expected = build_payload(state=state, tasks=tasks)
        if meta.get('sha256') != expected['sha256']:
            add('FAIL', 'SYNC_SHA', 'SYNC.md 哈希与当前状态重算不一致（手改或状态漂移）')
    last_event_version = int(events[-1].get('context_version') or 0) if events else 0
    if last_event_version != version:
        add('WARN', 'EVENTS_VERSION',
            f'最后一条事件 v{last_event_version} != PROJECT_STATE v{version}（写入中断或事件缺失）')

    # 2) protected models
    for name, model_meta in (state.get('model_artifacts') or {}).items():
        path = store.PROJECT_ROOT / str(model_meta.get('path') or '')
        if not path.is_file():
            add('FAIL', 'PROTECTED_MODEL_MISSING', f'{name}: {model_meta.get("path")}')
            continue
        if store.sha256_file(path) != model_meta.get('sha256'):
            add('FAIL', 'PROTECTED_MODEL_SHA', f'{name}: {model_meta.get("path")} sha 变化')

    # 3) provided sources
    for name, source_meta in (state.get('sources') or {}).items():
        path = store.PROJECT_ROOT / str((source_meta or {}).get('path') or '')
        if not path.is_file():
            add('FAIL', 'SOURCE_MISSING', f'{name}: {(source_meta or {}).get("path")}')
            continue
        if store.sha256_file(path) != (source_meta or {}).get('sha256'):
            add('FAIL', 'SOURCE_SHA', f'{name}: {(source_meta or {}).get("path")} sha 变化')

    # 4) task hygiene
    rows = tasks.get('tasks') or []
    non_terminal = [task for task in rows if task.get('status') in NON_TERMINAL]
    alias_map = {}
    for task in non_terminal:
        alias = task_alias(task)
        if alias:
            alias_map.setdefault(alias, []).append(task.get('task_id'))
    for alias, ids in alias_map.items():
        if len(ids) > 1:
            add('WARN', 'DUPLICATE_ALIAS', f'{alias} 对应多个本地任务: {", ".join(ids)}')
    mismatched = [task for task in non_terminal if alias_mismatch(task)]
    if mismatched:
        add('WARN', 'ALIAS_MISMATCH',
            f'{len(mismatched)}/{len(non_terminal)} 个在途任务“外号≠本地ID”（如 '
            + ', '.join(f"{task['task_id']}→{alias_mismatch(task)}" for task in mismatched[:5]) + '）')

    overdue = []
    for task in rows:
        if task.get('status') != 'REVIEW':
            continue
        hours = age_hours(task.get('updated_at') or task.get('created_at'))
        if hours is not None and hours > REVIEW_OVERDUE_HOURS:
            overdue.append(f"{task.get('task_id')}({hours}h)")
    if overdue:
        add('FAIL', 'REVIEW_OVERDUE', 'REVIEW 超过 24h 未裁决: ' + ', '.join(overdue))

    for task in rows:
        if task.get('status') == 'APPROVED' and not task.get('result_summary') \
                and not task.get('updated_at'):
            add('WARN', 'APPROVED_UNEXECUTED',
                f"{task.get('task_id')} 处于 APPROVED 但无执行记录（疑似与 BLOCKED 任务重复）")

    # 5) sync lag
    sync_state = state.get('sync_channel') or {}
    chatgpt = (state.get('agents') or {}).get('ChatGPT') or {}
    last_acked = int(sync_state.get('last_ack_version')
                     or chatgpt.get('last_synced_version') or 0)
    lag = max(0, version - last_acked)
    if lag > 0:
        add('WARN', 'CHATGPT_LAG', f'ChatGPT 落后 {lag} 个版本（last_ack=v{last_acked}）')

    # 6) handoff snapshots are not a fact source
    for path in glob.glob(str(store.PROJECT_ROOT / 'reports' / 'handoff-*' / '*.md')):
        try:
            text = pathlib.Path(path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        match = re.search(r'context_version\s*[=:]\s*(\d+)', text)
        if match and int(match.group(1)) < version:
            add('WARN', 'HANDOFF_STALE',
                f'{store.relative(path)} 基于 v{match.group(1)}（当前 v{version}）——不是事实源')

    fails = [issue for issue in issues if issue['level'] == 'FAIL']
    warns = [issue for issue in issues if issue['level'] == 'WARN']
    return {'context_version': version, 'verdict': 'FAIL' if fails else 'PASS',
            'fails': fails, 'warns': warns, 'issues': issues}


def print_checks(result):
    print(f"consistency check @ v{result['context_version']} -> {result['verdict']}")
    for issue in result['issues']:
        print(f"  [{issue['level']}] {issue['code']}: {issue['detail']}")
    if result['verdict'] == 'PASS':
        print(f"  PASS（{len(result['warns'])} 条警告）")


# ---------------------------------------------------------------------------
# ACK ingest
# ---------------------------------------------------------------------------
def extract_ack_json(text):
    match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.S)
    if match:
        return json.loads(match.group(1))
    match = re.search(r'(\{.*\})', text, re.S)
    if match:
        return json.loads(match.group(1))
    raise ValueError('回执中没有找到 JSON 对象（需要 ```json 代码块）')


def ingest(path, apply=False):
    text = pathlib.Path(path).read_text(encoding='utf-8-sig')
    reply = extract_ack_json(text)
    if reply.get('schema') not in (None, ACK_SCHEMA):
        raise ValueError(f"schema 不匹配: {reply.get('schema')} != {ACK_SCHEMA}")

    state = store.load_state()
    tasks = store.load_tasks()
    version = int(state.get('context_version') or 0)
    ack_version = int(reply.get('ack_packet_version') or 0)
    ack_sha = str(reply.get('ack_packet_sha256') or '')
    expected_sha = build_payload(state=state, tasks=tasks)['sha256']

    record = {
        'record_id': 'ACK-' + time.strftime('%Y%m%d-%H%M%S'),
        'received_at': store.now_iso(),
        'source': 'ChatGPT (manual relay via user)',
        'ack_packet_version': ack_version,
        'ack_packet_sha256': ack_sha,
        'current_context_version': version,
        'expected_packet_sha256': expected_sha,
        'stale': ack_version != version,
        'sha_match': ack_sha == expected_sha,
        'raw_sha256': sha256_text(text),
        'reply': reply,
    }
    report = {'record_id': record['record_id'], 'ack_packet_version': ack_version,
              'current_context_version': version, 'stale': record['stale'],
              'sha_match': record['sha_match'], 'decisions': [], 'directives': [],
              'claims': [], 'applied': False, 'new_version': None}

    if record['stale']:
        report['error'] = ('STALE_ACK：ack 版本不等于当前版本，只存档、不推进状态机。'
                           '请让 ChatGPT 基于最新同步文件重新回执。')
        if apply:
            append_jsonl(INBOX_PATH, record)
        return report
    if not record['sha_match']:
        report['error'] = ('SHA_MISMATCH：回执里的包哈希与当前同步文件不一致（传输损坏/内容被改/'
                           '或 ChatGPT 基于旧包回答）。只存档，不推进。')
        if apply:
            append_jsonl(INBOX_PATH, record)
        return report

    new_task_records = []
    for decision in reply.get('decisions') or []:
        task_id = str(decision.get('task_id') or '')
        verdict = str(decision.get('verdict') or '').upper()
        reason = str(decision.get('reason') or '')
        entry = {'task_id': task_id, 'verdict': verdict, 'reason': reason, 'result': ''}
        task = store.find_task(tasks, task_id) if task_id else None
        if not task_id:
            entry['result'] = 'MISSING_TASK_ID'
        elif task is None:
            entry['result'] = 'TASK_NOT_FOUND'
        elif verdict not in VERDICT_MAP:
            entry['result'] = 'UNKNOWN_VERDICT'
        elif task.get('status') != 'REVIEW':
            entry['result'] = f"NOT_IN_REVIEW(status={task.get('status')})"
        else:
            store.update_task(
                tasks, task_id, status=VERDICT_MAP[verdict],
                result_summary=(str(task.get('result_summary') or '')
                                + f"\n[ChatGPT verdict {verdict} @v{version}] {reason}").strip())
            entry['result'] = VERDICT_MAP[verdict]
        report['decisions'].append(entry)

    for directive in reply.get('directives') or []:
        title = str(directive.get('title') or '').strip()
        if not title:
            report['directives'].append({'alias': directive.get('alias'),
                                         'result': 'MISSING_TITLE'})
            continue
        alias = directive.get('alias')
        full_title = f"{alias} {title}" if alias and not title.startswith(str(alias)) else title
        duplicated = any(task.get('title') == full_title
                         and task.get('status') in NON_TERMINAL for task in tasks.get('tasks') or [])
        entry = {'alias': alias, 'title': full_title,
                 'result': 'DUPLICATE_SKIPPED' if duplicated else 'CREATED'}
        if not duplicated:
            task = store.create_task(
                tasks, title=full_title, objective=str(directive.get('objective') or ''),
                owner=str(directive.get('owner') or 'DSH'), status='PROPOSED',
                base_context_version=version,
                dependencies=list(directive.get('deps') or []),
                approval_required=bool(directive.get('approval_required')))
            entry['task_id'] = task['task_id']
            new_task_records.append(task)
        report['directives'].append(entry)

    for claim in reply.get('claims') or []:
        claim_text = str(claim.get('text') or '').strip()
        if not claim_text:
            continue
        report['claims'].append({
            'claim_id': 'CLAIM-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(len(report['claims']) + 1),
            'created_at': store.now_iso(),
            'source': f"ChatGPT (via ACK {record['record_id']})",
            'text': claim_text,
            'expected_evidence_path': claim.get('expected_evidence_path'),
            'status': 'UNVERIFIED', 'verified_by': None, 'evidence': None, 'verified_at': None,
        })

    if apply:
        with store.Lock():
            if new_task_records:
                store.save_tasks(tasks)
            append_jsonl(INBOX_PATH, record)
            for claim_record in report['claims']:
                append_jsonl(CLAIMS_PATH, claim_record)

            sync_state = dict(state.get('sync_channel') or {})
            sync_state.update({
                'last_ack_version': version,
                'last_ack_at': store.now_iso(),
                'last_ack_packet_id': f"PKT-v{version}-{expected_sha[:12]}",
                'last_ack_packet_sha256': expected_sha,
                'ack_records': int(sync_state.get('ack_records') or 0) + 1,
            })
            agents = dict(state.get('agents') or {})
            chatgpt = dict(agents.get('ChatGPT') or {})
            chatgpt.update({'last_synced_version': version,
                            'last_acked_at': store.now_iso(),
                            'channel': 'manual_relay (SYNC.md up / ACK json down)'})
            agents['ChatGPT'] = chatgpt
            new_state = store.bump_context(
                state, reason=f"ingest {record['record_id']} (ack v{version})", actor='DSH',
                extra_updates={'sync_channel': sync_state, 'agents': agents})
        store.regenerate_current_context(new_state)
        store.append_event('chatgpt_ack_ingested',
                           f"{record['record_id']}: ack v{version}, "
                           f"{len(report['decisions'])} decisions, "
                           f"{len(report['directives'])} directives, "
                           f"{len(report['claims'])} claims",
                           actor='DSH',
                           payload={'record_id': record['record_id'],
                                    'ack_packet_version': version,
                                    'new_version': new_state['context_version']},
                           context_version=new_state['context_version'])
        for entry in report['decisions']:
            store.append_event('chatgpt_verdict_applied', f"{entry['task_id']} -> {entry['result']}",
                               actor='ChatGPT', payload=entry,
                               context_version=new_state['context_version'])
        report['applied'] = True
        report['new_version'] = new_state['context_version']
        report['new_packet_id'] = build_sync(new_state, quiet=True)['packet_id']
    return report


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
def selftest():
    failures = []

    def check(name, condition):
        if condition:
            print(f'  PASS {name}')
        else:
            failures.append(name)
            print(f'  FAIL {name}')

    check('canonical_json deterministic',
          canonical_json({'b': 1, 'a': [2, 3]}) == canonical_json({'a': [2, 3], 'b': 1}))
    check('sha256_text known vector',
          sha256_text('abc').startswith('ba7816bf8f01cfea414140de5dae2223'))
    check('alias extraction', task_alias({'title': 'TASK-099 x'}) == 'TASK-099')
    check('alias mismatch detected',
          alias_mismatch({'title': 'TASK-099 x', 'task_id': 'TASK-20261101-101'}) == 'TASK-099')
    check('alias match ok',
          alias_mismatch({'title': 'TASK-20261101-101 x', 'task_id': 'TASK-20261101-101'}) is None)
    check('age_hours parses tz', age_hours('2026-11-01T20:00:00+0800') is not None)
    check('ack fenced json', extract_ack_json(
        '前言\n```json\n{"ack_packet_version": 7}\n```\n后记')['ack_packet_version'] == 7)
    check('ack bare json', extract_ack_json('{"ack_packet_version": 8}')['ack_packet_version'] == 8)
    check('verdict map', VERDICT_MAP == {'APPROVE': 'COMPLETED', 'REVISE': 'PROPOSED',
                                         'REJECT': 'BLOCKED'})

    payload = build_payload()
    check('packet_id', str(payload.get('packet_id', '')).startswith('PKT-v'))
    check('payload sha deterministic', payload['sha256'] == build_payload()['sha256'])
    text = render_sync(payload)
    check('single file has meta', read_meta_from_text(text) is not None)
    check('single file has reply template',
          '```json' in text and 'ack_packet_version' in text)
    check('meta sha equals payload sha', read_meta_from_text(text)['sha256'] == payload['sha256'])
    check('meta version equals payload version',
          int(read_meta_from_text(text)['context_version']) == payload['context_version'])
    check('no separate json artifact path referenced', 'SYNC_PACKET.json' not in text)

    result = run_checks()
    print(f"  INFO consistency verdict: {result['verdict']} "
          f"({len(result['fails'])} fails / {len(result['warns'])} warns)")
    for issue in result['fails']:
        print(f"    FAIL {issue['code']}: {issue['detail']}")

    print(f"selftest: {'PASS' if not failures else 'FAIL'} ({len(failures)} failed)")
    return 0 if not failures else 1


def read_meta_from_text(text):
    match = META_RE.search(text)
    if not match:
        return None
    meta = {}
    for token in match.group(1).split():
        if '=' in token:
            key, val = token.split('=', 1)
            meta[key] = val
    return meta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description='ChatGPT<->DSH single-file sync channel')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('build', help='生成 coordination/SYNC.md')
    check_parser = sub.add_parser('check', help='一致性门禁')
    check_parser.add_argument('--json', action='store_true')
    ingest_parser = sub.add_parser('ingest', help='解析 ChatGPT 回执（默认 dry-run）')
    ingest_parser.add_argument('--file', required=True)
    ingest_parser.add_argument('--apply', action='store_true')
    sub.add_parser('selftest', help='纯函数自检')
    args = parser.parse_args(argv)

    if args.command == 'build':
        build_sync()
        return 0
    if args.command == 'check':
        result = run_checks()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_checks(result)
        return 0 if result['verdict'] == 'PASS' else 1
    if args.command == 'selftest':
        return selftest()
    if args.command == 'ingest':
        report = ingest(args.file, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get('error'):
            print(f"ERROR: {report['error']}", file=sys.stderr)
            return 2
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
