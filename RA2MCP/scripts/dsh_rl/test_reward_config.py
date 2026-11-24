import json
import pathlib
import tempfile
import unittest

from dsh_rl.reward_config import (SPECS, defaults, RewardLedger, load, save,
                                 validate)
from dsh_rl.full_env import RA2FullEnv, per_unit_advance_delta
from dsh_rl.test_full_control import obj, row

ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestRewardConfig(unittest.TestCase):
    def test_default_scores_and_caps(self):
        ledger = RewardLedger(defaults())
        parts = ledger.apply({'opponent_hp': 100, 'own_hp': 50, 'victory': 1,
                              'exploration': 100, 'income': 10000, 'time': 2})
        self.assertEqual(parts['opponent_hp'], 2)          # V4: 2 / 100 HP
        self.assertAlmostEqual(parts['own_hp'], -.3)       # V4: -0.6 / 100 HP
        self.assertEqual(parts['victory'], 500)
        self.assertAlmostEqual(parts['exploration'], .02)  # step cap 0.02
        self.assertAlmostEqual(parts['income'], .1)
        self.assertAlmostEqual(parts['time'], -.004)
        for _ in range(100):
            ledger.apply({'exploration': 100, 'income': 10000, 'victory': 1})
        self.assertAlmostEqual(ledger.used['exploration'], .8)
        self.assertAlmostEqual(ledger.used['income'], 2)
        self.assertEqual(ledger.used['victory'], 500)

    def test_v4_kill_building_and_first_milestones(self):
        ledger = RewardLedger(defaults())
        parts = ledger.apply({'enemy_kill_infantry': 3, 'enemy_kill_vehicle': 2,
                              'enemy_building_destroy': 1, 'killed_hp_credit': 250,
                              'first_kill': 1, 'first_building_destroy': 1})
        self.assertEqual(parts['enemy_kill_infantry'], 18)
        self.assertEqual(parts['enemy_kill_vehicle'], 24)
        self.assertEqual(parts['enemy_building_destroy'], 40)
        self.assertEqual(parts['killed_hp_credit'], 5)
        self.assertEqual(parts['first_kill'], 5)
        self.assertEqual(parts['first_building_destroy'], 10)
        # first-* milestones score at most once per episode
        again = ledger.apply({'first_kill': 1, 'first_building_destroy': 1})
        self.assertEqual(again['first_kill'], 0)
        self.assertEqual(again['first_building_destroy'], 0)

    def test_own_hp_episode_cap_is_20(self):
        ledger = RewardLedger(defaults())
        parts = ledger.apply({'own_hp': 100000})
        self.assertEqual(parts['own_hp'], -20)
        self.assertEqual(ledger.apply({'own_hp': 100})['own_hp'], 0)

    def test_advance_and_milestones(self):
        ledger = RewardLedger(defaults())
        parts = ledger.apply({'advance': 4, 'first_advance': 1, 'first_enemy_sight': 1})
        self.assertAlmostEqual(parts['advance'], .3)   # 4 cells * 0.1 step cap 0.3
        self.assertEqual(parts['first_advance'], 5)
        self.assertEqual(parts['first_enemy_sight'], 2)
        self.assertAlmostEqual(ledger.apply({'advance': -4})['advance'], -.3)
        self.assertEqual(ledger.apply({'first_advance': 1})['first_advance'], 0)

    def test_legacy_v3_config_is_migrated_with_v4_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'rewards.json'
            legacy = defaults()
            for key in ('killed_hp_credit', 'enemy_kill_infantry', 'enemy_kill_vehicle',
                        'enemy_building_destroy', 'first_kill', 'first_building_destroy',
                        'timeout', 'advance', 'first_advance', 'first_enemy_sight'):
                legacy['rules'].pop(key)
            path.write_text(json.dumps(legacy), encoding='utf-8')
            loaded = load(path)
            for key in ('killed_hp_credit', 'enemy_kill_infantry', 'enemy_kill_vehicle',
                        'enemy_building_destroy', 'first_kill', 'first_building_destroy',
                        'timeout', 'advance'):
                self.assertIn(key, loaded['rules'])
            self.assertEqual(loaded['rules']['advance']['score'], .1)
            self.assertEqual(loaded['rules']['enemy_kill_infantry']['score'], 6)
            unknown = defaults()
            unknown['rules']['mystery_bonus'] = dict(enabled=True, score=1., unit=1.,
                                                     step_cap=None, episode_cap=None,
                                                     max_events=None)
            path.write_text(json.dumps(unknown), encoding='utf-8')
            with self.assertRaises(ValueError):
                load(path)

    def test_shipped_combat_config_is_v4_and_timeout_disabled(self):
        config = load(ROOT / 'config/rewards-combat.json')
        rules = config['rules']
        self.assertEqual(rules['opponent_hp']['score'], 2.0)
        self.assertEqual(rules['own_hp']['score'], -0.6)
        self.assertEqual(rules['own_hp']['episode_cap'], 20.0)
        self.assertEqual(rules['enemy_kill_infantry']['score'], 6.0)
        self.assertEqual(rules['enemy_kill_vehicle']['score'], 12.0)
        self.assertEqual(rules['enemy_building_destroy']['score'], 40.0)
        self.assertFalse(rules['timeout']['enabled'])   # independent experiment
        self.assertEqual(rules['timeout']['score'], -50.0)
        for key in ('killed_hp_credit', 'enemy_kill_infantry', 'enemy_kill_vehicle',
                    'enemy_building_destroy', 'first_kill', 'first_building_destroy'):
            self.assertTrue(rules[key]['enabled'], key)
        for key in ('progress', 'exploration', 'unit_produced', 'income'):
            self.assertFalse(rules[key]['enabled'], key)
        self.assertEqual([spec[0] for spec in SPECS][:7],
                         ['opponent_hp', 'killed_hp_credit', 'enemy_kill_infantry',
                          'enemy_kill_vehicle', 'enemy_building_destroy',
                          'first_kill', 'first_building_destroy'])

    def test_advance_potential_rewards_approach_only(self):
        from dsh_rl.full_env import advance_delta, enemy_base_contact, enemy_building_sight
        starts = [[92, 59], [90, 127]]
        near = row([obj(1), obj(10, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (95, 120)),
                    obj(11, 300, 'Tank', 'ABSTRACT_TYPE_UNIT', (95, 121))])
        far = row([obj(1), obj(10, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (92, 62)),
                   obj(11, 300, 'Tank', 'ABSTRACT_TYPE_UNIT', (92, 63))])
        self.assertGreater(advance_delta(far, near, starts), 0)
        self.assertLess(advance_delta(near, far, starts), 0)
        self.assertEqual(advance_delta(far, near, []), 0)
        # V4: per-unit advance is immune to the centre-of-mass shifts caused by
        # producing a unit at the base or losing a unit at the front.
        rest = row([obj(1), obj(10, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (92, 62)),
                    obj(11, 300, 'Tank', 'ABSTRACT_TYPE_UNIT', (92, 63))])
        moved = row([obj(1), obj(10, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (93, 63)),
                     obj(11, 300, 'Tank', 'ABSTRACT_TYPE_UNIT', (93, 64))])
        spawn = row([obj(1), obj(10, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (92, 62)),
                     obj(11, 300, 'Tank', 'ABSTRACT_TYPE_UNIT', (92, 63)),
                     obj(12, 300, 'Tank', 'ABSTRACT_TYPE_UNIT', (91, 58))])
        self.assertGreater(per_unit_advance_delta(rest, moved, starts), 0)
        self.assertAlmostEqual(per_unit_advance_delta(rest, spawn, starts), 0.0)
        single = row([obj(1), obj(10, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (95, 120))])
        self.assertFalse(enemy_base_contact(single, starts))
        miner = row([obj(1), obj(10, 500, 'Chrono Miner', 'ABSTRACT_TYPE_UNIT', (95, 120)),
                     obj(11, 500, 'Chrono Miner', 'ABSTRACT_TYPE_UNIT', (95, 121))])
        self.assertFalse(enemy_base_contact(miner, starts))
        self.assertTrue(enemy_base_contact(near, starts))
        enemy_building = dict(obj(30, 101, 'Power Plant', 'ABSTRACT_TYPE_BUILDING', (90, 127)),
                              owner='Computer')
        self.assertTrue(enemy_building_sight(dict(far, visible_enemies=[enemy_building])))
        self.assertFalse(enemy_building_sight(dict(far, visible_enemies=[dict(
            obj(31, 200, 'GI', 'ABSTRACT_TYPE_INFANTRY', (90, 127)), owner='Computer')])))

    def test_penalties_sign_changes_cannot_refill_absolute_budget(self):
        config = defaults()
        config['rules']['progress'].update(score=1, episode_cap=3, step_cap=2)
        ledger = RewardLedger(config)
        self.assertEqual(ledger.apply({'progress': 100})['progress'], 2)
        self.assertEqual(ledger.apply({'progress': -100})['progress'], -1)
        self.assertEqual(ledger.apply({'progress': 100})['progress'], 0)

    def test_disabled_zero_cap_and_event_limit(self):
        config = defaults()
        config['rules']['opponent_hp'].update(score=5, unit=10, max_events=2)
        config['rules']['time']['enabled'] = False
        config['rules']['own_hp']['episode_cap'] = 0
        ledger = RewardLedger(config)
        for expected in (10, 10, 0):
            parts = ledger.apply({'opponent_hp': 20, 'own_hp': 10, 'time': 99})
            self.assertEqual(parts['opponent_hp'], expected)
            self.assertEqual(parts['own_hp'], 0)
            self.assertEqual(parts['time'], 0)

    def test_invalid_inputs_rejected(self):
        for name, value in [('unit', 0), ('score', float('nan')), ('step_cap', -1),
                            ('max_events', .5), ('score', True)]:
            config = defaults()
            config['rules']['income'][name] = value
            with self.assertRaises(ValueError):
                validate(config)

    def test_saved_change_only_applies_to_new_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'rewards.json'
            config = defaults()
            save(config, path)
            old = RewardLedger(load(path))
            config['rules']['opponent_hp']['score'] = 7
            save(config, path)
            new = RewardLedger(load(path))
            self.assertEqual(old.apply({'opponent_hp': 100})['opponent_hp'], 2)
            self.assertEqual(new.apply({'opponent_hp': 100})['opponent_hp'], 7)
            self.assertNotEqual(old.revision, new.revision)

    def test_effective_attack_matching_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = RA2FullEnv(tmp)
            try:
                env.current = {'t': 10.0}
                env._truth_step = {}
                env._pending_attacks = [
                    {'target': 500, 't': 10.0, 'consumed': False},
                    {'target': 600, 't': 10.0, 'consumed': False},
                ]
                env._merge_truth({'enemy_damage_hp': 30.0,
                                  'enemy_damage_addrs': [(500, 30)]})
                self.assertEqual(env._truth_step['enemy_damage_hp'], 30.0)
                self.assertEqual(env._truth_step['effective_attacks'], 1)
                self.assertEqual(env.attacks_effective, 1)
                # The consumed intent is removed; the other one stays pending.
                self.assertEqual([p['target'] for p in env._pending_attacks], [600])
                # Stale pending attacks expire after the matching window.
                env.current = {'t': 20.0}
                env._merge_truth({'enemy_damage_hp': 0.0})
                self.assertEqual(env._pending_attacks, [])
                env.current = {'t': 21.0}
                env._begin_transition()
                self.assertEqual(env._truth_step, {})
            finally:
                env.close()

    def test_real_env_hook_uses_truth_events_and_v4_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = RA2FullEnv(tmp)
            try:
                config = defaults()
                config['rules']['first_building_type']['score'] = 3
                env.reward_ledger = RewardLedger(config)
                env.kind, env.starts, env.max_mapped = 'running', [], 0
                env.seen_buildings, env.seen_units = set(), set()
                env.prev_unit_count = 0
                env.attack_issued = env.last_attack_issued = 0
                env.first_damage_done = False
                env.first_kill_done = False
                env.first_building_destroy_done = False
                env._truth_step = {
                    'enemy_damage_hp': 100.0,
                    'killed_hp_credit': 250.0,
                    'kills_infantry': 2,
                    'kills_vehicle': 1,
                    'building_destroyed': 1,
                    'own_damage_hp': 60.0,
                    'own_removed_hp': 40.0,
                    'effective_attacks': 3,
                }
                before, after = row([]), row([obj(1), obj(2, 101, 'Power Plant')])
                before['t'], after['t'] = 0, 2
                after['map'] = {'mapped': 100}
                base = {'opponent_hp': 0., 'own_hp': 0., 'invalid': 0.,
                        'terminal': 0., 'time': -.004}
                parts = env._complete_reward_parts(before, after, base)
                self.assertEqual(parts['first_building_type'], 3)
                self.assertEqual(parts['construction_yard'], .3)
                self.assertAlmostEqual(parts['exploration'], .02)
                self.assertEqual(parts['opponent_hp'], 2)
                self.assertEqual(parts['killed_hp_credit'], 5)
                self.assertEqual(parts['enemy_kill_infantry'], 12)
                self.assertEqual(parts['enemy_kill_vehicle'], 12)
                self.assertEqual(parts['enemy_building_destroy'], 40)
                self.assertEqual(parts['first_kill'], 5)
                self.assertEqual(parts['first_building_destroy'], 10)
                self.assertEqual(parts['own_hp'], -0.6)     # 100 HP * -0.6/100
                # 3 effective attacks in one decision step: step cap 0.1 wins.
                self.assertEqual(parts['attack'], 0.1)
                self.assertEqual(env.attacks_effective, 0)  # merged by env, not here
                self.assertEqual(parts['first_damage'], 3)
                repeated = env._complete_reward_parts(after, after, base)
                self.assertEqual(repeated['first_building_type'], 0)
                self.assertEqual(repeated['construction_yard'], 0)
                self.assertEqual(repeated['exploration'], 0)
                self.assertEqual(repeated['first_damage'], 0)
                self.assertEqual(repeated['first_kill'], 0)
                self.assertEqual(repeated['first_building_destroy'], 0)
            finally:
                env.close()


if __name__ == '__main__':
    unittest.main()
