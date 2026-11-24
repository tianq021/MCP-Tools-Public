"""skill_metrics V2.2 掌握判定测试（合成 episode，不启动游戏）。"""
import unittest

from dsh_learning import skill_metrics


def base_episode(**overrides):
    episode = {
        'episode_id': 'ep',
        'data_quality': {'quality_flags': [], 'damage_coverage': 0.8},
        'first_effective_attack_s': 50.0, 'first_attack_command_s': 40.0,
        'attacks_per_min': 12.0, 'attack_effective6_rate': 0.5,
        'pre_contact_invalid_orders': 0, 'order_repeat_rate': 0.1,
        'close_multi_unit_effective_share': 0.30, 'ineffective_switch_rate': 0.40,
        'attack_concentration': 0.5, 'kills_per_1000_unit_damage': 4.0,
        'kills_per_effective_attack': 0.20,
        'hp_exchange': 0.5, 'own_losses_per_min': 2.0,
        'retreat_opportunities': 3, 'retreat_executed': 2,
        'retreat_execution_rate': 0.66, 'preserved_unit_ratio': 0.8,
        's5_discovery_status': 'OK', 'first_new_enemy_building_s': 60.0,
        'map_coverage_final': 0.1,
    }
    episode.update(overrides)
    return episode


def record(pass_all=True, n=20, skill_overrides=None):
    """构造 n 局的 aggregate 输入。"""
    episodes = []
    for index in range(n):
        skills = {}
        for sid in ('S1', 'S2', 'S3', 'S4'):
            skills[sid] = {'skill': sid, 'pass': pass_all}
        skills['S5.1'] = {'skill': 'S5.1', 'pass': pass_all}
        skills['S5.2'] = {'skill': 'S5.2', 'status': 'PROVISIONAL', 'pass': None}
        skills['S5.3'] = {'skill': 'S5.3', 'status': 'INCOMPUTABLE', 'pass': None}
        episodes.append({'episode_id': 'ep%d' % index,
                         'episode': base_episode(),
                         'skills': skills})
    if skill_overrides:
        for index, override in skill_overrides.items():
            episodes[index]['skills'].update(override)
    return episodes


class EpisodeSkillTests(unittest.TestCase):
    def test_s1_pass_and_spam(self):
        episode = base_episode()
        self.assertTrue(skill_metrics.episode_skill(episode, 'S1')['pass'])
        spam = base_episode(pre_contact_invalid_orders=6)
        result = skill_metrics.episode_skill(spam, 'S1')
        self.assertFalse(result['pass'])
        self.assertIn('SPAM_RISK', result['reasons'])
        spam2 = base_episode(order_repeat_rate=0.6)
        self.assertEqual(skill_metrics.episode_skill(spam2, 'S1')['status'],
                         'NOT_MEASURED')

    def test_s1_time_degraded_not_pass(self):
        episode = base_episode(
            data_quality={'quality_flags': ['DEGRADED_TIME'], 'damage_coverage': 0.8})
        result = skill_metrics.episode_skill(episode, 'S1')
        self.assertFalse(result['pass'])
        self.assertEqual(result['indicators']['first_effective_attack_s']['status'],
                         'DEGRADED_TIME')

    def test_s2_low_damage_coverage_undetermined(self):
        episode = base_episode(
            data_quality={'quality_flags': [], 'damage_coverage': 0.2})
        self.assertEqual(skill_metrics.episode_skill(episode, 'S2')['status'],
                         'UNDETERMINED')

    def test_s3_kill_scope_unknown(self):
        episode = base_episode(kills_per_1000_unit_damage=None)
        self.assertEqual(skill_metrics.episode_skill(episode, 'S3')['status'],
                         'UNDETERMINED')

    def test_s4_requires_two_opportunities(self):
        episode = base_episode(retreat_opportunities=1)
        self.assertEqual(skill_metrics.episode_skill(episode, 'S4')['status'],
                         'NOT_MEASURED')
        good = base_episode()
        self.assertTrue(skill_metrics.episode_skill(good, 'S4')['pass'])

    def test_s5_subskills(self):
        reveal = base_episode(s5_discovery_status='INCOMPUTABLE_REVEAL_MODE')
        self.assertEqual(skill_metrics.episode_skill(reveal, 'S5.1')['status'],
                         'INCOMPUTABLE')
        self.assertTrue(skill_metrics.episode_skill(base_episode(), 'S5.1')['pass'])
        self.assertEqual(skill_metrics.episode_skill(base_episode(), 'S5.3')['status'],
                         'INCOMPUTABLE')
        self.assertEqual(skill_metrics.episode_skill(base_episode(), 'S5.2')['status'],
                         'PROVISIONAL')


class AggregateTests(unittest.TestCase):
    def test_mastered_requires_n20_and_wilson_lower(self):
        result = skill_metrics.aggregate_skill('S1', record(pass_all=True, n=20))
        self.assertEqual(result['status'], 'MASTERED')
        self.assertEqual(result['mastery_rule_id'], 'v2.2-wilson-n20-lower0.60')
        small = skill_metrics.aggregate_skill('S1', record(pass_all=True, n=5))
        self.assertEqual(small['status'], 'UNDETERMINED')
        self.assertIn('insufficient_episodes', small['reasons'])

    def test_not_mastered_when_all_fail(self):
        result = skill_metrics.aggregate_skill('S1', record(pass_all=False, n=20))
        self.assertEqual(result['status'], 'NOT_MASTERED')

    def test_s5_requires_both_subskills(self):
        # 默认记录中 S5.3 = INCOMPUTABLE → 总体必须 INCOMPUTABLE
        block = skill_metrics.aggregate_skill('S5', record(pass_all=True, n=20))
        self.assertEqual(block['status'], 'INCOMPUTABLE')
        # 若 S5.3 可计算且掌握 → 才可能 MASTERED（用模拟子状态验证聚合函数）
        episodes = record(pass_all=True, n=20)
        for entry in episodes:
            entry['skills']['S5.3'] = {'skill': 'S5.3', 'status': 'MASTERED',
                                       'pass': True}
            entry['skills']['S5.1'] = {'skill': 'S5.1', 'pass': True}
        block = skill_metrics.aggregate_skill('S5', episodes)
        self.assertEqual(block['status'], 'MASTERED')
        self.assertEqual(skill_metrics.s5_overall(
            {'S5.1': 'MASTERED', 'S5.3': 'INCOMPUTABLE'}), 'INCOMPUTABLE')
        self.assertEqual(skill_metrics.s5_overall(
            {'S5.1': 'NOT_MASTERED', 'S5.3': 'MASTERED'}), 'NOT_MASTERED')

    def test_s4_sample_rule_applied(self):
        episodes = record(pass_all=True, n=20)
        for entry in episodes:
            entry['episode'] = base_episode(retreat_opportunities=0,
                                            retreat_executed=0)
        result = skill_metrics.aggregate_skill('S4', episodes)
        self.assertEqual(result['status'], 'UNDETERMINED')
        self.assertIn('s4_sample_rule_not_met', result['reasons'])
        self.assertEqual(result['sample_rule_id'],
                         'v2.2-s4-op20-ep10-exec5-episode-level-wilson')


if __name__ == '__main__':
    unittest.main()
