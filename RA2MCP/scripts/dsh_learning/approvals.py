"""一次性训练审批令牌 v2（P0 审批安全底座；不启动游戏/训练）。

设计依据：reports/dsh-learning/v2.2/v2.2-manager-security.md（§10.1–§10.4）。
核心约束：
- 令牌一次性：UNUSED -> RESERVED -> CONSUMED，任何失败都必须重新申请；
- 双维度状态：消费状态 (UNUSED/RESERVED/CONSUMED) × 启动结果
  (NOT_LAUNCHED/STARTING/RUNNING/FINISHED_OK/FINISHED_FAIL/FAILED_NO_RUN)；
- 并发原子：每个令牌一个 Windows 文件锁（msvcrt.locking 排他），单写者 + 只追加状态；
- 发放时记录 expected model SHA256；**启动时从实际模型文件重新计算**，不信任请求方；
- 绑定：experiment/skill/config_hash/budget(max_steps,eval_every_steps,eval_episodes)/
  seed/workers/episode_seconds/reward/action/obs 版本/thresholds(version,hash)/
  eval_protocol_hash/game(可执行文件 SHA256, 桥接版本)；
- 过期、撤销、配置变化、模型变化、预算越界、重复启动均拒绝；
- 本模块不 import 训练源码、不启动子进程、不启动游戏。
"""
import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import secrets
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
APPROVALS_DIR = ROOT / 'reports' / 'dsh-learning' / 'manager' / 'approvals'
TOKENS_PATH = APPROVALS_DIR / 'tokens.jsonl'
AUDIT_PATH = APPROVALS_DIR / 'audit.jsonl'
LOCKS_DIR = APPROVALS_DIR / 'locks'
ACTIVE_RUNS = [ROOT / 'reports/rl/active-runs.json', ROOT / 'reports/rl/active-run.json']

TOKEN_SCHEMA = 'approval-token-v2'
DEFAULT_TTL_HOURS = 24
RESERVED_TIMEOUT_S = 300
LOCK_TIMEOUT_S = 2.0
CONSUMPTION_STATES = ('UNUSED', 'RESERVED', 'CONSUMED')
LAUNCH_STATES = ('NOT_LAUNCHED', 'STARTING', 'RUNNING', 'FINISHED_OK',
                 'FINISHED_FAIL', 'FAILED_NO_RUN')
LIFECYCLE_STATES = ('ISSUED', 'RESERVED', 'RUNNING', 'COMPLETED', 'FAILED_RUN',
                    'ABANDONED', 'EXPIRED', 'REVOKED', 'REJECTED')
CONFIG_APPROVAL_FIELDS = (
    'experiment', 'skill', 'budget', 'seed', 'workers', 'episode_seconds',
    'reward_version', 'action_space_version', 'obs_version',
    'thresholds_version', 'thresholds_hash', 'eval_protocol_hash', 'game',
)


class ApprovalError(RuntimeError):
    def __init__(self, code, message, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(code + ': ' + message)


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def parse_iso(text):
    try:
        return time.mktime(time.strptime(text, '%Y-%m-%dT%H:%M:%S%z'))
    except Exception:  # noqa: BLE001
        return 0.0


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def content_hash(obj):
    return sha256_bytes(canonical_json(obj).encode('utf-8'))


def rel_to_root(path):
    return str(pathlib.Path(path).resolve().relative_to(ROOT)).replace('\\', '/')


def root_path(rel):
    return (ROOT / rel).resolve()


def _ensure_dirs():
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path, record):
    _ensure_dirs()
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + '\n')
        fh.flush()
        os.fsync(fh.fileno())


def audit(event, token_id=None, actor='system', detail=None):
    _append_jsonl(AUDIT_PATH, {'ts': now_iso(), 'event': event, 'token_id': token_id,
                               'actor': actor, 'detail': detail or {}})


def load_config(config_path):
    path = pathlib.Path(config_path)
    if not path.exists():
        raise ApprovalError('CONFIG_NOT_FOUND', str(path))
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as exc:  # noqa: BLE001
        raise ApprovalError('CONFIG_INVALID', repr(exc))


