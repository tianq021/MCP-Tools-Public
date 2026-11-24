"""trace_parser V2.2 指标测试（合成轨迹，不启动游戏）。"""
import json
import pathlib
import tempfile
import unittest

from dsh_learning import trace_parser


def build_episode(path, reveal_off=False, dense=True):
    records = []
    n_obs = 51 if dense else 12
    own_units = [{'addr': 'o1', 'type_name': 'HTNK', 'x': 0.0, 'y': 0.0, 'health': 100},
                 {'addr': 'o2', 'type_name': 'HTNK', 'x': 2.0, 'y': 0.0, 'health': 100}]
    for i in range(n_obs):
        t = round(i * 0.5, 2)
        # 前 4.5s 处于劣势（敌 HP 高、距离 18 格）；之后后撤到 35 格（脱离）
        if t <= 4.5:
            units = own_units
        else:
            units = [dict(u, x=-15.0, y=0.0) for u in own_units]
        enemies = [
            {'addr': 'e1', 'type_name': 'HTNK', 'owner': 'enemy', 'x': 20.0, 'y': 0.0,
             'health': 500 if t <= 4.5 else 500},
            {'addr': 'e2', 'type_name': 'HTNK', 'owner': 'enemy', 'x': 22.0, 'y': 0.0,
             'health': 500},
        ]
        if i >= 1:
            enemies[0]['health'] = 500 - min(80, 20 * i)  # 持续掉血 → 攻击有效
        if reveal_off:
            enemies.append({'addr': 'b1', 'type_name': 'GAPOWR', 'owner': 'enemy',
                            'x': 30.0, 'y': 0.0, 'health': 1000 - 10 * i})
        elif i >= 10:
            enemies.append({'addr': 'b1', 'type_name': 'GAPOWR', 'owner': 'enemy',
                            'x': 30.0, 'y': 0.0, 'health': 1000 - 10 * (i - 10)})
        records.append({'type': 'observation', 't': t, 'frame': int(t / 0.5),
                        'own_objects': units, 'visible_enemies': enemies,
                        'map': {'mapped': min(500, 10 * i), 'cells': 1000}})
        if i < 3:  # 前 3 个时刻各发一条相同 ATTACK（用于去重测试）
            records.append({'type': 'action', 'action': 'ATTACK',
                            'decision_source': 'ppo',
                            'object_addresses': ['o1', 'o2'], 'target_address': 'e1'})
    with open(path, 'w', encoding='utf-8') as fh:
        for record in records:
            fh.write(json.dumps(record) + '\n')
    result = {'episode_id': 'ep-test', 'elapsed_s': 25.0, 'outcome_confirmed': True,
              'victory': False,
              'metrics': {'damage_dealt': 200, 'truth_kills_infantry': 1,
                          'truth_kills_vehicle': 1, 'truth_kills_building': 0,
                          'truth_building_destroyed': 0, 'own_unit_losses': 0,
                          'own_hp_lost': 0, 'truth_own_removed_hp': 0}}
    return result


