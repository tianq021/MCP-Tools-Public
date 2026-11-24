"""Local stdio MCP server for the RA2MCP coordination context.

Exposes eight tools (read-only first, controlled writes require
base_context_version):

  context.get, context.diff, context.search, files.search,
  tasks.create, tasks.update, events.append, artifacts.register

Run (stdio transport, e.g. from a DSH profile entry):
    <python> E:/RA2MCP/scripts/coordination/mcp_context_server.py

Safety rules:
* reads never modify files;
* writes require base_context_version == current context_version,
  use atomic JSON writes, append-only EVENTS.jsonl, no delete operations;
* artifact paths must stay inside the project root;
* no network, no training, no model/reward/action changes.

The server is NOT auto-registered in DSH by this script; registration is a
separate, user-approved step (see coordination/MCP_INTERFACE.md).
"""
from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import context_store as store  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover - import error surfaces clearly
    raise SystemExit(f'python "mcp" package 1.x required: {exc!r}')

mcp = FastMCP(
    'ra2-context',
    instructions=(
        'RA2MCP project context memory. Read CURRENT_CONTEXT.md via context.get before '
        'working. Only VERIFIED facts are authoritative. Writes require '
        'base_context_version and never delete history. This server only touches '
        'coordination/ files and registers artifact hashes; it does not start games, '
        'training, or modify models/rewards/actions.'
    ),
)

READ = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True,
        'openWorldHint': False}
WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False,
         'openWorldHint': False}

EXCLUDED_DIR_NAMES = {
    '.git', '.venv', '.venv-rl', 'node_modules', '__pycache__', 'vendor', 'build',
    'downloads', '.tmp', 'backups',
}
EXCLUDED_PATH_PARTS = ('vendor/', 'node_modules/', '.venv', 'build/', 'downloads/',
                       'test-game', 'reports/rl/', 'backups/')


def _ok(**data):
    data.setdefault('ok', True)
    return data


def _err(error, **data):
    data.update({'ok': False, 'error': str(error)})
    return data


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------
@mcp.tool(name='context.get', description='读取当前上下文：状态、任务、事件尾部、'
          'MCP 状态、快照版本、原始文件哈希。只读。', annotations=READ)
def context_get(include_current_context: bool = True, tasks_limit: int = 50,
                events_limit: int = 10, include_state: bool = True) -> dict:
    try:
        state = store.load_state()
        tasks = store.load_tasks()
        artifacts = store.load_artifacts()
        events = store.read_events(limit=max(0, min(events_limit, 200)))
        versions = sorted(
            int(p.stem.split('-')[1].lstrip('v'))
            for p in store.SNAPSHOTS_DIR.glob('context-v*-PROJECT_STATE.json')
            if p.stem.split('-')[1].lstrip('v').isdigit())
        manifest = store.load_json(store.MANIFESTS_DIR / 'originals-sha256.json', {})
        current_md = ''
        if include_current_context and store.CURRENT_CONTEXT_PATH.exists():
            current_md = store.CURRENT_CONTEXT_PATH.read_text(encoding='utf-8')
        payload = _ok(
            context_version=state.get('context_version'),
            project_status=state.get('project_status'),
            status_detail=state.get('status_detail'),
            current_stage=state.get('current_stage'),
            updated_at=state.get('updated_at'),
            best_candidate_model=state.get('best_candidate_model'),
            model_artifacts=state.get('model_artifacts'),
        )
        if include_state:
            payload['state'] = state
        payload.update(
            tasks_total=len(tasks.get('tasks') or []),
            tasks_summary=[{'task_id': t.get('task_id'), 'title': t.get('title'),
                            'status': t.get('status'), 'owner': t.get('owner')}
                           for t in (tasks.get('tasks') or [])[:max(1, tasks_limit)]],
            artifacts_total=len(artifacts.get('artifacts') or []),
            events_tail=events,
            available_snapshot_versions=versions,
            originals=manifest.get('files'),
            mcp_interface=state.get('mcp_interface'),
        )
        if include_current_context:
            payload['current_context_md'] = current_md
        return payload
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _flat_diff(left, right, prefix='', limit=200):
    diffs = []
    if len(diffs) >= limit:
        return diffs
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if len(diffs) >= limit:
                break
            diffs.extend(_flat_diff(left.get(key), right.get(key),
                                    f'{prefix}.{key}' if prefix else str(key), limit))
    elif isinstance(left, list) and isinstance(right, list):
        if left != right:
            diffs.append({'path': prefix, 'from': left if len(str(left)) < 800
                          else f'<list len={len(left)}>',
                          'to': right if len(str(right)) < 800
                          else f'<list len={len(right)}>'})
    elif left != right:
        diffs.append({'path': prefix, 'from': left, 'to': right})
    return diffs


