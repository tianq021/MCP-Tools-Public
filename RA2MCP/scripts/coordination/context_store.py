"""Core storage for the RA2MCP coordination context system.

Design rules (see coordination/通用上下文记忆规范_v1.0.md):
* single source of truth: PROJECT_STATE.json / TASK_QUEUE.json /
  EVENTS.jsonl / ARTIFACT_INDEX.json / CURRENT_CONTEXT.md
* context_version only increases; every write declares/validates
  base_context_version (optimistic concurrency)
* EVENTS.jsonl is append-only
* JSON writes are atomic (temp file + os.replace)
* no delete API: this module only adds/updates entries and appends events
* path writes are restricted to the coordination directory for context files;
  artifact paths must stay inside the project root
* optional advisory file lock serialises writers across processes

Importers: scripts/coordination/bootstrap_context.py,
scripts/coordination/mcp_context_server.py, tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time
import uuid

PROJECT_ROOT = pathlib.Path(os.environ.get(
    'RA2_CONTEXT_ROOT', pathlib.Path(__file__).resolve().parents[2])).resolve()
COORD_DIR = pathlib.Path(os.environ.get('RA2_COORD_DIR', PROJECT_ROOT / 'coordination')).resolve()
STATE_PATH = COORD_DIR / 'PROJECT_STATE.json'
TASKS_PATH = COORD_DIR / 'TASK_QUEUE.json'
EVENTS_PATH = COORD_DIR / 'EVENTS.jsonl'
ARTIFACTS_PATH = COORD_DIR / 'ARTIFACT_INDEX.json'
CURRENT_CONTEXT_PATH = COORD_DIR / 'CURRENT_CONTEXT.md'
SNAPSHOTS_DIR = COORD_DIR / 'snapshots'
MANIFESTS_DIR = COORD_DIR / 'manifests'
LOCK_PATH = COORD_DIR / '.context.lock'

SCHEMA_VERSION = '1.0'
STATUS_ENUM = ('PROPOSED', 'APPROVED', 'READY', 'RUNNING', 'REVIEW', 'COMPLETED',
               'BLOCKED', 'FAILED')
FACT_ENUM = ('VERIFIED', 'REPORTED', 'PROPOSED', 'APPROVED', 'RUNNING',
             'COMPLETED', 'BLOCKED', 'UNKNOWN', 'SUPERSEDED')

TOOLS = ('context.get', 'context.diff', 'context.search', 'files.search',
         'tasks.create', 'tasks.update', 'events.append', 'artifacts.register')


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------
def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def relative(path):
    path = pathlib.Path(path)
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def inside_project(path):
    path = pathlib.Path(path).resolve()
    try:
        path.relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False


def load_json(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_write_text(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    tmp.write_text(text, encoding='utf-8', newline='\n')
    os.replace(tmp, path)


def atomic_write_json(path, payload):
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


class Lock:
    """Small cross-process advisory lock for context writers (Windows/msvcrt)."""

    def __init__(self, path=LOCK_PATH, timeout=15.0, poll=0.05):
        self.path = pathlib.Path(path)
        self.timeout = timeout
        self.poll = poll
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, 'a+', encoding='utf-8')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'context write lock timeout: {self.path}')
                time.sleep(self.poll)

    def __exit__(self, *exc):
        try:
            if self._fh is not None:
                if os.name == 'nt':
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
        return False


# ---------------------------------------------------------------------------
# context state / queue / index
# ---------------------------------------------------------------------------
def load_state():
    state = load_json(STATE_PATH)
    if not state:
        raise FileNotFoundError(f'missing {STATE_PATH}; run bootstrap_context.py first')
    return state


def save_state(state):
    atomic_write_json(STATE_PATH, state)


def load_tasks():
    doc = load_json(TASKS_PATH, None)
    if doc is None:
        return {'schema_version': SCHEMA_VERSION, 'tasks': []}
    if isinstance(doc, list):  # tolerate a bare list form
        return {'schema_version': SCHEMA_VERSION, 'tasks': doc}
    return doc


def save_tasks(doc):
    doc = dict(doc)
    doc['updated_at'] = now_iso()
    doc['task_count'] = len(doc.get('tasks') or [])
    atomic_write_json(TASKS_PATH, doc)


def load_artifacts():
    doc = load_json(ARTIFACTS_PATH, None)
    if doc is None:
        return {'schema_version': SCHEMA_VERSION, 'artifacts': [], 'artifacts_total': 0}
    return doc


def save_artifacts(doc):
    doc = dict(doc)
    doc['updated_at'] = now_iso()
    doc['artifacts_total'] = len(doc.get('artifacts') or [])
    atomic_write_json(ARTIFACTS_PATH, doc)


def current_context_version():
    return int(load_state().get('context_version') or 0)


def check_base_version(base_context_version):
    current = current_context_version()
    if int(base_context_version) != current:
        raise ContextConflict(
            f'base_context_version {base_context_version} != current {current}; '
            're-read PROJECT_STATE.json and retry')
    return current


class ContextConflict(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# events (append-only)
# ---------------------------------------------------------------------------
def _last_event_number(prefix):
    if not EVENTS_PATH.exists():
        return 0
    last = 0
    with EVENTS_PATH.open('r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = str(event.get('event_id') or '')
            if event_id.startswith(prefix):
                try:
                    last = max(last, int(event_id.rsplit('-', 1)[-1]))
                except ValueError:
                    continue
    return last


def next_event_id(when=None):
    prefix = 'EV-' + (when or time.strftime('%Y%m%d'))
    return f'{prefix}-{_last_event_number(prefix) + 1:04d}'


def append_event(event_type, message, actor='DSH', payload=None, context_version=None,
                 event_id=None):
    if not event_type or not str(event_type).strip():
        raise ValueError('event_type required')
    state = load_state()
    event = {
        'event_id': event_id or next_event_id(),
        'timestamp': now_iso(),
        'event_type': str(event_type),
        'actor': str(actor or 'DSH'),
        'context_version': int(context_version if context_version is not None
                               else state.get('context_version') or 0),
        'message': str(message or ''),
        'payload': payload if payload is not None else {},
    }
    with Lock():
        with EVENTS_PATH.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
    return event


def read_events(limit=100, offset=0):
    if not EVENTS_PATH.exists():
        return []
    events = []
    with EVENTS_PATH.open('r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events[offset:offset + limit] if limit else events[offset:]


# ---------------------------------------------------------------------------
# snapshots / context version bump
# ---------------------------------------------------------------------------
def snapshot_path(version):
    return SNAPSHOTS_DIR / f'context-v{int(version)}-PROJECT_STATE.json'


def write_snapshot(version, state):
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(snapshot_path(version), state)
    return relative(snapshot_path(version))


def bump_context(state, reason, actor='DSH', extra_updates=None):
    """Increment context_version, write state, snapshot the new version."""
    old = int(state.get('context_version') or 0)
    new = old + 1
    state = dict(state)
    state['context_version'] = new
    state['updated_at'] = now_iso()
    history = dict(state.get('history') or {})
    history[f'v{new}'] = {'changed_at': now_iso(), 'actor': actor, 'reason': reason}
    state['history'] = history
    if extra_updates:
        state.update(extra_updates)
    snapshot = write_snapshot(new, state)
    state.setdefault('snapshots', {})[f'v{new}'] = snapshot
    save_state(state)
    return state


def regenerate_current_context(state=None, tasks=None):
    """Render the L0 entry file. Keeps the versioned provided originals untouched."""
    state = state or load_state()
    tasks = tasks or load_tasks()
    best = state.get('best_candidate_model') or {}
    models = state.get('model_artifacts') or {}
    next_actions = state.get('next_actions') or []
    latest = state.get('latest_verified_result') or {}
    lines = [
        '# RA2 AI 项目当前上下文（自动生成的 L0 入口）',
        '',
        f"- context_version：**{state.get('context_version')}**",
        f"- 更新时间：{state.get('updated_at')}",
        f"- 项目状态：`{state.get('project_status')}`"
        f"（detail: `{state.get('status_detail')}`）",
        f"- 当前阶段：**{state.get('current_stage')}**",
        '- 本文件由 `coordination/PROJECT_STATE.json` 生成；原始快照文件'
        ' `RA2_AI_CURRENT_CONTEXT_v1.0.md` / `RA2_AI_PROJECT_STATE_v1.0.json` 保持不变。',
        '',
        '## 项目目标',
        '构建能在红警 2 / 尤里的复仇中自主对战、持续学习、逐步形成战术能力的 AI；'
        '当前聚焦可复现的观测/动作接口、V4 奖励、可靠评测与训练稳定性。',
        '',
        '## 历史 Stage A 记录（不代表当前部署能力）',
        f"- Stage A 已完成 {', '.join(state.get('completed_rounds') or [])}"
        '（各 10 局有效对局，被中断对局不计入）。',
        f"- 最新一轮：{latest.get('round')}；6 秒有效攻击率 "
        f"{latest.get('attack_effective_6s_rate')}，击杀/局 {latest.get('kills_mean')}，"
        f"胜利率 {latest.get('wins')}/{latest.get('valid_episodes')}。",
        f"- 历史训练期候选：**{best.get('round')}** "
        f"`{str(best.get('sha256'))[:16]}…`（仅历史指标排序；不是已验证最强策略）。",
        '- R3 出现攻击刷屏/有效率下降/PPO 不稳定信号，仅作退化诊断样本，禁止从其续训。',
        '',
        '## 保护模型（不得覆盖或删除）',
    ]
    progress = state.get('current_training_progress')
    if progress:
        position = lines.index('## 历史 Stage A 记录（不代表当前部署能力）')
        lines[position:position] = [
            '## 最新接管核验',
            f"- 核验时间：{progress.get('checked_at')}；负责人：{progress.get('owner')}",
            *[f'- {item}' for item in progress.get('summary', [])],
            f"- 证据：`{progress.get('report')}`",
            '- 本节为文件与离线核验，不代表当前有游戏或训练进程运行。',
            '',
        ]
    for name, meta in models.items():
        lines.append(f"- {name}: `{meta.get('path')}` sha256 `{meta.get('sha256')}` "
                     f"({meta.get('status')})")
    lines += [
        '',
        '## 关键约束',
    ]
    for item in state.get('constraints') or []:
        lines.append(f'- {item}')
    lines += ['', '## 当前任务 / 下一步（PROPOSED，未执行）']
    for action in next_actions:
        lines.append(f"- {action.get('id')} [{action.get('status')}] {action.get('title')}"
                     f" (owner={action.get('owner')})")
    active = [t for t in (tasks.get('tasks') or []) if t.get('status') in
              ('RUNNING', 'READY', 'APPROVED')]
    if active:
        lines.append('')
        lines.append('活跃任务：')
        for task in active:
            lines.append(f"- {task.get('task_id')} [{task.get('status')}] {task.get('title')}")
    lines += [
        '',
        '## 如何使用（同步协议）',
        '1. 开始任务前读取本文件、`PROJECT_STATE.json`、`TASK_QUEUE.json`，'
        '确认 `context_version` 与任务所有权。',
        '2. 只把 `VERIFIED` 当事实；`REPORTED/PROPOSED/UNKNOWN` 必须标注来源。',
        '3. 写操作必须带 `base_context_version`；冲突时先读取差异，禁止盲目覆盖。',
        '4. 完成后登记产物（路径+大小+SHA256）、追加 `EVENTS.jsonl`、递增 '
        '`context_version`，并重新生成本文件。',
        '',
        '## 重要限制',
        '- 本地 `coordination/` 文件不会自动出现在 ChatGPT 网页上下文中。',
        '- MCP 接口状态见 `coordination/MCP_INTERFACE.md`；在完成连接与注册前，'
        '不得声称 ChatGPT 已能自动读取本地文件。',
        '- 完成证据以 `ARTIFACT_INDEX.json` 与 `verification/` 为准。',
        '',
    ]
    atomic_write_text(CURRENT_CONTEXT_PATH, '\n'.join(lines))
    return relative(CURRENT_CONTEXT_PATH)


# ---------------------------------------------------------------------------
# tasks helpers
# ---------------------------------------------------------------------------
def find_task(tasks_doc, task_id):
    for task in tasks_doc.get('tasks') or []:
        if task.get('task_id') == task_id:
            return task
    return None


def next_task_id(tasks_doc, prefix_date=None):
    date = prefix_date or time.strftime('%Y%m%d')
    base = f'TASK-{date}-'
    numbers = []
    for task in tasks_doc.get('tasks') or []:
        task_id = str(task.get('task_id') or '')
        if task_id.startswith(base):
            try:
                numbers.append(int(task_id[len(base):]))
            except ValueError:
                continue
    return f'{base}{(max(numbers) + 1) if numbers else 1:03d}'


def create_task(tasks_doc, title, objective, owner='DSH', status='PROPOSED',
                base_context_version=None, scope=None, constraints=None,
                acceptance_criteria=None, dependencies=None, outputs=None,
                approval_required=False, task_id=None):
    if status not in STATUS_ENUM:
        raise ValueError(f'invalid status {status!r}; expected one of {STATUS_ENUM}')
    task = {
        'task_id': task_id or next_task_id(tasks_doc),
        'title': str(title),
        'status': status,
        'owner': owner,
        'created_at': now_iso(),
        'base_context_version': int(base_context_version or 0),
        'objective': str(objective),
        'scope': list(scope or []),
        'constraints': list(constraints or []),
        'acceptance_criteria': list(acceptance_criteria or []),
        'dependencies': list(dependencies or []),
        'outputs': list(outputs or []),
        'approval_required': bool(approval_required),
        'result_summary': None,
        'completed_at': None,
    }
    tasks_doc.setdefault('tasks', []).append(task)
    return task


def update_task(tasks_doc, task_id, status=None, result_summary=None, outputs=None,
                owner=None):
    task = find_task(tasks_doc, task_id)
    if task is None:
        raise KeyError(f'unknown task_id {task_id}')
    if status is not None:
        if status not in STATUS_ENUM:
            raise ValueError(f'invalid status {status!r}')
        task['status'] = status
        if status == 'COMPLETED':
            task['completed_at'] = now_iso()
    if result_summary is not None:
        task['result_summary'] = str(result_summary)
    if outputs is not None:
        merged = list(task.get('outputs') or [])
        for item in outputs:
            if item not in merged:
                merged.append(item)
        task['outputs'] = merged
    if owner is not None:
        task['owner'] = owner
    task['updated_at'] = now_iso()
    return task


# ---------------------------------------------------------------------------
# artifact helpers
# ---------------------------------------------------------------------------
def register_artifact(artifacts_doc, path, artifact_type='file', role=None,
                      task_id=None, status='VERIFIED', context_version=None,
                      note=None, register_self=False):
    target = pathlib.Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target = target.resolve()
    if not inside_project(target):
        raise ValueError('artifact path must be inside the project root')
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(str(target))
    rel = relative(target)
    entry = {
        'path': rel,
        'type': artifact_type,
        'role': role,
        'size': target.stat().st_size,
        'sha256': sha256_file(target),
        'task_id': task_id,
        'status': status,
        'context_version': int(context_version or 0),
        'registered_at': now_iso(),
        'note': note,
    }
    existing = next((a for a in artifacts_doc.get('artifacts') or []
                     if a.get('path') == rel), None)
    if existing is not None:
        previous = {k: existing.get(k) for k in ('sha256', 'size', 'status')}
        existing.update(entry)
        existing['previous'] = previous
    else:
        artifacts_doc.setdefault('artifacts', []).append(entry)
    return entry


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def validate_state(state):
    problems = []
    if state.get('schema_version') != SCHEMA_VERSION:
        problems.append(f"schema_version != {SCHEMA_VERSION}")
    if not isinstance(state.get('context_version'), int) or state['context_version'] < 1:
        problems.append('context_version must be a positive integer')
    if state.get('project_status') not in ('RUNNING', 'PAUSED', 'BLOCKED', 'COMPLETED'):
        problems.append('project_status not in schema enum')
    if state.get('agents') and 'ChatGPT' in state['agents']:
        cg = state['agents']['ChatGPT']
        if cg.get('status') not in ('UNKNOWN', 'LOCAL_FILE_ACCESS_VERIFIED',
                                    'NOT_CONNECTED_TO_LOCAL_FILES_BY_DEFAULT'):
            problems.append('unexpected ChatGPT access status')
    return problems


def write_state_checked(state, event=None):
    problems = validate_state(state)
    if problems:
        raise ValueError('; '.join(problems))
    save_state(state)
    if event:
        append_event(**event)
    return state
