#!/usr/bin/env python3
"""RFE-1.0 EXCHANGE 文件工具（coordination/exchange/）。

唯一正式协议（master，永久以此为准）：
    RA2MCP_记忆信息工程与文件交换约定_V1.0_优化草案.md

职责
----
* new            按命名规则创建 EXCHANGE 骨架（A-J 分区）
* check          校验：协议 master SHA、META 完整性、分区顺序、编号唯一、
                 保护模型 SHA、REVIEW 超期、context_version 是否过期
* apply-verdicts 解析 E 区末轮“裁决表”并机械应用到 TASK_QUEUE（默认 dry-run）
* selftest       纯函数自检

约束
----
* 本工具只实现协议的执行细节，不新增协议；协议解释权在 master 文件。
* E 区（ChatGPT）与 G 区（USER）只读，工具永不改写；DSH 只写 A/B/C/D/F/H/I/J。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import context_store as store  # noqa: E402

EXCHANGE_DIR = store.COORD_DIR / 'exchange'
ACTIVE_DIR = EXCHANGE_DIR / 'active'
ARCHIVE_DIR = EXCHANGE_DIR / 'archive'
PROTOCOL_MASTER = store.PROJECT_ROOT / 'RA2MCP_记忆信息工程与文件交换约定_V1.0_优化草案.md'
PROTOCOL_ROLE = 'protocol_master'
PROTOCOL_ID = 'RFE-1.0'

SECTION_LETTERS = list('ABCDEFGHIJ')
SECTION_TITLES = {'A': 'META', 'B': 'CONTEXT', 'C': 'REQUEST', 'D': 'EVIDENCE',
                  'E': 'CHATGPT', 'F': 'DSH', 'G': 'USER', 'H': 'ROUND_HISTORY',
                  'I': 'CURRENT_CONCLUSION', 'J': 'CLOSEOUT'}
DIFFICULTY = ('A', 'B', 'C', 'D', 'UNASSESSED')
ENG_TYPES = ('RES', 'AUD', 'ARC', 'MOD', 'FIX', 'PAT', 'UPD', 'DAT', 'TST', 'INT',
             'DOC', 'OPS')
FILE_TYPES = ('EXCHANGE', 'SPEC', 'REPORT', 'GUIDE')
STATUS = ('OPEN', 'WAITING_CHATGPT', 'WAITING_DSH', 'WAITING_USER', 'CLOSED')
REQUIRED_META = ('protocol', 'exchange_id', 'difficulty', 'engineering_type',
                 'related_task', 'context_version', 'revision', 'status')
VERDICT_MAP = {'APPROVE': 'COMPLETED', 'REVISE': 'PROPOSED', 'REJECT': 'BLOCKED'}
FILENAME_RE = re.compile(r'^([ABCD]|UNASSESSED)_(\d{3})_([A-Z]{3})_([A-Z]+)\.md$')
SECTION_RE = re.compile(r'^##\s+([A-J])\.\s*', re.M)
REVIEW_OVERDUE_HOURS = 24


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def parse_iso(ts):
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S'):
        try:
            return _dt.datetime.strptime(str(ts), fmt)
        except (ValueError, TypeError):
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


def section_blocks(text):
    """Split markdown into {letter: content} by '## X. TITLE' headings."""
    matches = list(SECTION_RE.finditer(text))
    blocks = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[match.group(1)] = text[match.end():end]
    return blocks


def parse_meta(text):
    blocks = section_blocks(text)
    if 'A' not in blocks:
        return None
    meta = {}
    for line in blocks['A'].splitlines():
        match = re.match(r'^\s*[-*]\s*([A-Za-z_0-9]+)\s*[:：]\s*(.*?)\s*$', line)
        if match:
            meta[match.group(1).lower()] = match.group(2).strip().strip('`')
    return meta


def filename_info(path):
    match = FILENAME_RE.match(pathlib.Path(path).name)
    if not match:
        return None
    difficulty, number, eng_type, file_type = match.groups()
    return {'difficulty': difficulty, 'number': number,
            'engineering_type': eng_type, 'file_type': file_type}


def section_titles(text):
    return [match.group(1) for match in SECTION_RE.finditer(text)]


def protocol_master_sha():
    if not PROTOCOL_MASTER.is_file():
        return None
    return store.sha256_file(PROTOCOL_MASTER)


def registered_master_sha():
    artifacts = store.load_artifacts()
    for entry in artifacts.get('artifacts') or []:
        if entry.get('role') == PROTOCOL_ROLE:
            return entry.get('sha256')
    return None


def parse_verdict_table(text):
    """Parse the LAST markdown table in section E with header 任务ID | verdict | 理由."""
    blocks = section_blocks(text)
    block = blocks.get('E') or ''
    rows = []
    header = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue
        cells = [cell.strip() for cell in stripped.strip('|').split('|')]
        if len(cells) < 2:
            continue
        if cells[0] in ('任务ID', '任务 ID', 'TASK_ID', 'task_id', '任务'):
            header = cells
            rows = []
            continue
        if header is None:
            continue
        if all(set(cell) <= set('-: ') for cell in cells):
            continue
        row = {'task_id': cells[0].strip('` ').strip(),
               'verdict': cells[1].upper() if len(cells) > 1 else '',
               'reason': cells[2] if len(cells) > 2 else ''}
        row['verdict'] = row['verdict'].strip('` ')
        rows.append(row)
    return rows


def update_meta(text, updates):
    """Update '- key: value' lines inside section A; keep everything else intact."""
    matches = list(SECTION_RE.finditer(text))
    start = end = None
    for index, match in enumerate(matches):
        if match.group(1) == 'A':
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            break
    if start is None:
        raise ValueError('section A not found')
    block = text[start:end]
    lines = block.splitlines(keepends=False)
    for key, value in updates.items():
        pattern = re.compile(r'^(\s*[-*]\s*' + re.escape(key) + r'\s*[:：]\s*)(.*)$')
        replaced = False
        for line_index, line in enumerate(lines):
            if pattern.match(line):
                lines[line_index] = pattern.sub(lambda m: m.group(1) + str(value), line)
                replaced = True
                break
        if not replaced:
            lines.append(f'- {key}: {value}')
    return text[:start] + '\n'.join(lines).rstrip('\n') + '\n\n' + text[end:]


def append_to_section(text, letter, body):
    matches = list(SECTION_RE.finditer(text))
    index = None
    for cursor, match in enumerate(matches):
        if match.group(1) == letter:
            index = cursor
            break
    if index is None:
        raise ValueError(f'section {letter} not found')
    end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
    block = text[matches[index].end():end].rstrip('\n')
    new_block = block + '\n\n' + body.strip('\n') + '\n\n'
    return text[:matches[index].end()] + new_block + text[end:]


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def run_checks(strict=True):
    issues = []

    def add(level, code, detail):
        issues.append({'level': level, 'code': code, 'detail': detail})

    state = store.load_state()
    tasks = store.load_tasks()
    version = int(state.get('context_version') or 0)

    # 0) protocol master
    actual_master_sha = protocol_master_sha()
    registered_sha = registered_master_sha()
    if not actual_master_sha:
        add('FAIL', 'PROTOCOL_MASTER_MISSING', str(PROTOCOL_MASTER))
    elif registered_sha and actual_master_sha != registered_sha:
        add('FAIL', 'PROTOCOL_MASTER_CHANGED',
            'master 协议文件 SHA 与登记不一致（被修改未登记）：'
            f'{actual_master_sha[:16]}… != {registered_sha[:16]}…')
    elif not registered_sha:
        add('WARN', 'PROTOCOL_MASTER_UNREGISTERED', 'master 协议尚未登记到 ARTIFACT_INDEX')

    # 1) exchange files
    active_files = sorted(ACTIVE_DIR.glob('*.md')) if ACTIVE_DIR.exists() else []
    seen_ids = {}
    for path in active_files:
        text = path.read_text(encoding='utf-8')
        info = filename_info(path)
        if not info:
            add('FAIL', 'FILENAME', f'{path.name} 不符合 <难度>_<编号>_<工程类型>_<类型>.md')
            continue
        if info['difficulty'] not in DIFFICULTY or info['engineering_type'] not in ENG_TYPES \
                or info['file_type'] not in FILE_TYPES:
            add('FAIL', 'FILENAME_CODE', f'{path.name} 含非法代码')
        meta = parse_meta(text)
        if not meta:
            add('FAIL', 'META_MISSING', f'{path.name} 缺少 A.META')
            continue
        missing = [key for key in REQUIRED_META if key not in meta]
        if missing:
            add('FAIL', 'META_INCOMPLETE', f'{path.name} 缺字段: {", ".join(missing)}')
        if not str(meta.get('protocol', '')).startswith('RFE-'):
            add('FAIL', 'META_PROTOCOL', f'{path.name} protocol={meta.get("protocol")}')
        if meta.get('difficulty') != info['difficulty']:
            add('WARN', 'META_FILENAME_DIFFICULTY',
                f'{path.name} META difficulty={meta.get("difficulty")} 与文件名不一致')
        if meta.get('engineering_type') != info['engineering_type']:
            add('WARN', 'META_FILENAME_TYPE',
                f'{path.name} META engineering_type={meta.get("engineering_type")} 与文件名不一致')
        exchange_id = str(meta.get('exchange_id') or '')
        if exchange_id in seen_ids:
            add('FAIL', 'EXCHANGE_ID_DUPLICATE',
                f'{exchange_id} 同时出现在 {seen_ids[exchange_id]} 与 {path.name}')
        seen_ids.setdefault(exchange_id, path.name)
        titles = section_titles(text)
        if titles != SECTION_LETTERS:
            add('FAIL', 'SECTION_ORDER',
                f'{path.name} 分区 {titles} != {SECTION_LETTERS}')
        try:
            file_version = int(str(meta.get('context_version') or 0))
        except ValueError:
            add('FAIL', 'META_VERSION', f'{path.name} context_version 非整数')
            file_version = 0
        if file_version > version:
            add('FAIL', 'META_VERSION_FUTURE',
                f'{path.name} context_version v{file_version} > 当前 v{version}')
        elif file_version < version:
            add('WARN', 'META_VERSION_STALE',
                f'{path.name} 基于 v{file_version}（当前 v{version}），交付前应 refresh')
        related = str(meta.get('related_task') or 'NONE').strip('` ')
        if related not in ('NONE', 'None', ''):
            if store.find_task(tasks, related) is None:
                add('WARN', 'RELATED_TASK_MISSING',
                    f'{path.name} related_task={related} 不在 TASK_QUEUE')
        status = str(meta.get('status') or '')
        if status not in STATUS:
            add('FAIL', 'META_STATUS', f'{path.name} status={status} 非枚举值')

    # 2) protected models
    for name, model_meta in (state.get('model_artifacts') or {}).items():
        target = store.PROJECT_ROOT / str(model_meta.get('path') or '')
        if not target.is_file():
            add('FAIL', 'PROTECTED_MODEL_MISSING', f'{name}: {model_meta.get("path")}')
        elif store.sha256_file(target) != model_meta.get('sha256'):
            add('FAIL', 'PROTECTED_MODEL_SHA', f'{name}: {model_meta.get("path")} sha 变化')

    # 3) REVIEW overdue
    overdue = []
    for task in tasks.get('tasks') or []:
        if task.get('status') != 'REVIEW':
            continue
        hours = age_hours(task.get('updated_at') or task.get('created_at'))
        if hours is not None and hours > REVIEW_OVERDUE_HOURS:
            overdue.append(f"{task.get('task_id')}({hours}h)")
    if overdue:
        add('WARN', 'REVIEW_OVERDUE', 'REVIEW 超过 24h 未裁决: ' + ', '.join(overdue))

    fails = [issue for issue in issues if issue['level'] == 'FAIL']
    warns = [issue for issue in issues if issue['level'] == 'WARN']
    return {'context_version': version, 'exchange_files': [p.name for p in active_files],
            'verdict': 'FAIL' if fails else 'PASS', 'fails': fails, 'warns': warns,
            'issues': issues}


def print_checks(result):
    print(f"exchange check @ v{result['context_version']} -> {result['verdict']}")
    print(f"  active files: {', '.join(result['exchange_files']) or '(none)'}")
    for issue in result['issues']:
        print(f"  [{issue['level']}] {issue['code']}: {issue['detail']}")
    if result['verdict'] == 'PASS':
        print(f"  PASS（{len(result['warns'])} 条警告）")


# ---------------------------------------------------------------------------
# create skeleton
# ---------------------------------------------------------------------------
TEMPLATE = '''# {stem}

## A. META
- protocol: {protocol}
- exchange_id: EX-{number}
- difficulty: {difficulty}
- engineering_type: {eng_type}
- related_task: {related_task}
- context_version: {version}
- revision: 1
- status: OPEN
- created_by: DSH
- created_at: {now}

## B. CONTEXT
（待 DSH 填写：背景、当前状态、已完成、决策、约束、未解决问题）

## C. REQUEST
（待 DSH 填写：本轮核心问题、要 ChatGPT 做什么、DSH 后续做什么、本轮不做、需用户授权项）

## D. EVIDENCE
（待 DSH 填写：源码片段/测试原始输出/日志/报告结论/关键文件 SHA256）

## E. CHATGPT
（仅 ChatGPT 追加；每轮用 ### ROUND-n，末尾必须附裁决表）

## F. DSH
（仅 DSH 追加；每轮用 ### ROUND-n）

## G. USER
（仅用户追加）

## H. ROUND_HISTORY
| ROUND | 发送方 → 接收方 | 引用上轮 | 核心内容 | 状态 |
|---|---|---|---|---|

## I. CURRENT_CONCLUSION
（待填写）

## J. CLOSEOUT
- status: OPEN
'''


def create_file(difficulty, eng_type, related_task='NONE', title='', number=None,
                file_type='EXCHANGE'):
    if difficulty not in DIFFICULTY:
        raise ValueError(f'difficulty 必须是 {DIFFICULTY}')
    if eng_type not in ENG_TYPES:
        raise ValueError(f'engineering_type 必须是 {ENG_TYPES}')
    if file_type not in FILE_TYPES:
        raise ValueError(f'file_type 必须是 {FILE_TYPES}')
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if number is None:
        used = []
        for path in ACTIVE_DIR.glob('*.md'):
            info = filename_info(path)
            if info:
                used.append(int(info['number']))
        number = f'{(max(used) + 1) if used else 1:03d}'
    stem = f'{difficulty}_{number}_{eng_type}_{file_type}'
    target = ACTIVE_DIR / f'{stem}.md'
    if target.exists():
        raise FileExistsError(str(target))
    state = store.load_state()
    body = TEMPLATE.format(
        stem=stem, protocol=PROTOCOL_ID, number=number, difficulty=difficulty,
        eng_type=eng_type, related_task=related_task, version=state.get('context_version'),
        now=store.now_iso())
    if title:
        body = body.replace(f'# {stem}', f'# {stem} · {title}', 1)
    store.atomic_write_text(target, body)
    return target


# ---------------------------------------------------------------------------
# apply verdicts
# ---------------------------------------------------------------------------
def apply_verdicts(path, apply=False):
    path = pathlib.Path(path)
    text = path.read_text(encoding='utf-8')
    meta = parse_meta(text) or {}
    state = store.load_state()
    tasks = store.load_tasks()
    version = int(state.get('context_version') or 0)
    rows = parse_verdict_table(text)
    report = {'file': str(path), 'context_version': version, 'rows': len(rows),
              'applications': [], 'applied': False}
    for row in rows:
        task_id, verdict = row['task_id'], row['verdict']
        task = store.find_task(tasks, task_id)
        entry = {'task_id': task_id, 'verdict': verdict, 'reason': row.get('reason', ''),
                 'result': ''}
        if not task_id:
            entry['result'] = 'MISSING_TASK_ID'
        elif task is None:
            entry['result'] = 'TASK_NOT_FOUND'
        elif verdict not in VERDICT_MAP:
            entry['result'] = 'UNKNOWN_VERDICT'
        elif task.get('status') != 'REVIEW':
            entry['result'] = f"NOT_IN_REVIEW(status={task.get('status')})"
        else:
            entry['result'] = VERDICT_MAP[verdict]
            store.update_task(
                tasks, task_id, status=VERDICT_MAP[verdict],
                result_summary=(str(task.get('result_summary') or '')
                                + f"\n[RFE-1.0 verdict {verdict} @v{version}] {row.get('reason', '')}").strip())
        report['applications'].append(entry)

    valid = [entry for entry in report['applications']
             if entry['result'] in set(VERDICT_MAP.values())]
    report['valid_applications'] = len(valid)
    if not apply or not valid:
        return report

    with store.Lock():
        store.save_tasks(tasks)
        new_state = store.bump_context(state, reason=f'exchange verdicts {path.name}', actor='DSH')
    store.regenerate_current_context(new_state)
    lines = [f'\n### ROUND-{_round_number(text)} · DSH 裁决应用',
             f'- 应用时间：{store.now_iso()}',
             f'- 依据：`{store.relative(path)}` E 区裁决表（context_version v{version} → v{new_state["context_version"]}）']
    for entry in report['applications']:
        lines.append(f"- `{entry['task_id']}` {entry['verdict']} -> {entry['result']}"
                     + (f"（{entry['reason']}）" if entry['reason'] else ''))
    text = append_to_section(text, 'F', '\n'.join(lines))
    text = update_meta(text, {'context_version': new_state['context_version'],
                              'revision': int(meta.get('revision') or 1) + 1,
                              'status': 'WAITING_CHATGPT'})
    store.atomic_write_text(path, text)
    store.append_event('exchange_verdicts_applied',
                       f"{path.name}: {len(valid)} verdicts applied",
                       actor='DSH', payload={'file': store.relative(path),
                                             'applications': report['applications']},
                       context_version=new_state['context_version'])
    report['applied'] = True
    report['new_version'] = new_state['context_version']
    return report


def _round_number(text):
    block = section_blocks(text).get('F') or ''
    numbers = [int(match) for match in re.findall(r'###\s+ROUND-(\d+)', block)]
    return (max(numbers) if numbers else 0)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
SELFTEST_TEXT = '''# A_001_AUD_EXCHANGE

## A. META
- protocol: RFE-1.0
- exchange_id: EX-001
- difficulty: A
- engineering_type: AUD
- related_task: TASK-1
- context_version: 7
- revision: 1
- status: WAITING_CHATGPT

## B. CONTEXT
ctx

## C. REQUEST
req

## D. EVIDENCE
ev

## E. CHATGPT
### ROUND-001
分析

| 任务ID | verdict | 理由 |
|---|---|---|
| TASK-1 | APPROVE | ok |
| TASK-2 | REVISE | bad |

## F. DSH
f

## G. USER
g

## H. ROUND_HISTORY
h

## I. CURRENT_CONCLUSION
i

## J. CLOSEOUT
- status: OPEN
'''


def selftest():
    failures = []

    def check(name, condition):
        if condition:
            print(f'  PASS {name}')
        else:
            failures.append(name)
            print(f'  FAIL {name}')

    meta = parse_meta(SELFTEST_TEXT)
    check('parse_meta required keys', meta is not None and 'exchange_id' in meta)
    check('parse_meta protocol', meta['protocol'] == 'RFE-1.0')
    check('section order', section_titles(SELFTEST_TEXT) == SECTION_LETTERS)
    info = filename_info(pathlib.Path('A_001_AUD_EXCHANGE.md'))
    check('filename parse', info == {'difficulty': 'A', 'number': '001',
                                     'engineering_type': 'AUD', 'file_type': 'EXCHANGE'})
    check('filename rejects time', filename_info(pathlib.Path('A_001_AUD_EXCHANGE_20261101.md')) is None)
    rows = parse_verdict_table(SELFTEST_TEXT)
    check('verdict table rows', len(rows) == 2)
    check('verdict table values',
          [row['verdict'] for row in rows] == ['APPROVE', 'REVISE'])
    check('verdict map', VERDICT_MAP == {'APPROVE': 'COMPLETED', 'REVISE': 'PROPOSED',
                                         'REJECT': 'BLOCKED'})
    updated = update_meta(SELFTEST_TEXT, {'revision': 2, 'status': 'WAITING_DSH'})
    new_meta = parse_meta(updated)
    check('update_meta revision', new_meta['revision'] == '2')
    check('update_meta status', new_meta['status'] == 'WAITING_DSH')
    check('update_meta keeps sections', section_titles(updated) == SECTION_LETTERS)
    appended = append_to_section(updated, 'F', '### ROUND-002\nok')
    check('append_to_section adds round', 'ROUND-002' in appended)
    check('append_to_section keeps E intact', 'TASK-2 | REVISE' in appended)
    result = run_checks()
    print(f"  INFO exchange check verdict: {result['verdict']} "
          f"({len(result['fails'])} fails / {len(result['warns'])} warns)")
    for issue in result['fails']:
        print(f"    FAIL {issue['code']}: {issue['detail']}")
    print(f"selftest: {'PASS' if not failures else 'FAIL'} ({len(failures)} failed)")
    return 0 if not failures else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description='RFE-1.0 EXCHANGE tooling')
    sub = parser.add_subparsers(dest='command', required=True)

    check_parser = sub.add_parser('check', help='校验 EXCHANGE 目录与协议一致性')
    check_parser.add_argument('--json', action='store_true')

    new_parser = sub.add_parser('new', help='创建 EXCHANGE 骨架')
    new_parser.add_argument('--difficulty', required=True, choices=DIFFICULTY)
    new_parser.add_argument('--type', dest='eng_type', required=True, choices=ENG_TYPES)
    new_parser.add_argument('--file-type', default='EXCHANGE', choices=FILE_TYPES)
    new_parser.add_argument('--task', default='NONE')
    new_parser.add_argument('--title', default='')
    new_parser.add_argument('--number', default=None)

    apply_parser = sub.add_parser('apply-verdicts', help='应用 E 区裁决表（默认 dry-run）')
    apply_parser.add_argument('--file', required=True)
    apply_parser.add_argument('--apply', action='store_true')

    sub.add_parser('selftest', help='纯函数自检')
    args = parser.parse_args(argv)

    if args.command == 'check':
        result = run_checks()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_checks(result)
        return 0 if result['verdict'] == 'PASS' else 1
    if args.command == 'new':
        target = create_file(args.difficulty, args.eng_type, related_task=args.task,
                             title=args.title, number=args.number,
                             file_type=args.file_type)
        print(store.relative(target))
        return 0
    if args.command == 'apply-verdicts':
        report = apply_verdicts(args.file, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'selftest':
        return selftest()
    return 1


if __name__ == '__main__':
    sys.exit(main())