@mcp.tool(name='context.diff', description='比较两个 context_version 的状态快照差异。'
          '缺少快照时返回 UNKNOWN 而不猜测。只读。', annotations=READ)
def context_diff(from_version: int, to_version: int | None = None) -> dict:
    try:
        paths = {int(p.stem.split('-')[1].lstrip('v')): p
                 for p in store.SNAPSHOTS_DIR.glob('context-v*-PROJECT_STATE.json')
                 if p.stem.split('-')[1].lstrip('v').isdigit()}
        if from_version not in paths:
            return _err(f'UNKNOWN: snapshot for version {from_version} not found',
                        available_versions=sorted(paths))
        if to_version is None:
            to_version = max(paths)
        if to_version not in paths:
            return _err(f'UNKNOWN: snapshot for version {to_version} not found',
                        available_versions=sorted(paths))
        left = store.load_json(paths[from_version], {})
        right = store.load_json(paths[to_version], {})
        diffs = _flat_diff(left, right, limit=300)
        return _ok(from_version=from_version, to_version=to_version,
                   changed=bool(diffs), diff_count=len(diffs), diff=diffs)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _iter_context_files():
    for pattern in ('*.md', '*.json', '*.jsonl', '*.yml', '*.yaml'):
        for path in store.COORD_DIR.rglob(pattern):
            if any(part in {'.git', '__pycache__'} for part in path.parts):
                continue
            if path.name.endswith('.lock') or path.stat().st_size > 2 * 1024 * 1024:
                continue
            yield path


@mcp.tool(name='context.search', description='在 coordination/ 文本文件中搜索关键字'
          '（默认字面量、忽略大小写，可选正则），返回文件/行号/片段。只读。',
          annotations=READ)
def context_search(query: str, limit: int = 20, regex: bool = False,
                   include_glob: str | None = None) -> dict:
    try:
        if not query:
            return _err('query required')
        limit = max(1, min(int(limit), 100))
        matcher = re.compile(query, re.IGNORECASE) if regex else None
        needle = query.lower()
        results = []
        for path in sorted(_iter_context_files()):
            if include_glob and not fnmatch.fnmatch(path.name, include_glob):
                continue
            try:
                lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
            except Exception:  # noqa: BLE001
                continue
            for number, line in enumerate(lines, 1):
                hit = bool(matcher.search(line)) if matcher else needle in line.lower()
                if hit:
                    results.append({'path': store.relative(path), 'line': number,
                                    'text': line.strip()[:300]})
                    if len(results) >= limit:
                        break
            if len(results) >= limit:
                break
        return _ok(query=query, matches=len(results), results=results)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _excluded(rel_parts):
    if any(part in EXCLUDED_DIR_NAMES for part in rel_parts):
        return True
    joined = '/'.join(rel_parts)
    return any(token in joined for token in EXCLUDED_PATH_PARTS)


@mcp.tool(name='files.search', description='只读搜索项目文件路径/文件名（默认排除 '
          '.venv/vendor/build/test-game*/reports/rl 等大目录），返回相对路径、大小、'
          '修改时间。', annotations=READ)
def files_search(query: str, limit: int = 50, extensions: list[str] | None = None) -> dict:
    try:
        if not query:
            return _err('query required')
        limit = max(1, min(int(limit), 200))
        exts = {e.lower().lstrip('.') for e in (extensions or [])}
        needle = query.lower()
        results = []
        scan_cap = 400000
        scanned = 0
        for current, dirs, files in os.walk(store.PROJECT_ROOT):
            rel_dir = pathlib.Path(current).relative_to(store.PROJECT_ROOT)
            dirs[:] = sorted(d for d in dirs if not _excluded(list(rel_dir.parts) + [d]))
            for name in sorted(files):
                scanned += 1
                if scanned > scan_cap:
                    break
                if exts and name.rsplit('.', 1)[-1].lower() not in exts:
                    continue
                rel = (rel_dir / name).as_posix()
                if needle not in rel.lower():
                    continue
                path = pathlib.Path(current) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                results.append({'path': rel, 'size': stat.st_size,
                                'modified_at': time.strftime(
                                    '%Y-%m-%dT%H:%M:%S',
                                    time.localtime(stat.st_mtime))})
                if len(results) >= limit:
                    break
            if len(results) >= limit or scanned > scan_cap:
                break
        return _ok(query=query, matches=len(results), truncated=scanned > scan_cap,
                   results=results)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------------
# controlled writes
# ---------------------------------------------------------------------------
def _begin_write(base_context_version, actor, reason):
    store.check_base_version(base_context_version)
    return store.load_state()


def _commit(state, reason, actor, extra_updates=None):
    with store.Lock():
        new_state = store.bump_context(state, reason=reason, actor=actor,
                                       extra_updates=extra_updates)
    store.regenerate_current_context(new_state)
    return new_state


