"""P0 审批安全底座测试（7 类安全场景；不启动游戏/训练）。"""
import json
import pathlib
import shutil
import threading
import unittest
import uuid
from unittest import mock

from dsh_learning import approvals


class ApprovalTestBase(unittest.TestCase):
    def setUp(self):
        # 令牌库/模型/配置都必须在项目根内（rel_to_root 约束），用 .tmp 隔离。
        self.base = approvals.ROOT / '.tmp' / 'p0-tests' / uuid.uuid4().hex[:12]
        self.base.mkdir(parents=True, exist_ok=True)
        self.approvals_dir = self.base / 'approvals'
        self._patchers = [
            mock.patch.object(approvals, 'APPROVALS_DIR', self.approvals_dir),
            mock.patch.object(approvals, 'TOKENS_PATH', self.approvals_dir / 'tokens.jsonl'),
            mock.patch.object(approvals, 'AUDIT_PATH', self.approvals_dir / 'audit.jsonl'),
            mock.patch.object(approvals, 'LOCKS_DIR', self.approvals_dir / 'locks'),
            mock.patch.object(approvals, '_active_training_pids', lambda: []),
        ]
        for patcher in self._patchers:
            patcher.start()
        approvals._ensure_dirs()
        self.model = self.base / 'model.zip'
        self.model.write_bytes(b'fake-ppo-weights-v1')
        self.thresholds = {'S1': {'first_effective_attack_s': 90.0},
                           'mastery': {'n': 20, 'wilson_low': 0.60}}
        self.config = {
            'approval': {
                'experiment': 'A',
                'skill': 'S1',
                'budget': {'max_steps': 15000, 'eval_every_steps': 2500,
                           'eval_episodes': 20},
                'seed': 11,
                'workers': 8,
                'episode_seconds': 300,
                'reward_version': 'full_configurable_v4',
                'action_space_version': 'full_ground_v2p1',
                'obs_version': 'features_v2',
                'thresholds_version': 'v2.2-draft',
                'thresholds_hash': approvals.content_hash(self.thresholds),
                'eval_protocol_hash': approvals.content_hash({'map': '2peaks', 'n': 20}),
                'game': {'executable_sha256': '', 'bridge_version': ''},
            }
        }
        self.config_path = self.base / 'run-config.json'
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False),
                                    encoding='utf-8')

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def issue(self, **kwargs):
        return approvals.issue_token(self.config_path, self.model, **kwargs)

    def runtime(self, **overrides):
        approval = self.config['approval']
        runtime = {
            'workers': approval['workers'],
            'episode_seconds': approval['episode_seconds'],
            'steps': 15000,
            'device': 'cpu',
            'resume': str(self.model),
            'fresh': False,
            'reward_version': approval['reward_version'],
            'action_space_version': approval['action_space_version'],
            'obs_version': approval['obs_version'],
            'config_path': str(self.config_path),
        }
        runtime.update(overrides)
        return runtime

    def reserve(self, token, **overrides):
        request = approvals.validate_launch_request(
            token['token_id'], self.config_path, self.runtime(**overrides))
        return approvals.reserve_and_mark_running(
            token['token_id'], request, self.base / 'run', 4242)


class IssueAndHappyPathTests(ApprovalTestBase):
    def test_issue_show_and_happy_path_consume(self):
        token = self.issue()
        self.assertEqual(token['consumption'], 'UNUSED')
        self.assertEqual(token['lifecycle'], 'ISSUED')
        self.assertEqual(token['model']['sha256_expected'],
                         approvals.sha256_file(self.model))
        request = approvals.validate_launch_request(
            token['token_id'], self.config_path, self.runtime())
        self.assertEqual(request['experiment'], 'A')
        self.assertEqual(request['skill'], 'S1')
        consumed = approvals.reserve_and_mark_running(
            token['token_id'], request, self.base / 'run', 4242)
        self.assertEqual(consumed['consumption'], 'CONSUMED')
        self.assertEqual(consumed['launch'], 'RUNNING')
        finished = approvals.mark_finished(token['token_id'], True)
        self.assertEqual(finished['launch'], 'FINISHED_OK')
        self.assertEqual(finished['lifecycle'], 'COMPLETED')
        # 审计只追加且覆盖关键事件
        audit_events = [json.loads(line)['event'] for line in
                        (self.approvals_dir / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertIn('issue', audit_events)
        self.assertIn('reserve', audit_events)
        self.assertIn('start', audit_events)
        self.assertIn('finish', audit_events)


class SingleUseTests(ApprovalTestBase):
    def test_second_consume_rejected(self):
        token = self.issue()
        self.reserve(token)
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'TOKEN_ALREADY_USED')

    def test_concurrent_consume_only_one_wins(self):
        token = self.issue()
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            try:
                results.append(('ok', self.reserve(token)))
            except approvals.ApprovalError as exc:
                results.append(('error', exc.code))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        ok = [r for r in results if r[0] == 'ok']
        errors = [r for r in results if r[0] == 'error']
        self.assertEqual(len(ok), 1, results)
        self.assertEqual(len(errors), 1, results)
        self.assertIn(errors[0][1], ('TOKEN_ALREADY_USED', 'TOKEN_BUSY'))

    def test_finish_or_expire_unused_token_rejected(self):
        token = self.issue()
        with self.assertRaises(approvals.ApprovalError):
            approvals.mark_finished(token['token_id'], True)
        revoked = approvals.revoke(token['token_id'])
        self.assertEqual(revoked['lifecycle'], 'REVOKED')
        with self.assertRaises(approvals.ApprovalError):
            approvals.expire(token['token_id'])


class ExpiryTests(ApprovalTestBase):
    def test_expired_token_rejected(self):
        token = self.issue(ttl_hours=-1)
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'TOKEN_EXPIRED')