def config_hash_of(config):
    return content_hash(config)


def _fold_tokens():
    tokens = {}
    if not TOKENS_PATH.exists():
        return tokens
    with open(TOKENS_PATH, 'r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if record.get('token_id'):
                tokens[record['token_id']] = record
    return tokens


def get_token(token_id):
    token = _fold_tokens().get(token_id)
    if token is None:
        raise ApprovalError('TOKEN_NOT_FOUND', token_id)
    return token


def _store(token):
    token['updated_at'] = now_iso()
    _append_jsonl(TOKENS_PATH, token)


def _pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def _lock_path(token_id):
    return LOCKS_DIR / (token_id + '.lock')


@contextlib.contextmanager
def token_lock(token_id, timeout=LOCK_TIMEOUT_S):
    """排他锁：Windows 用 msvcrt.locking，其它平台用 fcntl.flock。"""
    _ensure_dirs()
    path = _lock_path(token_id)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ApprovalError('TOKEN_BUSY', 'token lock timeout: ' + token_id)
                time.sleep(0.05)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({'token_id': token_id, 'pid': os.getpid(),
                                 'host': os.environ.get('COMPUTERNAME', ''),
                                 'created_at': now_iso()}).encode('utf-8'))
        os.fsync(fd)
        yield
    finally:
        if acquired:
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _active_training_pids():
    pids = set()
    for path in ACTIVE_RUNS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception:  # noqa: BLE001
            continue
        records = data if isinstance(data, list) else [data]
        for record in records:
            if isinstance(record, dict) and _pid_alive(record.get('pid')):
                pids.add(int(record['pid']))
    return sorted(pids)


def validate_token_structure(token):
    if token.get('schema') != TOKEN_SCHEMA:
        raise ApprovalError('TOKEN_SCHEMA', 'unexpected token schema')
    for key in ('token_id', 'nonce', 'issued_at', 'expires_at', 'target', 'model',
                'config_path', 'config_hash', 'budget', 'seed', 'workers',
                'episode_seconds', 'reward_version', 'action_space_version',
                'obs_version', 'thresholds_version', 'thresholds_hash',
                'eval_protocol_hash', 'consumption', 'launch', 'lifecycle'):
        if key not in token:
            raise ApprovalError('TOKEN_INCOMPLETE', 'missing field: ' + key)
    if token['consumption'] not in CONSUMPTION_STATES:
        raise ApprovalError('TOKEN_STATE', 'bad consumption: ' + str(token['consumption']))
    if token['launch'] not in LAUNCH_STATES:
        raise ApprovalError('TOKEN_STATE', 'bad launch: ' + str(token['launch']))
    if token['lifecycle'] not in LIFECYCLE_STATES:
        raise ApprovalError('TOKEN_STATE', 'bad lifecycle: ' + str(token['lifecycle']))
    if not token['budget'].get('max_steps', 0) > 0:
        raise ApprovalError('TOKEN_BUDGET', 'max_steps must be > 0')
    if not token['budget'].get('eval_every_steps', 0) > 0:
        raise ApprovalError('TOKEN_BUDGET', 'eval_every_steps must be > 0')
    if not token['budget'].get('eval_episodes', 0) > 0:
        raise ApprovalError('TOKEN_BUDGET', 'eval_episodes must be > 0')
    return True


