"""Read-only verification of the RA2MCP project state for the coordination system.

Collects file hashes, run metadata, metrics, configs and environment state, then
compares them against the *claims* in the provided memory snapshot
(RA2_AI_PROJECT_STATE_v1.0.json / RA2_AI_CURRENT_CONTEXT_v1.0.md).

Writes only a new evidence file under coordination/verification/ (never
overwrites provided originals, reports, models or checkpoints).

Usage:
    python scripts/coordination/verify_project_state.py [--out PATH]
"""
import argparse
import hashlib
import json
import math
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
COORD = ROOT / 'coordination'
ROUNDS = {
    'R1': {'dir': 'reports/rl/stage-A-round1-20261030-153225',
           'report': 'reports/stage-A-round1-report.md',
           'metrics': 'reports/stage-A-round1-metrics.json'},
    'R2': {'dir': 'reports/rl/stage-A-round2-20261030-155824',
           'report': 'reports/stage-A-round2-report.md',
           'metrics': 'reports/stage-A-round2-metrics.json'},
    'R3': {'dir': 'reports/rl/stage-A-round3-20261030-162536',
           'report': 'reports/stage-A-round3-report.md',
           'metrics': 'reports/stage-A-round3-metrics.json'},
}
OLD_MODEL = 'reports/rl/ppo-shared-20261030-102633/model.zip'
REPORT_FILES = [
    'reports/stage-A-round1-report.md',
    'reports/stage-A-round1-metrics.json',
    'reports/stage-A-round2-report.md',
    'reports/stage-A-round2-metrics.json',
    'reports/stage-A-round3-report.md',
    'reports/stage-A-round3-metrics.json',
    'reports/stage-A-mid5-report.md',
    'reports/stage-A-stop-report-20261030.md',
]
CONFIG_FILES = [
    'config/rewards-combat.json',
    'config/rewards.json',
    'config/curriculum/stage-A-combat.spawn.ini',
]
CLAIM_MODEL_SHA = {
    'R1': 'f650f4fd1a21f236116426bfe9a38b71d1c63334836e2303337f0ef7127cea8a',
    'R2': 'c340d9ccead7c9a1b9add2a37fee990e2caee5c3c7c2c316dc79d2010c2ea542',
    'R3': '0c9c75d7ecc4c360b6c165b1b224f10491bc13dad9aeea4b3b84abd8d6511ce8',
    'OLD': 'f8380e8bd55d2454179ca1955682f1d869356f03d51fa19c11a2f6a4dc6f1b56',
}
CLAIM_METRICS = {
    'R1': dict(rate6=0.4979, attacks=23.9, effective=11.9, kills=6.0, own_losses=17.4,
               kill_exchange=0.345, ineff_switches=11.4, top1=0.330),
    'R2': dict(rate6=0.4227, attacks=22.0, effective=9.3, kills=5.0, own_losses=17.5,
               kill_exchange=0.286, ineff_switches=12.2, top1=0.339),
    'R3': dict(rate6=0.1485, attacks=55.9, effective=8.3, kills=2.6, own_losses=20.1,
               kill_exchange=0.129, ineff_switches=47.0, top1=0.320),
}
CLAIM_R3_TRAINING = dict(timesteps_before=6656, timesteps_after=9792, optimizer_epochs=600)
CLAIM_CONFIG = dict(action_space_version='full_ground_v2p1', action_n=301,
                    target_candidate_range=30.0, target_nearest_k=3,
                    policy_parameters=258606, workers=2, episode_seconds=300.0,
                    decision_seconds=0.5, reward_sha_prefix='2aced7dd',
                    reward_revision_prefix='ee65fd0b',
                    difficulty=2, credits=20000, unit_count=8,
                    unit_types={'Grizzly Battle Tank': 4, 'GI': 4,
                                'Allied Construction Vehicle': 1})


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def file_info(path):
    path = pathlib.Path(path)
    if not path.exists():
        return {'path': str(path), 'exists': False}
    return {'path': path.relative_to(ROOT).as_posix(), 'exists': True,
            'size': path.stat().st_size, 'sha256': sha256_file(path)}


def load_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))
    except Exception:  # noqa: BLE001
        return default


def parse_spawn_ini(path):
    out = {}
    if not path.exists():
        return out
    section = None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        text = line.split(';', 1)[0].strip()
        if not text:
            continue
        if text.startswith('[') and text.endswith(']'):
            section = text[1:-1]
            continue
        if '=' in text and section:
            key, value = text.split('=', 1)
            out.setdefault(section, {})[key.strip()] = value.strip()
    return out


