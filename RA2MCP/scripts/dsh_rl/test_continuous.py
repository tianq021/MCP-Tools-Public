"""Offline lifecycle checks; fixtures are never used by real-game training."""
import pathlib
import tempfile
import unittest

import gymnasium as gym
import numpy as np
from sb3_contrib import MaskablePPO
from dsh_rl.env import RA2MicroEnv
from dsh_episode.schema import classify_outcome
from dsh_rl.reflection import review, write_reflection
from dsh_rl.train import EvidenceCallback, learn_chunks


class CountingEnv(gym.Env):
    observation_space = gym.spaces.Box(0, 100, (1,), dtype=np.float32)
    action_space = gym.spaces.Discrete(2)

    def __init__(self):
        self.resets = self.steps = 0
        self.total_reward = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.resets += 1
        self.steps = 0
        return np.array([0], np.float32), {}

    def action_masks(self):
        return np.ones(2, dtype=bool)

    def step(self, action):
        self.steps += 1
        self.total_reward += 1
        return np.array([self.steps], np.float32), 1., self.steps == 10, False, {}


class StopAt24(EvidenceCallback):
    def _on_step(self):
        if self.num_timesteps == 24:
            (self.out / 'STOP').touch()
        return super()._on_step()


class TestContinuous(unittest.TestCase):
    def test_chunk_boundaries_do_not_reset_game_and_stop_saves_optimizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            env = CountingEnv()
            model = MaskablePPO('MlpPolicy', env, n_steps=8, batch_size=4,
                                n_epochs=1, device='cpu', verbose=0)
            callback = StopAt24(out)
            learn_chunks(model, 8, callback, out, continuous=True)
            self.assertTrue(callback.requested_stop)
            self.assertEqual(model.num_timesteps, 24)
            self.assertEqual(env.resets, 3)  # initial reset + actual ends at 10 and 20
            self.assertEqual(env.steps, 4)
            loaded = MaskablePPO.load(out / 'checkpoint.zip', device='cpu')
            self.assertEqual(loaded._n_updates, 2)
            self.assertTrue(loaded.policy.optimizer.state)

    def test_unlimited_episode_keeps_terminal_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = RA2MicroEnv(tmp, max_seconds=0)
            try:
                kwargs = dict(stage_name='STAGE_INGAME', elapsed_s=100000,
                              max_seconds=env.max_seconds or None)
                self.assertEqual(classify_outcome(**kwargs)['kind'], 'running')
                self.assertEqual(classify_outcome(**kwargs, human_is_loser=True)['kind'], 'defeat')
                self.assertEqual(classify_outcome(**kwargs, human_is_winner=True)['kind'], 'win')
            finally:
                env.close()

    def test_review_does_not_invent_victory_or_failure_cause(self):
        result = dict(episode_id='offline-test', kind='interrupted', elapsed_s=3,
                      metrics={'damage_dealt': 0}, stats={'invalid_orders': 1})
        data = review(result, [], {'terminal': 0})
        self.assertEqual(data['kind'], 'interrupted')
        self.assertEqual(data['observed_building_types'], [])
        self.assertTrue(any('没有确认胜负' in s for s in data['findings']))
        with tempfile.TemporaryDirectory() as tmp:
            write_reflection(pathlib.Path(tmp), result, [], {'terminal': 0})
            self.assertTrue((pathlib.Path(tmp) / 'reflections/offline-test.md').exists())

    def test_parallel_instance_parameters_never_sweep_sibling_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = RA2MicroEnv(tmp, max_seconds=180, instance=0)
            self.assertEqual(primary.instance, 0)
            self.assertTrue(primary.kill_stale)
            primary.close()
            worker = RA2MicroEnv(tmp, max_seconds=180, game_dir='test-game-2',
                                 port=14523, instance=2)
            self.assertEqual(worker.instance, 2)
            self.assertEqual(worker.port, 14523)
            self.assertEqual(worker.game_dir, pathlib.Path('test-game-2'))
            self.assertFalse(worker.kill_stale)   # instance > 0 must not kill siblings
            worker.close()
            lone = RA2MicroEnv(tmp, max_seconds=180, port=14524, kill_stale=False)
            self.assertFalse(lone.kill_stale)
            lone.close()


if __name__ == '__main__':
    unittest.main()
