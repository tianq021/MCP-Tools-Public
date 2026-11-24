"""Generate the V4 reward configs (full game + combat stage) from the SPECS contract.

Run once before a V4 training session:

    F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts/dsh_rl/make_v4_rewards.py

The V3 configs are preserved by ``backups/rewards-v3-pre-v4-*`` (created by the
implementation step); this script only writes ``config/rewards.json`` and
``config/rewards-combat.json``.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from dsh_rl import reward_config as R  # noqa: E402

# V4 explicit overrides on top of the SPECS defaults.  Keys absent here keep the
# SPECS default (score/unit/caps) and stay enabled in the full-game config.
V4_OVERRIDES = {
    'opponent_hp': dict(score=2.0, unit=100.0),
    'killed_hp_credit': dict(score=2.0, unit=100.0),
    'enemy_kill_infantry': dict(score=6.0, unit=1.0),
    'enemy_kill_vehicle': dict(score=12.0, unit=1.0),
    'enemy_building_destroy': dict(score=40.0, unit=1.0),
    'first_kill': dict(score=5.0, unit=1.0, max_events=1),
    'first_building_destroy': dict(score=10.0, unit=1.0, max_events=1),
    'own_hp': dict(score=-0.6, unit=100.0, episode_cap=20.0),
    'victory': dict(score=500.0, unit=1.0, max_events=1),
    'defeat': dict(score=-300.0, unit=1.0, max_events=1),
    'timeout': dict(score=-50.0, unit=1.0, max_events=1),   # experiment item
    'advance': dict(score=0.1, unit=1.0, step_cap=0.3, episode_cap=20.0),
    'first_advance': dict(score=5.0, unit=1.0, max_events=1),
    'first_enemy_sight': dict(score=2.0, unit=1.0, max_events=1),
    'first_damage': dict(score=3.0, unit=1.0, max_events=1),
    'attack': dict(score=0.1, unit=1.0, step_cap=0.1, episode_cap=1.0),
    'time': dict(score=-0.002, unit=1.0),
}

# Combat stage: economy/exploration terms are disabled, killing/building terms on.
COMBAT_ENABLED = {
    'opponent_hp', 'killed_hp_credit', 'enemy_kill_infantry', 'enemy_kill_vehicle',
    'enemy_building_destroy', 'first_kill', 'first_building_destroy',
    'own_hp', 'invalid', 'victory', 'defeat', 'time', 'advance', 'first_advance',
    'first_enemy_sight', 'attack', 'first_damage',
}
# Timeout is an independent experiment: never enabled by default (needs a
# controlled A/B run before it can influence training).
EXPERIMENT_DISABLED = {'timeout'}


def build(combat_stage):
    data = R.defaults()
    for key, override in V4_OVERRIDES.items():
        data['rules'][key].update(override)
    for key in EXPERIMENT_DISABLED:
        data['rules'][key]['enabled'] = False
    if combat_stage:
        for key in data['rules']:
            data['rules'][key]['enabled'] = key in COMBAT_ENABLED
    return data


def dump_table(data):
    lines = [f"{'project':26s} {'enabled':8s} {'score':>8s} {'unit':>8s} "
             f"{'step_cap':>9s} {'ep_cap':>8s} {'max_ev':>7s}"]
    for key, rule in data['rules'].items():
        lines.append(f"{key:26s} {str(rule['enabled']):8s} {rule['score']:8.2f} "
                     f"{rule['unit']:8.1f} {str(rule['step_cap']):>9s} "
                     f"{str(rule['episode_cap']):>8s} {str(rule['max_events']):>7s}")
    return "\n".join(lines)


def main():
    full = build(combat_stage=False)
    combat = build(combat_stage=True)
    full_rev = R.save(full, ROOT / 'config/rewards.json')
    combat_rev = R.save(combat, ROOT / 'config/rewards-combat.json')
    summary = {
        'schema_version': 1,
        'reward_version': 'full_configurable_v4',
        'generated_by': 'scripts/dsh_rl/make_v4_rewards.py',
        'full_config': {'path': 'config/rewards.json', 'revision': full_rev},
        'combat_config': {'path': 'config/rewards-combat.json', 'revision': combat_rev},
        'rules': combat['rules'],
    }
    out = ROOT / 'reports' / 'v4-reward-config.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('full-game config revision:', full_rev)
    print('combat config revision   :', combat_rev)
    print('\n=== combat-stage V4 table ===')
    print(dump_table(combat))
    print('\nwrote', ROOT / 'config/rewards.json')
    print('wrote', ROOT / 'config/rewards-combat.json')
    print('wrote', out)


if __name__ == '__main__':
    main()
