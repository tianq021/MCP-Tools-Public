"""真实对局：去掉所有脚本化设置，让模型在普通 1v1 遭遇战中自主行动。

- 无脚本预部署（setup_mode='none'）：从标准开局（1 MCV + 8 单位）开始；
- 无课程掩码（power/cap/mcv 全部关闭），使用完整原生掩码（301 动作，含 DEPLOY/移动/攻击）；
- 真实 1v1：2peaks 地图，1 个人类模型 + 1 个 Easy AI（复用 config/spawn.ini 的真实遭遇战配置）；
- 模型逐步决策，直到真实胜负结束或达到墙钟上限；记录全过程关键状态。

用法：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\play_real_match.py \\
      --model experiments\\build_training_v2\\build_training_v2_model\\model.zip \\
      --out reports\\build_training\\real_match_v1 --max-minutes 8
"""
import argparse
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
THIS_DIR = pathlib.Path(__file__).resolve().parent
for path in (str(THIS_DIR), str(SCRIPTS), str(SCRIPTS / 'dsh_train'),
             str(ROOT / 'vendor' / 'pyra2yr')):
    if path not in sys.path:
        sys.path.insert(0, path)

import numpy as np  # noqa: E402
import gymnasium as gym  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402

import build_env_v2 as BE2  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402

GAME_DIR = ROOT / 'test-game-build'
MATCH_SPAWN = ROOT / 'config' / 'spawn.ini'      # 真实 1v1 遭遇战配置（1 human + 1 AI）
MAP_SOURCE = ROOT / 'experiments' / 'build_training_v2' / 'map' / 'base_2peaks.map'


def prepare_match():
    """准备真实对局配置：普通 1v1 遭遇战（无脚本化预部署）。"""
    GAME_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MATCH_SPAWN, GAME_DIR / 'spawn.ini')
    shutil.copyfile(MAP_SOURCE, GAME_DIR / 'spawnmap.ini')
    return {'spawn_ini': str(MATCH_SPAWN), 'map': str(MAP_SOURCE)}


class SingleEnv(gym.Wrapper):
    """把环境包成 MaskablePPO 可直接 predict 的单环境。"""

    def __init__(self, env):
        super().__init__(env)

    def action_masks(self):
        return self.env.action_masks()