def issue_token(config_path, model_path=None, ttl_hours=DEFAULT_TTL_HOURS,
                issued_by='USER', extra=None):
    """由用户侧调用：生成一次性令牌（写入只追加令牌库）。"""
    config_path = pathlib.Path(config_path).resolve()
    config = load_config(config_path)
    approval = config.get('approval')
    if not isinstance(approval, dict):
        raise ApprovalError('CONFIG_INVALID', 'config.approval missing')
    missing = [f for f in CONFIG_APPROVAL_FIELDS if f not in approval]
    if missing:
        raise ApprovalError('CONFIG_INVALID', 'config.approval missing: ' + str(missing))
    target = {'experiment': approval['experiment'], 'skill': approval['skill']}
    model = {'path': None, 'sha256_expected': None}
    if model_path:
        model_path = pathlib.Path(model_path).resolve()
        if not model_path.exists():
            raise ApprovalError('MODEL_NOT_FOUND', str(model_path))
        model = {'path': rel_to_root(model_path), 'sha256_expected': sha256_file(model_path)}
    token = {
        'schema': TOKEN_SCHEMA,
        'token_id': 'APR-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8],
        'nonce': secrets.token_hex(16),
        'issued_by': issued_by,
        'issued_at': now_iso(),
        'expires_at': time.strftime('%Y-%m-%dT%H:%M:%S%z',
                                    time.localtime(time.time() + ttl_hours * 3600)),
        'ttl_hours': ttl_hours,
        'target': target,
        'model': model,
        'config_path': rel_to_root(config_path),
        'config_hash': config_hash_of(config),
        'budget': dict(approval['budget']),
        'seed': approval['seed'],
        'workers': approval['workers'],
        'episode_seconds': approval['episode_seconds'],
        'reward_version': approval['reward_version'],
        'action_space_version': approval['action_space_version'],
        'obs_version': approval['obs_version'],
        'thresholds_version': approval['thresholds_version'],
        'thresholds_hash': approval['thresholds_hash'],
        'eval_protocol_hash': approval['eval_protocol_hash'],
        'game': dict(approval.get('game') or {}),
        'consumption': 'UNUSED',
        'launch': 'NOT_LAUNCHED',
        'lifecycle': 'ISSUED',
        'reservation': None,
        'consumed': None,
        'notes': extra or {},
    }
    _ensure_dirs()
    with token_lock(token['token_id']):
        if token['token_id'] in _fold_tokens():
            raise ApprovalError('TOKEN_ID_COLLISION', token['token_id'])
        _store(token)
        audit('issue', token['token_id'], actor=issued_by,
              detail={'experiment': target['experiment'], 'skill': target['skill'],
                      'expires_at': token['expires_at']})
    return token


def _expected_runtime(runtime):
    missing = [k for k in ('workers', 'episode_seconds', 'steps', 'device',
                           'resume', 'fresh', 'reward_version',
                           'action_space_version', 'obs_version', 'config_path')
               if k not in runtime]
    if missing:
        raise ApprovalError('REQUEST_INCOMPLETE', 'runtime missing: ' + str(missing))
    return runtime