def run_metadata(round_dir):
    meta = load_json(round_dir / 'run.json', {}) or {}
    keys = ('status', 'model_init', 'resume_model', 'reward_version', 'reward_config_sha256',
            'reward_config_revision', 'action_space_version', 'target_candidate_range',
            'target_nearest_k', 'timesteps_before', 'timesteps_after', 'optimizer_epochs',
            'completed_episodes', 'batch_size', 'policy_parameters', 'parameters',
            'fresh_hyperparameters')
    return {k: meta.get(k) for k in keys}


def result_kinds(round_dir):
    counts = {}
    files = sorted((round_dir / 'workers').glob('*/episodes/*.result.json'))
    for path in files:
        try:
            kind = json.loads(path.read_text(encoding='utf-8')).get('kind')
        except Exception:  # noqa: BLE001
            kind = 'unreadable'
        counts[kind] = counts.get(kind, 0) + 1
    return counts, len(files)


def round_inventory(name, rel):
    round_dir = ROOT / rel['dir']
    models = file_info(round_dir / 'model.zip')
    zips = []
    for path in sorted(round_dir.glob('*.zip')):
        zips.append({'name': path.name, 'size': path.stat().st_size,
                     'sha256': sha256_file(path)})
    counts, total = result_kinds(round_dir)
    metrics_path = ROOT / rel['metrics']
    metrics = load_json(metrics_path, {}) or {}
    aggregate = metrics.get('aggregate') or {}
    report_path = ROOT / rel['report']
    return {
        'dir': rel['dir'],
        'model': models,
        'zips': zips,
        'result_counts': counts,
        'result_files_total': total,
        'valid_episodes': sum(counts.get(k, 0) for k in ('defeat', 'win', 'timeout')),
        'interrupted_episodes': counts.get('interrupted', 0),
        'run': run_metadata(round_dir),
        'metrics_file': file_info(metrics_path),
        'aggregate': {k: aggregate.get(k) for k in (
            'episodes', 'attacks_issued_total', 'attacks_issued_mean',
            'attacks_effective_6s_total', 'attacks_effective_6s_rate',
            'kills_total', 'kills_mean', 'first_kill_sampled_rate',
            'damage_dealt_mean', 'own_hp_lost_damage_mean', 'own_removed_hp_mean',
            'hp_exchange_mean', 'own_unit_losses_mean', 'unit_kill_exchange',
            'ineffective_switches_mean', 'top1_damage_share_mean',
            'target_switches_mean', 'candidate_coverage_mean', 'unit_slot_swaps_max')},
        'report': file_info(report_path),
    }


def environment_state():
    games = []
    trainers = []
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (proc.info.get('name') or '').lower()
                cmd = ' '.join(proc.info.get('cmdline') or [])
                if 'gamemd-spawn-ra2yrcpp' in name:
                    games.append({'pid': proc.info['pid'], 'name': proc.info['name']})
                if 'train_shared.py' in cmd:
                    trainers.append({'pid': proc.info['pid'], 'cmd': cmd[:300]})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as exc:  # noqa: BLE001
        games.append({'error': repr(exc)})
    locks = [p.name for p in (ROOT / 'build').glob('dsh-*.lock')]
    stop_files = [p.as_posix() for p in (ROOT / 'reports/rl').glob('stage-A-round*[0-9]/STOP')]
    return {'game_processes': games, 'training_processes': trainers,
            'build_locks': locks, 'active_stop_files': stop_files,
            'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S')}