class ConfigChangeTests(ApprovalTestBase):
    def test_config_content_change_rejected(self):
        token = self.issue()
        changed = json.loads(json.dumps(self.config))
        changed['approval']['seed'] = 12
        self.config_path.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime(workers=8))
        self.assertEqual(ctx.exception.code, 'CONFIG_HASH_MISMATCH')

    def test_token_budget_tamper_rejected(self):
        token = self.issue()
        tampered = dict(token)
        tampered['budget'] = dict(token['budget'], max_steps=999999)
        with open(approvals.TOKENS_PATH, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(tampered, ensure_ascii=False) + '\n')
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'BINDING_MISMATCH')

    def test_runtime_binding_mismatches_rejected(self):
        token = self.issue()
        cases = [
            ({'workers': 4}, 'BINDING_MISMATCH'),
            ({'episode_seconds': 600}, 'BINDING_MISMATCH'),
            ({'reward_version': 'older_reward'}, 'BINDING_MISMATCH'),
            ({'action_space_version': 'legacy'}, 'BINDING_MISMATCH'),
            ({'steps': 15001}, 'BUDGET_EXCEEDED'),
            ({'steps': 0}, 'BUDGET_UNBOUNDED'),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(approvals.ApprovalError) as ctx:
                    approvals.validate_launch_request(
                        token['token_id'], self.config_path, self.runtime(**overrides))
                self.assertEqual(ctx.exception.code, expected, overrides)


    def test_runtime_seed_binding(self):
        token = self.issue()
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime(seed=12))
        self.assertEqual(ctx.exception.code, 'BINDING_MISMATCH')
        ok = approvals.validate_launch_request(
            token['token_id'], self.config_path, self.runtime(seed=11))
        self.assertEqual(ok['token_id'], token['token_id'])


class ModelIntegrityTests(ApprovalTestBase):
    def test_model_sha_changed_after_issue_rejected(self):
        token = self.issue()
        self.model.write_bytes(b'fake-ppo-weights-v2-tampered')
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'MODEL_SHA_MISMATCH')

    def test_model_path_swap_rejected(self):
        token = self.issue()
        other = self.base / 'other-model.zip'
        other.write_bytes(b'fake-ppo-weights-v1')
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime(resume=str(other)))
        self.assertEqual(ctx.exception.code, 'MODEL_PATH_MISMATCH')

    def test_fresh_run_with_model_bound_token_rejected(self):
        token = self.issue()
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime(resume=None, fresh=True))
        self.assertEqual(ctx.exception.code, 'MODEL_MISMATCH')


class DuplicateLaunchTests(ApprovalTestBase):
    def test_active_training_process_blocks_launch(self):
        token = self.issue()
        with mock.patch.object(approvals, '_active_training_pids', lambda: [12345]):
            with self.assertRaises(approvals.ApprovalError) as ctx:
                approvals.validate_launch_request(
                    token['token_id'], self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'DUPLICATE_LAUNCH')


class RecoveryTests(ApprovalTestBase):
    def test_stale_reserved_becomes_abandoned(self):
        token = self.issue()
        stale = dict(token)
        stale['consumption'] = 'RESERVED'
        stale['lifecycle'] = 'RESERVED'
        stale['launch'] = 'NOT_LAUNCHED'
        stale['reservation'] = {'at': '2020-01-01T00:00:00+0800', 'pid': 99999999,
                                'run_dir': 'x'}
        with open(approvals.TOKENS_PATH, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(stale, ensure_ascii=False) + '\n')
        recovered = approvals.recover_stale()
        self.assertIn(token['token_id'], recovered)
        current = approvals.get_token(token['token_id'])
        self.assertEqual(current['lifecycle'], 'ABANDONED')
        self.assertEqual(current['launch'], 'FAILED_NO_RUN')
        # 已废弃令牌不可再次使用
        with self.assertRaises(approvals.ApprovalError):
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())

    def test_failed_no_run_is_terminal(self):
        token = self.issue()
        self.reserve(token)
        abandoned = approvals.mark_failed_no_run(token['token_id'], 'launch_failed')
        self.assertEqual(abandoned['lifecycle'], 'ABANDONED')
        self.assertEqual(abandoned['launch'], 'FAILED_NO_RUN')
        with self.assertRaises(approvals.ApprovalError):
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())


class SchemaTests(ApprovalTestBase):
    def test_incomplete_token_rejected(self):
        token = self.issue()
        broken = dict(token)
        broken.pop('config_hash')
        with open(approvals.TOKENS_PATH, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(broken, ensure_ascii=False) + '\n')
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                token['token_id'], self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'TOKEN_INCOMPLETE')

    def test_missing_token_rejected(self):
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.validate_launch_request(
                'APR-does-not-exist', self.config_path, self.runtime())
        self.assertEqual(ctx.exception.code, 'TOKEN_NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