def validate_launch_request(token_id, config_path, runtime, check_active_run=True):
    """只校验、不改变任何状态；训练入口在真正 spawn 前调用。"""
    runtime = _expected_runtime(runtime)
    if runtime['fresh'] == bool(runtime['resume']):
        raise ApprovalError('REQUEST_INVALID', 'exactly one of fresh/resume required')
    token = get_token(token_id)
    validate_token_structure(token)
    if token['lifecycle'] != 'ISSUED' or token['consumption'] != 'UNUSED':
        raise ApprovalError('TOKEN_ALREADY_USED',
                            'token lifecycle=%s consumption=%s'
                            % (token['lifecycle'], token['consumption']))
    if time.time() > parse_iso(token['expires_at']):
        raise ApprovalError('TOKEN_EXPIRED', token['expires_at'])
    config_path = pathlib.Path(config_path).resolve()
    config = load_config(config_path)
    approval = config.get('approval') or {}
    if rel_to_root(config_path) != token['config_path']:
        raise ApprovalError('CONFIG_PATH_MISMATCH', '%s != %s'
                            % (rel_to_root(config_path), token['config_path']))
    if config_hash_of(config) != token['config_hash']:
        raise ApprovalError('CONFIG_HASH_MISMATCH', 'config content changed')
    for field in ('experiment', 'skill', 'budget', 'seed', 'workers',
                  'episode_seconds', 'reward_version', 'action_space_version',
                  'obs_version', 'thresholds_version', 'thresholds_hash',
                  'eval_protocol_hash', 'game'):
        token_value = (token['target'].get(field) if field in ('experiment', 'skill')
                       else token.get(field))
        if approval.get(field) != token_value:
            raise ApprovalError('BINDING_MISMATCH', 'field: ' + field,
                                {'token': token_value, 'config': approval.get(field)})
    # 运行时边界
    if runtime['workers'] != token['workers']:
        raise ApprovalError('BINDING_MISMATCH', 'workers')
    if float(runtime['episode_seconds']) != float(token['episode_seconds']):
        raise ApprovalError('BINDING_MISMATCH', 'episode_seconds')
    runtime_seed = runtime.get('seed')
    if runtime_seed is not None and int(runtime_seed) != int(token['seed']):
        raise ApprovalError('BINDING_MISMATCH', 'seed',
                            {'runtime': int(runtime_seed), 'token': token['seed']})
    steps = int(runtime['steps'] or 0)
    if steps <= 0:
        raise ApprovalError('BUDGET_UNBOUNDED',
                            'token runs require explicit --steps > 0')
    if steps > int(token['budget']['max_steps']):
        raise ApprovalError('BUDGET_EXCEEDED', '%s > %s'
                            % (steps, token['budget']['max_steps']))
    for field in ('reward_version', 'action_space_version', 'obs_version'):
        if runtime[field] != token[field]:
            raise ApprovalError('BINDING_MISMATCH', field,
                                {'runtime': runtime[field], 'token': token[field]})
    # 模型：启动时从实际文件重算 SHA256
    model = token.get('model') or {}
    if model.get('path'):
        if not runtime['resume']:
            raise ApprovalError('MODEL_MISMATCH', 'token bound to resume model but runtime is fresh')
        actual_path = pathlib.Path(runtime['resume']).resolve()
        if rel_to_root(actual_path) != model['path']:
            raise ApprovalError('MODEL_PATH_MISMATCH',
                                '%s != %s' % (rel_to_root(actual_path), model['path']))
        actual_sha = sha256_file(actual_path)
        if actual_sha != model['sha256_expected']:
            raise ApprovalError('MODEL_SHA_MISMATCH',
                                'actual=%s expected=%s'
                                % (actual_sha, model['sha256_expected']))
    else:
        if not runtime['fresh']:
            raise ApprovalError('MODEL_MISMATCH', 'token is fresh-run; resume requires model-bound token')
    # 游戏/桥接（若令牌绑定要求，必须由运行时提供并校验）
    game = token.get('game') or {}
    if game.get('executable_sha256'):
        exe = runtime.get('game_executable')
        if not exe:
            raise ApprovalError('GAME_UNVERIFIED', 'token binds executable sha but runtime gave none')
        actual = sha256_file(pathlib.Path(exe).resolve())
        if actual != game['executable_sha256']:
            raise ApprovalError('GAME_SHA_MISMATCH', 'actual=%s' % actual)
    if game.get('bridge_version') and runtime.get('bridge_version') not in (None, ''):
        if runtime['bridge_version'] != game['bridge_version']:
            raise ApprovalError('BRIDGE_VERSION_MISMATCH')
    if check_active_run:
        pids = _active_training_pids()
        if pids:
            raise ApprovalError('DUPLICATE_LAUNCH',
                                'active training pid(s): ' + str(pids))
    return {
        'token_id': token_id,
        'experiment': token['target']['experiment'],
        'skill': token['target']['skill'],
        'config': config,
        'config_hash': token['config_hash'],
        'model_path': model.get('path'),
        'actual_model_sha256': (sha256_file(root_path(model['path']))
                                if model.get('path') else None),
        'steps': steps,
        'budget': token['budget'],
    }