class TraceParserV22Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, reveal_off=False, dense=True, decision_seconds=0.5):
        jsonl = self.dir / 'ep.jsonl'
        result = build_episode(jsonl, reveal_off=reveal_off, dense=dense)
        return trace_parser.parse_episode(jsonl, result, decision_seconds=decision_seconds)

    def test_schema_and_quality_fields(self):
        episode = self.parse()
        self.assertEqual(episode['schema_version'], 'v2.2-episode-metrics')
        quality = episode['data_quality']
        self.assertIsNotNone(quality['obs_coverage_composite'])
        self.assertGreater(quality['obs_coverage_composite'], 0.9)
        self.assertIn('obs_sampling_ratio', quality)
        self.assertIn('usable_time_ratio', quality)
        self.assertIsNotNone(quality['gap_max_s'])
        self.assertEqual(quality['damage_coverage'], 0.4)  # 80/200 可见单位伤害
        self.assertEqual(quality['damage_coverage_scope'],
                         'visible_units_over_total_truth')

    def test_attack_dedup_and_first_attack_split(self):
        episode = self.parse()
        self.assertEqual(episode['raw_attack_orders'], 3)
        self.assertEqual(episode['unique_attack_orders'], 1)  # 1.5s 内同组同目标合并
        self.assertEqual(episode['order_repeat_rate'], 0.6667)
        self.assertEqual(episode['first_attack_command_s'], 0.0)
        self.assertIsNotNone(episode['first_effective_attack_s'])
        self.assertIsNotNone(episode['first_damage_dealt_s'])
        self.assertEqual(episode['pre_contact_invalid_orders'], 0)

    def test_s2_nested_same_denominator_relation(self):
        episode = self.parse()
        multi_order = episode['multi_unit_order_share']
        multi_eff = episode['multi_unit_effective_share']
        close_eff = episode['close_multi_unit_effective_share']
        self.assertIsNotNone(multi_order)
        self.assertLessEqual(close_eff, multi_eff)
        self.assertLessEqual(multi_eff, multi_order)
        self.assertIsNotNone(episode['attribution_unconfirmed_share'])

    def test_s3_unit_vs_building_damage_split(self):
        episode = self.parse(reveal_off=True)
        self.assertGreater(episode['damage_to_buildings_visible'], 0)
        self.assertEqual(episode['truth_kills_units'], 2)
        self.assertEqual(episode['truth_building_destroyed'], 0)
        self.assertIsNotNone(episode['kills_per_1000_unit_damage'])

    def test_s4_opportunity_execution_and_preservation(self):
        episode = self.parse()
        self.assertGreaterEqual(episode['retreat_opportunities'], 1)
        self.assertEqual(episode['retreat_executed'], 1)
        self.assertEqual(episode['retreat_execution_rate'], 1.0)
        self.assertEqual(episode['preserved_unit_ratio'], 1.0)
        self.assertIsNone(episode['hp_exchange'])  # 无己方损失 → 不可计算，不是 0

    def test_s5_discovery_reveal_mode(self):
        normal = self.parse()
        self.assertFalse(normal['reveal_mode'])
        self.assertIsNotNone(normal['first_new_enemy_building_s'])
        reveal = self.parse(reveal_off=True)
        self.assertTrue(reveal['reveal_mode'])
        self.assertEqual(reveal['s5_discovery_status'], 'INCOMPUTABLE_REVEAL_MODE')
        self.assertIsNone(reveal['first_new_enemy_building_s'])

    def test_sampling_cadence_is_data_driven(self):
        # 实测决策节奏可能慢于标称 0.5s：用观测间隔中位数，避免系统性误报低覆盖
        episode = self.parse(decision_seconds=None)
        quality = episode['data_quality']
        self.assertEqual(quality['cadence_source'], 'observed_median_interval')
        self.assertIsNotNone(quality['cadence_median_s'])
        self.assertGreaterEqual(quality['obs_coverage_composite'], 0.95)
        self.assertNotIn('UNKNOWN_SAMPLING', quality['quality_flags'])

    def test_insufficient_observations_is_unknown(self):
        jsonl = self.dir / 'sparse.jsonl'
        rows = [{'type': 'observation', 't': 0.0, 'frame': 0,
                 'own_objects': [], 'visible_enemies': []},
                {'type': 'observation', 't': 0.5, 'frame': 1,
                 'own_objects': [], 'visible_enemies': []}]
        with open(jsonl, 'w', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row) + '\n')
        episode = trace_parser.parse_episode(jsonl, {'elapsed_s': 1.0},
                                             decision_seconds=0.5)
        quality = episode['data_quality']
        self.assertIsNone(quality['obs_coverage_composite'])
        self.assertEqual(quality['na_reason'], 'insufficient_observations')
        self.assertIn('UNUSABLE', quality['quality_flags'])


if __name__ == '__main__':
    unittest.main()
