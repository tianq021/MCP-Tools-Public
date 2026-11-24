"""V1 自动化测试：模型兼容性判定逻辑（合成数据，不加载真实模型）。"""
import unittest

from dsh_learning import compatibility

CONTRACT = {'obs_dim': 1801, 'action_n': 301,
            'reward_version': 'full_configurable_v4',
            'action_space_version': 'full_ground_v2p1'}


def make_asset(**overrides):
    asset = {
        'id': 'TEST', 'path': 'reports/rl/fake/model.zip', 'kind': 'ppo',
        'role': 'test', 'protected': False, 'do_not_resume': False,
        'file': {'exists': True, 'sha256': 'a' * 64},
        'run': {'run_json': {'control_mode': 'full',
                             'reward_version': 'full_configurable_v4',
                             'action_space_version': 'full_ground_v2p1'}},
        'inspection': {'loadable': True, 'inference_ok': True, 'obs_dim': 1801,
                       'action_n': 301, 'optimizer_loaded': True,
                       'parameters': 258606, 'net_arch': [64, 64]},
    }
    for key, value in overrides.items():
        if key in ('run', 'inspection', 'file') and isinstance(value, dict):
            asset[key].update(value)
        else:
            asset[key] = value
    return asset


class CompatibilityTests(unittest.TestCase):
    def test_fully_matching_model_can_resume(self):
        result = compatibility.evaluate_asset(make_asset(), CONTRACT)
        self.assertEqual(result['verdict'], 'CAN_RESUME')
        self.assertTrue(result['read_only_eval'])
        self.assertTrue(result['can_resume_train'])

    def test_reward_mismatch_requires_explicit_flag(self):
        asset = make_asset(run={'run_json': {'control_mode': 'full',
                                             'reward_version': 'full_configurable_v4_survival',
                                             'action_space_version': 'full_ground_v2p1'}})
        result = compatibility.evaluate_asset(asset, CONTRACT)
        self.assertEqual(result['verdict'], 'RESUME_WITH_FLAGS')
        self.assertIn('--allow-reward-change', result['required_flags'])

    def test_action_space_mismatch_requires_explicit_flag(self):
        asset = make_asset(run={'run_json': {'control_mode': 'full',
                                             'reward_version': 'full_configurable_v4',
                                             'action_space_version': 'full_ground_v2'}})
        result = compatibility.evaluate_asset(asset, CONTRACT)
        self.assertEqual(result['verdict'], 'RESUME_WITH_FLAGS')
        self.assertIn('--allow-action-space-change', result['required_flags'])

    def test_obs_dim_mismatch_is_blocked(self):
        asset = make_asset(inspection={'obs_dim': 1800})
        result = compatibility.evaluate_asset(asset, CONTRACT)
        self.assertEqual(result['verdict'], 'BLOCKED')
        self.assertFalse(result['read_only_eval'])

    def test_do_not_resume_is_blocked(self):
        result = compatibility.evaluate_asset(make_asset(do_not_resume=True), CONTRACT)
        self.assertEqual(result['verdict'], 'BLOCKED')
        self.assertFalse(result['can_resume_train'])

    def test_unloadable_model_is_blocked(self):
        result = compatibility.evaluate_asset(make_asset(
            inspection={'loadable': False, 'load_error': 'boom'}), CONTRACT)
        self.assertEqual(result['verdict'], 'BLOCKED')

    def test_non_full_control_mode_is_blocked(self):
        asset = make_asset(run={'run_json': {'control_mode': 'micro',
                                             'reward_version': 'full_configurable_v4',
                                             'action_space_version': 'full_ground_v2p1'}})
        self.assertEqual(compatibility.evaluate_asset(asset, CONTRACT)['verdict'],
                         'BLOCKED')

    def test_bc_model_is_diagnostic_only(self):
        asset = {'id': 'BC', 'path': 'x.pt', 'kind': 'bc', 'role': 'bc',
                 'do_not_resume': True,
                 'inspection': {'loadable': True, 'obs_dim': 1801, 'action_n': 301}}
        result = compatibility.evaluate_asset(asset, CONTRACT)
        self.assertEqual(result['verdict'], 'BC_DIAGNOSTIC_ONLY')
        self.assertFalse(result['can_resume_train'])


if __name__ == '__main__':
    unittest.main()