def reserve_and_mark_running(token_id, request, run_dir, pid):
    """原子预占并标记 RUNNING；返回最终令牌记录。调用方须已 validate。"""
    with token_lock(token_id):
        token = get_token(token_id)  # 锁内重读，防并发
        if token['lifecycle'] != 'ISSUED' or token['consumption'] != 'UNUSED':
            raise ApprovalError('TOKEN_ALREADY_USED', 'concurrent consumption rejected',
                                {'lifecycle': token['lifecycle'],
                                 'consumption': token['consumption']})
        token['consumption'] = 'RESERVED'
        token['launch'] = 'NOT_LAUNCHED'
        token['lifecycle'] = 'RESERVED'
        token['reservation'] = {'at': now_iso(), 'pid': int(pid),
                                'run_dir': str(run_dir),
                                'actual_model_sha256': request.get('actual_model_sha256'),
                                'config_hash': request.get('config_hash'),
                                'steps': request.get('steps')}
        _store(token)
        audit('reserve', token_id, detail={'pid': int(pid), 'run_dir': str(run_dir)})
        token = dict(token)
        token['consumption'] = 'CONSUMED'
        token['launch'] = 'RUNNING'
        token['lifecycle'] = 'RUNNING'
        token['consumed'] = dict(token['reservation'])
        token['consumed']['state_at'] = now_iso()
        _store(token)
        audit('start', token_id, detail={'pid': int(pid), 'run_dir': str(run_dir)})
    return token


def mark_finished(token_id, ok, detail=None):
    token = get_token(token_id)
    if token['consumption'] != 'CONSUMED':
        raise ApprovalError('TOKEN_STATE', 'cannot finish non-consumed token')
    token['launch'] = 'FINISHED_OK' if ok else 'FINISHED_FAIL'
    token['lifecycle'] = 'COMPLETED' if ok else 'FAILED_RUN'
    _store(token)
    audit('finish', token_id, detail={'ok': bool(ok), **(detail or {})})
    return token


def mark_failed_no_run(token_id, reason):
    token = get_token(token_id)
    if token['consumption'] == 'CONSUMED':
        token['launch'] = 'FAILED_NO_RUN'
        token['lifecycle'] = 'ABANDONED'
    elif token['consumption'] == 'RESERVED':
        token['consumption'] = 'CONSUMED'
        token['launch'] = 'FAILED_NO_RUN'
        token['lifecycle'] = 'ABANDONED'
    elif token['consumption'] == 'UNUSED':
        token['lifecycle'] = 'REJECTED'
    else:
        raise ApprovalError('TOKEN_STATE', 'cannot mark failed for ' + token['consumption'])
    _store(token)
    audit('abandon', token_id, detail={'reason': reason})
    return token


def expire(token_id):
    token = get_token(token_id)
    if token['consumption'] != 'UNUSED' or token['lifecycle'] != 'ISSUED':
        raise ApprovalError('TOKEN_STATE', 'only ISSUED/UNUSED tokens can expire')
    token['lifecycle'] = 'EXPIRED'
    _store(token)
    audit('expire', token_id)
    return token


def revoke(token_id, reason='user_revoked'):
    token = get_token(token_id)
    if token['consumption'] != 'UNUSED' or token['lifecycle'] != 'ISSUED':
        raise ApprovalError('TOKEN_STATE', 'only ISSUED/UNUSED tokens can be revoked')
    token['lifecycle'] = 'REVOKED'
    _store(token)
    audit('revoke', token_id, detail={'reason': reason})
    return token


def recover_stale(now=None, timeout_s=RESERVED_TIMEOUT_S):
    """RESERVED 超时且进程不存在 → ABANDONED（不可恢复为可用令牌）。"""
    now = now or time.time()
    recovered = []
    for token_id, token in _fold_tokens().items():
        if token.get('lifecycle') != 'RESERVED':
            continue
        reservation = token.get('reservation') or {}
        reserved_at = parse_iso(reservation.get('at', ''))
        pid = reservation.get('pid')
        if now - reserved_at < timeout_s:
            continue
        if pid and _pid_alive(pid):
            continue
        try:
            with token_lock(token_id):
                current = get_token(token_id)
                if current.get('lifecycle') != 'RESERVED':
                    continue
                current['consumption'] = 'CONSUMED'
                current['launch'] = 'FAILED_NO_RUN'
                current['lifecycle'] = 'ABANDONED'
                _store(current)
                audit('recover_abandoned', token_id,
                      detail={'reason': 'reserved_timeout', 'pid': pid})
            recovered.append(token_id)
        except ApprovalError:
            continue
    return recovered


