"""End-to-end stdio MCP protocol smoke test for mcp_context_server.py.

Copies the live coordination/ tree into a temporary root, launches the server
as a real subprocess, performs the MCP handshake (initialize / initialized /
tools/list / tools/call), verifies the eight tools and exercises one controlled
write (tasks.create) plus a stale-version rejection. The live context is never
modified by this test.

Usage:
    python scripts/coordination/test_mcp_stdio.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

REAL_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVER = pathlib.Path(__file__).resolve().parent / 'mcp_context_server.py'
EXPECTED_TOOLS = {'context.get', 'context.diff', 'context.search', 'files.search',
                  'tasks.create', 'tasks.update', 'events.append', 'artifacts.register'}
LOG_LINES = []


def log(message):
    line = f'[mcp-selftest] {message}'
    print(line, flush=True)
    LOG_LINES.append(line)


def send(proc, payload):
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
    proc.stdin.flush()


def read_response(proc, request_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError('server closed stdout unexpectedly')
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if message.get('id') == request_id:
            return message
        if 'error' in message:
            raise RuntimeError(f'server error: {message}')
    raise TimeoutError(f'no response for id={request_id}')


def result_payload(message):
    # `message` may be a full JSON-RPC response or an already-unwrapped result dict.
    result = message.get('result') if isinstance(message, dict) and 'result' in message else message
    result = result or {}
    structured = result.get('structuredContent')
    if isinstance(structured, dict) and structured:
        return structured
    for item in result.get('content') or []:
        if item.get('type') == 'text':
            try:
                parsed = json.loads(item['text'])
                if isinstance(parsed, dict):
                    return parsed
                return {'value': parsed}
            except json.JSONDecodeError:
                return {'raw': item['text']}
    return structured if isinstance(structured, dict) else result


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ra2ctx-mcp-'))
    try:
        shutil.copytree(REAL_ROOT / 'coordination', tmp / 'coordination',
                        ignore=shutil.ignore_patterns('.context.lock'))
        env = dict(os.environ)
        env['RA2_CONTEXT_ROOT'] = str(tmp)
        env['RA2_COORD_DIR'] = str(tmp / 'coordination')
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', bufsize=1, cwd=str(tmp), env=env)
        try:
            request_id = 0

            def call(method, params=None):
                nonlocal request_id
                request_id += 1
                send(proc, {'jsonrpc': '2.0', 'id': request_id, 'method': method,
                            'params': params or {}})
                message = read_response(proc, request_id)
                if 'error' in message:
                    raise RuntimeError(f'{method} -> {message["error"]}')
                return message['result']

            init = call('initialize', {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'ra2-coordination-selftest', 'version': '0.1.0'},
            })
            assert init.get('serverInfo', {}).get('name'), init
            log(f"initialize OK: {init.get('serverInfo')}")
            send(proc, {'jsonrpc': '2.0', 'method': 'notifications/initialized'})

            tools_result = call('tools/list')
            names = {tool['name'] for tool in tools_result.get('tools', [])}
            log(f'tools/list OK: {sorted(names)}')
            missing = EXPECTED_TOOLS - names
            assert not missing, f'missing tools: {missing}'
            extra = names - EXPECTED_TOOLS
            if extra:
                log(f'note: extra tools registered: {extra}')

            state_message = call('tools/call', {
                'name': 'context.get', 'arguments': {'events_limit': 3}})
            state = result_payload(state_message)
            if not state.get('ok'):
                log(f'context.get returned: {state_message}')
            assert state.get('ok'), state
            current_version = int(state['context_version'])
            log(f"context.get OK: context_version={current_version}")
            assert state.get('model_artifacts', {}).get('R1', {}).get('sha256')
            assert 'current_context_md' in state

            search = result_payload(call('tools/call', {
                'name': 'context.search', 'arguments': {'query': 'R3', 'limit': 3}}))
            assert search.get('ok') and search.get('matches', 0) > 0, search
            log(f"context.search OK: matches={search['matches']}")

            files = result_payload(call('tools/call', {
                'name': 'files.search',
                'arguments': {'query': 'PROJECT_STATE', 'limit': 3}}))
            assert files.get('ok') and files.get('matches', 0) > 0, files
            log(f"files.search OK: matches={files['matches']}")

            diff = result_payload(call('tools/call', {
                'name': 'context.diff', 'arguments': {'from_version': 1}}))
            assert diff.get('ok') and diff.get('to_version', 0) >= current_version, diff
            log(f"context.diff OK: changed={diff.get('diff_count')}")

            created = result_payload(call('tools/call', {
                'name': 'tasks.create',
                'arguments': {'title': 'mcp smoke task', 'objective': 'verify write path',
                              'base_context_version': current_version, 'owner': 'DSH'}}))
            assert created.get('ok'), created
            log(f"tasks.create OK: task_id={created['task']['task_id']} "
                f"version={created['context_version']}")
            assert created['context_version'] == current_version + 1

            stale = result_payload(call('tools/call', {
                'name': 'events.append',
                'arguments': {'event_type': 'stale_smoke', 'message': 'should fail',
                              'base_context_version': current_version}}))
            assert stale.get('ok') is False, stale
            log(f"stale base_context_version correctly rejected: {stale.get('error')}")

            event = result_payload(call('tools/call', {
                'name': 'events.append',
                'arguments': {'event_type': 'mcp_smoke_event',
                              'message': 'stdio smoke test appended event',
                              'base_context_version': created['context_version'],
                              'payload': {'source': 'test_mcp_stdio.py'}}}))
            assert event.get('ok'), event
            log(f"events.append OK: {event['event']['event_id']}")

            artifact = result_payload(call('tools/call', {
                'name': 'artifacts.register',
                'arguments': {'path': 'coordination/README.md',
                              'base_context_version': event['context_version'],
                              'artifact_type': 'context_file', 'role': 'readme'}}))
            assert artifact.get('ok'), artifact
            log(f"artifacts.register OK: {artifact['artifact']['path']} "
                f"sha={artifact['artifact']['sha256'][:12]}…")
        finally:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            stderr = proc.stderr.read() if proc.stderr else ''
            if stderr.strip():
                log(f'server stderr tail: {stderr.strip().splitlines()[-3:]}')
        log('ALL MCP STDIO TESTS PASSED')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        log(f'FAILED: {exc!r}')
        raise
