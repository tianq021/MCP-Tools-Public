"""Summarise why recent training episodes ended, from recorded episode data.

Usage (from E:\\RA2MCP):
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts\\analyze_episodes.py
  ... --run reports/rl/<run-dir>

Reads episodes/*.result.json and transitions.jsonl, then writes
<run>/failure-analysis.md with per-episode causes and action mix.
"""
import argparse
import json
import pathlib
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_run(run_dir=None):
    if run_dir:
        return pathlib.Path(run_dir)
    active = json.loads((ROOT / 'reports/rl/active-run.json').read_text(encoding='utf-8-sig'))
    return pathlib.Path(active['run_dir'])


def read_results(run):
    rows = []
    for path in sorted(run.glob('episodes/*.result.json'), key=lambda p: p.stat().st_mtime):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        metrics = data.get('metrics') or {}
        stats = data.get('stats') or {}
        rows.append({
            'episode': data['episode_id'],
            'short': data['episode_id'][-6:],
            'kind': data.get('kind', '?'),
            'elapsed': float(data.get('elapsed_s') or 0),
            'damage_dealt': int(metrics.get('damage_dealt') or 0),
            'own_hp_lost': int(metrics.get('own_hp_lost') or 0),
            'buildings_peak': int(metrics.get('buildings_peak') or 0),
            'units_peak': int(metrics.get('units_peak') or 0),
            'first_damage_at': metrics.get('first_damage_at_s'),
            'invalid': int(stats.get('invalid_orders') or 0),
        })
    return rows


def read_transitions(run):
    actions = defaultdict(Counter)
    rewards = defaultdict(Counter)
    path = run / 'transitions.jsonl'
    if not path.exists():
        return actions, rewards
    with path.open(encoding='utf-8-sig', errors='replace') as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            episode = (record.get('episode_id') or '?')[-6:]
            intent = (record.get('selected_intent') or {}).get('action', 'NOOP')
            actions[episode][intent] += 1
            for key, value in (record.get('reward_parts') or {}).items():
                if value:
                    rewards[episode][key] += float(value)
    return actions, rewards


def diagnose(row, action_mix, reward_parts):
    """Return a list of human-readable cause labels for one episode."""
    causes = []
    total = sum(action_mix.values()) or 1
    move_share = action_mix.get('MOVE', 0) / total
    if row['buildings_peak'] == 0 and row['own_hp_lost'] >= 900 and row['units_peak'] <= 1:
        causes.append('基地车没展开就被打掉（没有建造任何建筑）')
    elif row['buildings_peak'] == 0:
        causes.append('整局没有建成任何建筑')
    if row['units_peak'] <= 4 and row['buildings_peak'] > 0:
        causes.append(f"部队太少（峰值 {row['units_peak']} 个），没有成规模的军队")
    if row['buildings_peak'] > 0 and row['own_hp_lost'] > max(100, row['damage_dealt'] * 3):
        causes.append(f"战损悬殊：敌方掉血 {row['damage_dealt']}，己方掉血 {row['own_hp_lost']}")
    shaping = (reward_parts.get('construction_yard', 0) + reward_parts.get('first_building_type', 0)
               + reward_parts.get('first_unit_type', 0) + reward_parts.get('exploration', 0)
               + reward_parts.get('progress', 0))
    if shaping > max(0.0, reward_parts.get('opponent_hp', 0)) + 0.5:
        causes.append('奖励集中在一次性建造/探图，战斗奖励很低')
    if reward_parts.get('exploration', 0) >= 0.49:
        causes.append('探图奖励已刷满（每局上限 0.5）')
    if move_share >= 0.55:
        causes.append(f'MOVE 动作占 {move_share:.0%}，大量决策花在移动上')
    if action_mix.get('PRODUCE', 0) <= 5 and row['elapsed'] > 100:
        causes.append('几乎不生产（PRODUCE 很少）')
    if row['elapsed'] < 200 and row['kind'] == 'defeat':
        causes.append('对局很快结束（快速结束/防线被速推）')
    if not causes:
        causes.append('没有明显异常')
    return causes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', default=None, help='training run directory')
    args = parser.parse_args()
    run = load_run(args.run)
    rows = read_results(run)
    actions, rewards = read_transitions(run)
    if not rows:
        print(f'no finished episodes in {run}')
        return 1
    lines = [f'# 对局失败归因：{run.name}', '']
    lines.append('| 对局 | 结果 | 时长s | 敌方掉血 | 己方掉血 | 建筑峰值 | 单位峰值 | 首次交火s |')
    lines.append('|---|---|---|---|---|---|---|---|')
    for row in rows:
        lines.append(f"| {row['short']} | {row['kind']} | {row['elapsed']:.0f} | "
                     f"{row['damage_dealt']} | {row['own_hp_lost']} | {row['buildings_peak']} | "
                     f"{row['units_peak']} | {row['first_damage_at'] or '—'} |")
    lines.append('')
    for row in rows:
        mix = actions.get(row['short'], Counter())
        reward = rewards.get(row['short'], Counter())
        total = sum(mix.values()) or 1
        top = ', '.join(f'{name}:{count}' for name, count in mix.most_common(5)) or '—'
        lines.append(f"## {row['short']}  ({row['kind']}, {row['elapsed']:.0f}s)")
        lines.append('')
        lines.append(f"- 决策 {total} 步；动作分布：{top}")
        lines.append('- 失败原因：')
        for cause in diagnose(row, mix, reward):
            lines.append(f'  - {cause}')
        if reward:
            parts = ', '.join(f'{key}={value:+.2f}' for key, value in reward.items())
            lines.append(f'- 奖励分项：{parts}')
        lines.append('')
    lines.append('## 数据来源')
    lines.append('')
    lines.append(f'- 对局记录：`{run / "episodes"}`（*.jsonl 逐帧观测/指令、*.result.json 汇总、*.ckpt.json 检查点）')
    lines.append(f"- 决策与奖励：`{run / 'transitions.jsonl'}`")
    lines.append(f'- 训练输出：`{ROOT / "build"}` 下同名 stdout/stderr 日志')
    report = run / 'failure-analysis.md'
    report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    print(f'\nwritten: {report}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