# ---------------- CLI ----------------
def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)

    p_issue = sub.add_parser('issue', help='生成一次性训练审批令牌（用户侧）')
    p_issue.add_argument('--config', required=True, help='run 配置 JSON（含 approval 块）')
    p_issue.add_argument('--model', default=None, help='resume 模型路径（fresh 运行可不填）')
    p_issue.add_argument('--ttl-hours', type=float, default=DEFAULT_TTL_HOURS)
    p_issue.add_argument('--issued-by', default='USER')

    p_show = sub.add_parser('show', help='查看令牌状态')
    p_show.add_argument('--token', required=True)

    p_check = sub.add_parser('check', help='只校验启动请求（不消费令牌、不启动训练）')
    p_check.add_argument('--token', required=True)
    p_check.add_argument('--config', required=True)
    p_check.add_argument('--runtime', default=None,
                         help='运行时参数 JSON 字符串或文件路径；替代各 --runtime-* 参数')
    p_check.add_argument('--workers', type=int)
    p_check.add_argument('--episode-seconds', type=float)
    p_check.add_argument('--steps', type=int)
    p_check.add_argument('--device', default='cpu')
    p_check.add_argument('--resume', default=None)
    p_check.add_argument('--fresh', action='store_true')
    p_check.add_argument('--reward-version', default=None)
    p_check.add_argument('--action-space-version', default=None)
    p_check.add_argument('--obs-version', default=None)
    p_check.add_argument('--game-executable', default=None)

    p_recover = sub.add_parser('recover', help='回收超时 RESERVED 令牌（标记 ABANDONED）')
    p_recover.add_argument('--timeout-seconds', type=float, default=RESERVED_TIMEOUT_S)

    args = ap.parse_args(argv)
    try:
        if args.command == 'issue':
            token = issue_token(args.config, args.model, args.ttl_hours, args.issued_by)
            _print({'ok': True, 'token_id': token['token_id'],
                    'path': 'reports/dsh-learning/manager/approvals/tokens.jsonl',
                    'target': token['target'], 'expires_at': token['expires_at'],
                    'config_hash': token['config_hash'],
                    'model': token['model'], 'budget': token['budget']})
            return 0
        if args.command == 'show':
            _print(get_token(args.token))
            return 0
        if args.command == 'check':
            if args.runtime:
                runtime_obj = (load_config(args.runtime) if pathlib.Path(args.runtime).exists()
                               else json.loads(args.runtime))
                if 'config_path' not in runtime_obj:
                    runtime_obj['config_path'] = str(pathlib.Path(args.config).resolve())
            else:
                config = load_config(args.config)
                approval = config.get('approval') or {}
                runtime_obj = {
                    'workers': args.workers if args.workers is not None else approval.get('workers'),
                    'episode_seconds': (args.episode_seconds if args.episode_seconds is not None
                                        else approval.get('episode_seconds')),
                    'steps': args.steps, 'device': args.device,
                    'resume': args.resume, 'fresh': args.fresh,
                    'reward_version': args.reward_version or approval.get('reward_version'),
                    'action_space_version': (args.action_space_version
                                             or approval.get('action_space_version')),
                    'obs_version': args.obs_version or approval.get('obs_version'),
                    'config_path': str(pathlib.Path(args.config).resolve()),
                    'game_executable': args.game_executable,
                }
            result = validate_launch_request(args.token, args.config, runtime_obj)
            _print({'ok': True, 'validated': result})
            return 0
        if args.command == 'recover':
            recovered = recover_stale(timeout_s=args.timeout_seconds)
            _print({'ok': True, 'recovered': recovered})
            return 0
    except ApprovalError as exc:
        _print({'ok': False, 'code': exc.code, 'message': str(exc), 'details': exc.details})
        audit('reject', getattr(exc, 'token_id', None), detail={'code': exc.code})
        return 2
    except Exception as exc:  # noqa: BLE001
        _print({'ok': False, 'code': 'UNEXPECTED', 'message': repr(exc)})
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
