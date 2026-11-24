"""生成 P0 审批令牌绑定的 run 配置（实验 A / S1），供无人值守训练监督器复用。"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from dsh_learning import fixed_eval_protocol, skill_metrics  # noqa: E402


def build_config(experiment='A', skill='S1', seed=11, workers=6,
                 max_steps=60000, eval_every_steps=2500, eval_episodes=20,
                 episode_seconds=300, reward_version='full_configurable_v4'):
    return {'approval': {
        'experiment': experiment,
        'skill': skill,
        'budget': {'max_steps': int(max_steps),
                   'eval_every_steps': int(eval_every_steps),
                   'eval_episodes': int(eval_episodes)},
        'seed': int(seed),
        'workers': int(workers),
        'episode_seconds': float(episode_seconds),
        'reward_version': reward_version,
        'action_space_version': 'full_ground_v2p1',
        'obs_version': 'features_v2',
        'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
        'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
        'eval_protocol_hash': fixed_eval_protocol.PROTOCOL_HASH,
        'game': {'executable_sha256': '', 'bridge_version': ''},
    }}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True)
    ap.add_argument('--experiment', default='A')
    ap.add_argument('--skill', default='S1')
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--max-steps', type=int, default=60000)
    ap.add_argument('--eval-every', type=int, default=2500)
    ap.add_argument('--eval-episodes', type=int, default=20)
    ap.add_argument('--episode-seconds', type=float, default=300)
    ap.add_argument('--reward-version', default='full_configurable_v4')
    args = ap.parse_args(argv)
    config = build_config(args.experiment, args.skill, args.seed, args.workers,
                          args.max_steps, args.eval_every, args.eval_episodes,
                          args.episode_seconds, args.reward_version)
    path = pathlib.Path(args.out)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