@mcp.tool(name='tasks.create', description='创建任务（PROPOSED 等），需要 base_context_version；'
          '追加事件并递增 context_version，不删除历史。', annotations=WRITE)
def tasks_create(title: str, objective: str, base_context_version: int,
                 owner: str = 'DSH', status: str = 'PROPOSED',
                 scope: list[str] | None = None,
                 constraints: list[str] | None = None,
                 acceptance_criteria: list[str] | None = None,
                 dependencies: list[str] | None = None,
                 approval_required: bool = False,
                 actor: str = 'DSH') -> dict:
    try:
        state = _begin_write(base_context_version, actor, 'tasks.create')
        tasks = store.load_tasks()
        task = store.create_task(tasks, title=title, objective=objective, owner=owner,
                                 status=status, base_context_version=base_context_version,
                                 scope=scope, constraints=constraints,
                                 acceptance_criteria=acceptance_criteria,
                                 dependencies=dependencies,
                                 approval_required=approval_required)
        store.save_tasks(tasks)
        new_state = _commit(state, f"tasks.create {task['task_id']}", actor,
                            {'active_tasks': (state.get('active_tasks') or [])
                             if status not in ('COMPLETED', 'FAILED')
                             else state.get('active_tasks') or []})
        event = store.append_event('task_created', f"created {task['task_id']}: {title}",
                                   actor=actor, payload={'task_id': task['task_id']},
                                   context_version=new_state['context_version'])
        return _ok(task=task, context_version=new_state['context_version'],
                   event_id=event['event_id'])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool(name='tasks.update', description='更新任务状态/结果/产物（无删除），需要 '
          'base_context_version；追加事件并递增 context_version。', annotations=WRITE)
def tasks_update(task_id: str, base_context_version: int, status: str | None = None,
                 result_summary: str | None = None,
                 outputs: list[str] | None = None, owner: str | None = None,
                 actor: str = 'DSH') -> dict:
    try:
        state = _begin_write(base_context_version, actor, 'tasks.update')
        tasks = store.load_tasks()
        task = store.update_task(tasks, task_id, status=status,
                                 result_summary=result_summary, outputs=outputs,
                                 owner=owner)
        store.save_tasks(tasks)
        active_extra = None
        new_state = _commit(state, f"tasks.update {task_id} -> {task.get('status')}", actor,
                            active_extra)
        event = store.append_event('task_updated', f"updated {task_id} -> {task.get('status')}",
                                   actor=actor, payload={'task_id': task_id,
                                                         'status': task.get('status')},
                                   context_version=new_state['context_version'])
        return _ok(task=task, context_version=new_state['context_version'],
                   event_id=event['event_id'])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool(name='events.append', description='向 EVENTS.jsonl 追加事件（append-only，无删除），'
          '需要 base_context_version；随后递增 context_version。', annotations=WRITE)
def events_append(event_type: str, message: str, base_context_version: int,
                  actor: str = 'DSH', payload: dict | None = None) -> dict:
    try:
        state = _begin_write(base_context_version, actor, 'events.append')
        new_state = _commit(state, f'events.append {event_type}', actor)
        event = store.append_event(event_type, message, actor=actor, payload=payload,
                                   context_version=new_state['context_version'])
        return _ok(event=event, context_version=new_state['context_version'])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool(name='artifacts.register', description='登记项目内文件到 ARTIFACT_INDEX（路径+大小+'
          'SHA256），需要 base_context_version；不删除旧条目，路径必须在项目根目录内。',
          annotations=WRITE)
def artifacts_register(path: str, base_context_version: int, artifact_type: str = 'file',
                       role: str | None = None, task_id: str | None = None,
                       note: str | None = None, status: str = 'VERIFIED',
                       actor: str = 'DSH') -> dict:
    try:
        state = _begin_write(base_context_version, actor, 'artifacts.register')
        artifacts = store.load_artifacts()
        entry = store.register_artifact(artifacts, path, artifact_type=artifact_type,
                                        role=role, task_id=task_id, status=status,
                                        context_version=base_context_version,
                                        note=note)
        store.save_artifacts(artifacts)
        new_state = _commit(state, f"artifacts.register {entry['path']}", actor)
        store.register_artifact(artifacts, entry['path'], artifact_type=artifact_type,
                                role=role, task_id=task_id, status=status,
                                context_version=new_state['context_version'], note=note)
        store.save_artifacts(artifacts)
        event = store.append_event('artifact_registered',
                                   f"registered {entry['path']}",
                                   actor=actor,
                                   payload={'path': entry['path'],
                                            'sha256': entry['sha256']},
                                   context_version=new_state['context_version'])
        return _ok(artifact=entry, context_version=new_state['context_version'],
                   event_id=event['event_id'])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def main():
    # Keep stdout clean for MCP protocol; nothing else should print here.
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