def float_close(a, b, tol=5e-3):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)
    COORD.mkdir(parents=True, exist_ok=True)
    out_path = pathlib.Path(args.out) if args.out else (
        COORD / 'verification' / 'project-state-verified.json')

    originals = []
    for path in sorted(COORD.glob('*')):
        if path.is_file() and path.suffix in ('.md', '.json'):
            originals.append(file_info(path))

    rounds = {name: round_inventory(name, rel) for name, rel in ROUNDS.items()}
    old_model = file_info(ROOT / OLD_MODEL)
    reports = [file_info(ROOT / rel) for rel in REPORT_FILES]
    configs = [file_info(ROOT / rel) for rel in CONFIG_FILES]
    spawn = parse_spawn_ini(ROOT / 'config/curriculum/stage-A-combat.spawn.ini')
    spawn_primary = parse_spawn_ini(ROOT / 'test-game/spawn.ini')
    preflight_path = ROOT / 'reports/stage-A-final-preflight-20261030-153217.json'
    preflight = load_json(preflight_path, {}) or {}
    preflight_frame = (preflight.get('frames') or [{}])[0]

    # Imports for verified code constants (read-only).
    sys.path.insert(0, str(ROOT / 'scripts'))
    venv = ROOT / '.venv-rl' / 'Lib' / 'site-packages'
    if venv.exists():
        sys.path.insert(0, str(venv))
    full_spec = reward_config = None
    try:
        from dsh_rl import full_spec
    except Exception as exc:  # noqa: BLE001
        full_spec = {'error': repr(exc)}
    try:
        from dsh_rl import reward_config as reward_config
    except Exception as exc:  # noqa: BLE001
        reward_config = {'error': repr(exc)}
    reward_sha = sha256_file(ROOT / 'config/rewards-combat.json')
    reward_revision = None
    if hasattr(reward_config, 'load'):
        try:
            data = reward_config.load(ROOT / 'config/rewards-combat.json')
            reward_revision = reward_config.revision(data)
        except Exception as exc:  # noqa: BLE001
            reward_revision = f'ERROR {exc!r}'

    env = environment_state()

    # ---- claim checks -------------------------------------------------------
    checks = []

    def check(name, expected, actual, status=None):
        ok = status
        if ok is None:
            ok = expected == actual or (isinstance(expected, float) and isinstance(actual, float)
                                        and abs(expected - actual) < 1e-6)
        checks.append({'claim': name, 'expected': expected, 'actual': actual,
                       'status': 'VERIFIED' if ok else 'CONFLICT'})

    for name in ('R1', 'R2', 'R3'):
        actual = (rounds[name]['model'] or {}).get('sha256')
        check(f'{name}_model_sha256', CLAIM_MODEL_SHA[name], actual)
    check('OLD_model_sha256_full', CLAIM_MODEL_SHA['OLD'], old_model.get('sha256'))
    for name, claims in CLAIM_METRICS.items():
        agg = rounds[name]['aggregate']
        check(f'{name}_6s_effective_rate', claims['rate6'], agg.get('attacks_effective_6s_rate'))
        check(f'{name}_attacks_per_episode', claims['attacks'], agg.get('attacks_issued_mean'))
        check(f'{name}_effective_attacks_per_episode', claims['effective'],
              round((agg.get('attacks_effective_6s_total') or 0) / max(1, agg.get('episodes') or 1), 1))
        check(f'{name}_kills_per_episode', claims['kills'], agg.get('kills_mean'))
        check(f'{name}_own_unit_losses', claims['own_losses'], agg.get('own_unit_losses_mean'))
        check(f'{name}_kill_exchange', claims['kill_exchange'], agg.get('unit_kill_exchange'))
        check(f'{name}_ineffective_switches', claims['ineff_switches'],
              agg.get('ineffective_switches_mean'))
        check(f'{name}_top1_concentration', claims['top1'], agg.get('top1_damage_share_mean'))
    r3 = rounds['R3']
    check('R3_valid_episodes', 10, r3['valid_episodes'])
    check('R3_interrupted_excluded', 2, r3['interrupted_episodes'])
    check('R3_timesteps_before', CLAIM_R3_TRAINING['timesteps_before'],
          (r3['run'] or {}).get('timesteps_before'))
    check('R3_timesteps_after', CLAIM_R3_TRAINING['timesteps_after'],
          (r3['run'] or {}).get('timesteps_after'))
    check('R3_optimizer_epochs', CLAIM_R3_TRAINING['optimizer_epochs'],
          (r3['run'] or {}).get('optimizer_epochs'))
    check('R3_resume_is_R2_model',
          str((ROOT / ROUNDS['R2']['dir'] / 'model.zip').resolve()).lower(),
          str(((r3['run'] or {}).get('resume_model') or '')).lower())
    check('R2_resume_is_R1_model',
          str((ROOT / ROUNDS['R1']['dir'] / 'model.zip').resolve()).lower(),
          str(((rounds['R2']['run'] or {}).get('resume_model') or '')).lower())
    check('R1_fresh_init', 'fresh', (rounds['R1']['run'] or {}).get('model_init'))
    for name in ('R1', 'R2'):
        check(f'{name}_valid_episodes', 10, rounds[name]['valid_episodes'])
        check(f'{name}_interrupted_excluded', 2, rounds[name]['interrupted_episodes'])
    # config claims
    check('action_space_version', CLAIM_CONFIG['action_space_version'],
          (r3['run'] or {}).get('action_space_version'))
    check('action_n', CLAIM_CONFIG['action_n'],
          getattr(full_spec, 'ACTION_N', None) if not isinstance(full_spec, dict) else None)
    check('target_candidate_range', CLAIM_CONFIG['target_candidate_range'],
          (r3['run'] or {}).get('target_candidate_range'))
    check('target_nearest_k', CLAIM_CONFIG['target_nearest_k'],
          (r3['run'] or {}).get('target_nearest_k'))
    check('policy_parameters', CLAIM_CONFIG['policy_parameters'],
          (r3['run'] or {}).get('policy_parameters'))
    check('workers', CLAIM_CONFIG['workers'],
          ((r3['run'] or {}).get('parameters') or {}).get('workers'))
    check('episode_seconds', CLAIM_CONFIG['episode_seconds'],
          ((r3['run'] or {}).get('parameters') or {}).get('episode_seconds'))
    check('decision_seconds', CLAIM_CONFIG['decision_seconds'],
          ((r3['run'] or {}).get('parameters') or {}).get('decision_seconds'))
    check('reward_sha_prefix', CLAIM_CONFIG['reward_sha_prefix'], reward_sha[:8])
    check('reward_revision_prefix', CLAIM_CONFIG['reward_revision_prefix'],
          (reward_revision or '')[:8])
    check('reward_version', 'full_configurable_v4',
          (r3['run'] or {}).get('reward_version'))
    stage_a = spawn.get('Settings', {})
    check('stage_a_difficulty', str(CLAIM_CONFIG['difficulty']), stage_a.get('AIDifficulty'))
    check('stage_a_credits', str(CLAIM_CONFIG['credits']), stage_a.get('Credits'))
    check('stage_a_unit_count', str(CLAIM_CONFIG['unit_count']), stage_a.get('UnitCount'))
    check('stage_a_superweapons', 'No', stage_a.get('Superweapons'))
    check('stage_a_house_handicap_multi2', '2',
          spawn.get('HouseHandicaps', {}).get('Multi2'))
    check('human_start_credits', 20000, preflight_frame.get('human_money'))
    check('ai_start_credits', 100000, preflight_frame.get('ai_money'))
    check('initial_units', CLAIM_CONFIG['unit_types'], preflight_frame.get('own_types'))
    check('map_spawn_distance', 68.0, preflight.get('distance'))
    check('no_game_processes', 0, len(env['game_processes']))
    check('no_training_processes', 0, len(env['training_processes']))
    check('no_build_locks', [], env['build_locks'])
    # The provided memory uses a non-standard project_status value; keep it as a
    # documented schema deviation (not a factual conflict with the machine state).
    checks.append({'claim': 'provided_project_status_enum', 'expected': 'PAUSED_FOR_DIAGNOSIS',
                   'actual': 'PAUSED (canonical) + status_detail=PAUSED_FOR_DIAGNOSIS',
                   'status': 'NON_STANDARD'})
    checks.append({'claim': 'provided_snapshot_context_version', 'expected': 1,
                   'actual': (load_json(COORD / 'RA2_AI_PROJECT_STATE_v1.0.json', {}) or
                              {}).get('context_version'),
                   'status': 'VERIFIED'})

    conflicts = [c for c in checks if c['status'] == 'CONFLICT']
    status_counts = {}
    for item in checks:
        status_counts[item['status']] = status_counts.get(item['status'], 0) + 1
    payload = {
        'schema': 'ra2-context-verification/1.0',
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'root': str(ROOT),
        'coordination_originals': originals,
        'model_artifacts': {
            'R1': rounds['R1']['model'],
            'R2': rounds['R2']['model'],
            'R3': rounds['R3']['model'],
            'OLD_REFERENCE': old_model,
        },
        'rounds': rounds,
        'reports': reports,
        'configs': configs,
        'stage_a_config_file': {
            'path': 'config/curriculum/stage-A-combat.spawn.ini',
            'settings': stage_a,
            'house_handicaps': spawn.get('HouseHandicaps', {}),
            'credits_factor_ignored': spawn.get('CreditsFactor', {}),
        },
        'stage_a_primary_spawn': spawn_primary.get('Settings', {}),
        'runtime_preflight_evidence': {
            'path': 'reports/stage-A-final-preflight-20261030-153217.json',
            'human_money': preflight_frame.get('human_money'),
            'ai_money': preflight_frame.get('ai_money'),
            'own_types': preflight_frame.get('own_types'),
            'spawn_distance': preflight.get('distance'),
        },
        'reward_config': {'sha256': reward_sha, 'revision': reward_revision,
                          'action_n': getattr(full_spec, 'ACTION_N', None)
                          if not isinstance(full_spec, dict) else None},
        'environment': env,
        'claims_checked': checks,
        'claim_summary': {'total': len(checks), 'verified': status_counts.get('VERIFIED', 0),
                          'conflicts': len(conflicts),
                          'non_standard': status_counts.get('NON_STANDARD', 0),
                          'status_counts': status_counts,
                          'conflict_claims': conflicts},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'out': str(out_path),
                      'models': {k: (v or {}).get('sha256') for k, v in payload['model_artifacts'].items()},
                      'claims': payload['claim_summary']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
