"""共享工具：路径、哈希、JSON 读写、当前环境契约（只读）。

本模块只做本地文件读取/统计，不启动游戏、不训练、不修改任何模型。
"""
import hashlib
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
REPORTS_DIR = ROOT / 'reports' / 'dsh-learning'
RL_DIR = ROOT / 'reports' / 'rl'

# 当前工程内“标准评测环境”的参考运行：用于读取 catalog sha 等只读指纹。
REFERENCE_RUN = RL_DIR / 'stage-A-round1-20261030-153225'


def ensure_dir(path):
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_json(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def save_json_atomic(path, data):
    path = pathlib.Path(path)
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
    return path


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def rel(path):
    try:
        return str(pathlib.Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def file_info(path):
    path = pathlib.Path(path)
    if not path.exists():
        return {'exists': False, 'path': rel(path)}
    stat = path.stat()
    return {'exists': True, 'path': rel(path), 'size': stat.st_size,
            'mtime': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(stat.st_mtime)),
            'sha256': sha256_file(path)}


def _parse_reward_version():
    """从源码读取 PPO 环境奖励版本（避免导入重量级 env 依赖）。"""
    src = ROOT / 'scripts/dsh_rl/full_env.py'
    if not src.exists():
        return None
    m = re.search(r'^\s*reward_version\s*=\s*"([^"]+)"', src.read_text(encoding='utf-8'), re.M)
    return m.group(1) if m else None


def current_contract():
    """读取当前代码的观测/动作/奖励契约（只读，尽力而为）。

    返回字段包含 source 说明，便于报告区分“代码实测”与“文件记录”。
    """
    sys.path.insert(0, str(SCRIPTS))
    out = {'action_space_version': None, 'action_n': None, 'unit_slots': None,
           'target_slots': None, 'obs_dim': None, 'reward_version': _parse_reward_version(),
           'catalog_sha256': None, 'source': {}}
    try:
        from dsh_rl import full_spec as S  # noqa: WPS433
        out.update(action_space_version=S.ACTION_SPACE_VERSION, action_n=S.ACTION_N,
                   unit_slots=S.UNIT_SLOTS, target_slots=S.TARGET_SLOTS,
                   target_candidate_range=S.DEFAULT_TARGET_CANDIDATE_RANGE,
                   target_nearest_k=S.DEFAULT_TARGET_NEAREST_K,
                   history_dim=getattr(S, 'HISTORY_DIM', None),
                   extra_dim=getattr(S, 'EXTRA_DIM', None))
        out['source']['full_spec'] = 'scripts/dsh_rl/full_spec.py (imported)'
    except Exception as exc:  # noqa: BLE001 - 兼容性报告需如实记录失败
        out['source']['full_spec'] = f'import failed: {exc!r}'
    try:
        sys.path.insert(0, str(SCRIPTS / 'dsh_train'))
        import spec_v2  # noqa: WPS433
        out['features_v2_obs_dim'] = int(spec_v2.OBS_DIM)
        if out.get('extra_dim') is not None:
            out['obs_dim'] = (int(spec_v2.OBS_DIM) + int(out['extra_dim'])
                              + int(out.get('history_dim') or 0))
        out['source']['spec_v2'] = "scripts/dsh_train/spec_v2.py (imported)"
    except Exception as exc:  # noqa: BLE001
        out['source']['spec_v2'] = f'import failed: {exc!r}'
    contract_file = REFERENCE_RUN / 'action-contract.json'
    data = load_json(contract_file)
    if data:
        out['catalog_sha256'] = data.get('catalog_sha256')
        out['source']['catalog'] = rel(contract_file)
    return out