def summarize(obs, env):
    own = obs.get('own_objects') or []
    buildings = [o for o in own if o.get('abstract') == 'ABSTRACT_TYPE_BUILDING'
                 and o.get('on_map') and not o.get('in_limbo')]
    units = [o for o in own if o.get('abstract') in
             ('ABSTRACT_TYPE_INFANTRY', 'ABSTRACT_TYPE_UNIT')
             and o.get('on_map') and not o.get('in_limbo')]
    enemies = obs.get('visible_enemies') or []
    hero = next((h for h in obs.get('houses') or [] if h.get('current_player')), {})
    return {
        'money': int(obs.get('money') or 0),
        'buildings': len(buildings),
        'building_types': sorted({str(o.get('type_name')) for o in buildings}),
        'units': len(units),
        'unit_types': sorted({str(o.get('type_name')) for o in units}),
        'visible_enemies': len(enemies),
        'power_output': int(hero.get('power_output') or 0),
        'power_drain': int(hero.get('power_drain') or 0),
        'defeated': bool(hero.get('defeated')),
        'is_winner': bool(hero.get('is_winner')),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--game-dir', default=str(GAME_DIR))
    ap.add_argument('--port', type=int, default=14531)
    ap.add_argument('--instance', type=int, default=8)
    ap.add_argument('--decision-seconds', type=float, default=0.5)
    ap.add_argument('--max-minutes', type=float, default=8.0)
    ap.add_argument('--max-steps', type=int, default=2000)
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = (ROOT / out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    prep = prepare_match()
    report = {'mode': 'real_match_no_scripts', 'model': str(pathlib.Path(a.model).resolve()),
              'prepared': prep, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
              'settings': {'setup_mode': 'none', 'curriculum_masks': 'off',
                           'unit_control': 'full_native', 'enemies': '1 Easy AI',
                           'decision_seconds': a.decision_seconds}}
    log_path = out / 'real_match_steps.jsonl'
    env = None
    raw = None
    try:
        raw = BE2.RA2BuildEnvV2(
            out / 'episodes', max_seconds=0,  # 0 = 不按时间截断，打真实对局
            decision_seconds=a.decision_seconds,
            game_dir=str(pathlib.Path(a.game_dir).resolve()
                         if not pathlib.Path(a.game_dir).is_absolute() else a.game_dir),
            port=a.port, instance=a.instance, kill_stale=True,
            setup_mode='none', unit_control_mode='full',
            power_curriculum_masks=False, cap_curriculum_masks=False,
            reward_config_path=str(ROOT / 'training' / 'build_curriculum'
                                   / 'reward_build_v2_8.json'))
        env = SingleEnv(raw)
        model = MaskablePPO.load(str(pathlib.Path(a.model).resolve()), env=env,
                                 device='cpu')
        obs, info = env.reset()
        t0 = time.monotonic()
        steps = 0
        last_summary = summarize(info.get('observation') or info, raw)
        records = []
        outcome = {'kind': 'running'}
        while steps < a.max_steps:
            if time.monotonic() - t0 > a.max_minutes * 60.0:
                outcome = {'kind': 'time_cap'}
                break
            try:
                masks = env.action_masks()
            except Exception:  # noqa: BLE001
                masks = None
            action, _ = model.predict(obs, action_masks=masks, deterministic=False)
            obs, reward, terminated, truncated, info = env.step(int(action))
            steps += 1
            row = info if isinstance(info, dict) else {}
            cur = summarize(row.get('observation') or row, raw)
            records.append({
                'step': steps, 'action': int(action),
                'selected_intent': row.get('selected_intent'),
                'ordered_roles': row.get('ordered_roles'),
                'reward': float(reward),
                'kind': row.get('kind'),
                'frame': row.get('frame'),
                **{k: cur[k] for k in ('money', 'buildings', 'units',
                                       'visible_enemies', 'power_output',
                                       'power_drain')},
            })
            with log_path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(records[-1], ensure_ascii=False) + '\n')
            if steps % 20 == 0:
                print('[match] step=%d money=%s buildings=%s units=%s enemies=%s power=%s/%s'
                      % (steps, cur['money'], cur['buildings'], cur['units'],
                         cur['visible_enemies'], cur['power_output'], cur['power_drain']),
                      flush=True)
            if terminated or truncated or row.get('kind') in ('win', 'defeat', 'game_end'):
                outcome = {'kind': row.get('kind') or ('win' if terminated else 'truncated'),
                           'terminated': bool(terminated), 'truncated': bool(truncated)}
                break
            last_summary = cur
        # 最终状态
        final_obs = raw.current if raw is not None else {}
        report.update({
            'status': 'finished',
            'steps': steps,
            'duration_s': round(time.monotonic() - t0, 1),
            'outcome': outcome,
            'final': summarize(final_obs, raw),
            'actions': {
                'total': steps,
                'move_orders': sum(1 for r in records
                                   if (r.get('selected_intent') or {}).get('action') == 'MOVE'),
                'attack_orders': sum(1 for r in records
                                     if (r.get('selected_intent') or {}).get('action') == 'ATTACK'),
                'build_orders': sum(1 for r in records
                                    if (r.get('selected_intent') or {}).get('action') == 'PRODUCE'),
                'place_orders': sum(1 for r in records
                                    if (r.get('selected_intent') or {}).get('action') == 'PLACE'),
                'deploy_orders': sum(1 for r in records
                                     if (r.get('selected_intent') or {}).get('action') == 'DEPLOY'),
            },
            'finished_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        })
        (out / 'real_match_result.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('[match] RESULT', json.dumps({
            'steps': report['steps'], 'duration_s': report['duration_s'],
            'outcome': report['outcome'], 'final': report['final'],
            'actions': report['actions']}, ensure_ascii=False), flush=True)
        return 0
    finally:
        if env is not None:
            try:
                env.close()
            except BaseException:  # noqa: BLE001
                pass


if __name__ == '__main__':
    sys.exit(main())
