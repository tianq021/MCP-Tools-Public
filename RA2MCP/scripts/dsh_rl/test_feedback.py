"""Regression checks for observed terminal rewards and excluded missing feedback."""
import asyncio
import json
import pathlib
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import gymnasium as gym
import numpy as np
from ra2yrproto import ra2yr
from sb3_contrib import MaskablePPO

from dsh_episode import schema, obs
from dsh_episode.runner import GameStateReadError, EpisodeRunner, read_state
from dsh_rl.env import RA2MicroEnv
from dsh_rl.feedback import TerminalMonitor, FeedbackUnavailable
from dsh_rl.reward_config import RewardLedger, load
from dsh_rl.safe_vec import (SafeSubprocVecEnv, FeedbackBatchInterrupted,
                             resume_after_feedback)

ROOT = pathlib.Path(__file__).resolve().parents[2]


def state(kind='running', frame=100):
    s = ra2yr.GameState(stage=ra2yr.STAGE_INGAME, current_frame=frame)
    h = s.houses.add()
    setattr(h, 'self', 7)
    h.name = 'human'
    h.is_human_player = True
    h.current_player = kind == 'running'
    h.defeated = h.is_loser = kind == 'defeat'
    h.is_winner = kind == 'win'
    return s


class FeedbackEnv(RA2MicroEnv):
    """Real capture/reward/finish paths, with a supplied protobuf snapshot."""
    def __init__(self, out, kind):
        super().__init__(out, max_seconds=300)
        self.observation_space = gym.spaces.Box(0, 1, (1,), dtype=np.float32)
        self.started = time.monotonic() - 10
        self._hero_id = 7
        self.state = state()
        self.hero = self.state.houses[0]
        self.type_names, self.map_soa, self.starts = {}, None, []
        self.last_map_at, self.max_mapped = 10, 0
        self.orders_ok = self.rl_orders_ok = self.invalid_orders = 0
        self.episode_reward_parts = {}
        self.last_acks = []
        self.writer = schema.EpisodeWriter(pathlib.Path(out), 'feedback-test', {'environment': {}})
        self.current = obs.capture_observation(self.state, 7, {}, {7:'human'}, None, schema.now_wall())
        self.current['t'] = 9.
        self.writer.add_row(self.current)
        self.kind, self.finished = 'running', False
        self.last_features = np.zeros(1, np.float32)
        self.runner = SimpleNamespace(proc=SimpleNamespace(pid=123, poll=lambda:0),
                                      client=object(), render_off=None)
        self._feedback_monitor = TerminalMonitor(None, None, 7, self.started, 300)
        if kind in ('win', 'defeat'):
            self._feedback_monitor.observe(state(kind, 120))
        self.ledger = RewardLedger(load(ROOT/'config/rewards-combat.json'))

    def _features(self):
        return np.asarray([float(self.hero.defeated or self.hero.is_winner)], np.float32)

    def _select_intent(self, action):
        raise AssertionError('no order may be sent after an observed terminal')

    def _is_noop(self, action):
        return True

    def _complete_reward_parts(self, before, after, parts):
        return self.ledger.apply({'victory': int(self.kind=='win'), 'defeat':int(self.kind=='defeat')})

    async def _stop_client(self):
        if self._feedback_monitor is not None:
            await self._feedback_monitor.stop()
            self._feedback_monitor = None

    def _cleanup_game(self):
        self.runner = None


class InterruptedEnv(gym.Env):
    observation_space = gym.spaces.Box(0, 100, (1,), dtype=np.float32)
    action_space = gym.spaces.Discrete(2)

    def __init__(self):
        self.calls = 0

    def reset(self, *, seed=None, options=None):
        return np.zeros(1, np.float32), {}

    def action_masks(self):
        return np.ones(2, dtype=bool)

    def step(self, action):
        self.calls += 1
        if self.calls == 2:
            return np.zeros(1,np.float32), 0., False, True, {'feedback_valid':False}
        return np.ones(1,np.float32), 1., False, False, {'feedback_valid':True}


class TestFeedback(unittest.TestCase):
    def test_confirmed_loss_survives_process_exit_and_pays_once(self):
        with tempfile.TemporaryDirectory() as td:
            env = FeedbackEnv(td, 'defeat')
            try:
                _, reward, terminated, truncated, info = env.step(0)
                self.assertEqual(reward, -300.)
                self.assertTrue(terminated)
                self.assertFalse(truncated)
                self.assertFalse(info['action_executed'])
                self.assertTrue(info['feedback_valid'])
                result = json.loads((pathlib.Path(td)/'feedback-test.result.json').read_text())
                self.assertEqual(result['reward_parts']['defeat'], -300.)
                self.assertTrue(result['outcome_confirmed'])
                with self.assertRaisesRegex(RuntimeError, 'reset required'):
                    env.step(0)
            finally:
                env.close()

    def test_confirmed_win_pays_victory_reward(self):
        with tempfile.TemporaryDirectory() as td:
            env=FeedbackEnv(td,'win')
            try:
                self.assertEqual(env.step(0)[1],500.)
            finally:env.close()

    def test_zero_exit_without_result_is_unknown_and_not_learned(self):
        with tempfile.TemporaryDirectory() as td:
            env=FeedbackEnv(td,'running')
            async def broken(action):raise GameStateReadError('connection closed')
            env._step=broken
            try:
                with self.assertRaises(FeedbackUnavailable):env.step(0)
                r=json.loads((pathlib.Path(td)/'feedback-test.result.json').read_text())
                self.assertEqual(r['kind'],'game_end')
                self.assertEqual(r['failure']['exit_code'],0)
                self.assertTrue(r['learning_excluded'])
                self.assertFalse(r['outcome_confirmed'])
                self.assertEqual(r['reward_parts'],{})
            finally:env.close()

    def test_programming_runtimeerror_is_not_hidden_as_game_crash(self):
        with tempfile.TemporaryDirectory() as td:
            env=FeedbackEnv(td,'running')
            async def broken(action):raise RuntimeError('bug in reward contract')
            env._step=broken
            try:
                with self.assertRaisesRegex(RuntimeError,'bug in reward'):env.step(0)
            finally:env.close()

    def test_monitor_runs_while_caller_is_idle_and_tracks_original_house(self):
        with tempfile.TemporaryDirectory() as td:
            env=RA2MicroEnv(td)
            count=[]
            async def reader(client, strict=False):
                count.append(1)
                return state('defeat' if len(count)>1 else 'running')
            monitor=TerminalMonitor(None,reader,7,time.monotonic(),300,interval=.01)
            async def start():monitor.start()
            env._run(start())
            time.sleep(.12)  # Simulate PPO updating outside the I/O loop.
            try:
                self.assertEqual(monitor.outcome['kind'],'defeat')
                self.assertFalse(monitor.terminal_state.houses[0].current_player)
                env._run(monitor.stop())
            finally:env.close()

    def test_strict_read_preserves_cause(self):
        client=SimpleNamespace(exec_command=AsyncMock(side_effect=OSError('socket gone')))
        with self.assertRaisesRegex(GameStateReadError,'socket gone'):
            asyncio.run(read_state(client,strict=True))
        self.assertIsNone(asyncio.run(read_state(client)))

    def test_unknown_feedback_never_reaches_ppo_optimizer(self):
        vec=SafeSubprocVecEnv([InterruptedEnv,InterruptedEnv],start_method='spawn')
        try:
            model=MaskablePPO('MlpPolicy',vec,n_steps=4,batch_size=8,n_epochs=1,
                              policy_kwargs={'net_arch':[8]},verbose=0)
            with self.assertRaises(FeedbackBatchInterrupted) as caught:
                model.learn(total_timesteps=8)
            self.assertEqual(model._n_updates,0)
            self.assertEqual(model.rollout_buffer.pos,1)
            self.assertEqual(resume_after_feedback(model,caught.exception),2)
            self.assertEqual(model.rollout_buffer.pos,0)
            model.learn(total_timesteps=8,reset_num_timesteps=False)
            self.assertGreater(model._n_updates,0)
            np.testing.assert_array_equal(model.rollout_buffer.rewards,1.)
        finally:vec.close()

    def test_dead_process_cleanup_does_not_restore_memory(self):
        runner=EpisodeRunner.__new__(EpisodeRunner)
        runner.lock_path='test-lock'
        runner.proc=SimpleNamespace(poll=lambda:0)
        exited=[]
        runner.render_off=SimpleNamespace(process_exited=lambda code:exited.append(code))
        runner.opts=SimpleNamespace(keep_game=True)
        runner.log=lambda text:None
        runner.disable_render_off=lambda: (_ for _ in ()).throw(AssertionError('must not restore'))
        with patch('dsh_episode.runner.lockfile_mod.release'):
            runner.cleanup()
        self.assertEqual(exited,[0])

    @staticmethod
    def _multi_house_state(enemy_defeated, game_over=False):
        s = ra2yr.GameState(stage=ra2yr.STAGE_INGAME, current_frame=500)
        human = s.houses.add()
        human.self = 7
        human.name = 'human'
        human.is_human_player = True
        human.is_game_over = game_over
        enemy = s.houses.add()
        enemy.self = 8
        enemy.name = 'Computer'
        enemy.is_human_player = False
        enemy.defeated = enemy_defeated
        neutral = s.houses.add()
        neutral.self = 9
        neutral.name = 'Neutral'
        neutral.is_human_player = False
        neutral.defeated = False
        return s

    def test_monitor_win_when_all_enemy_houses_defeated(self):
        from dsh_rl.feedback import TerminalMonitor
        monitor = TerminalMonitor(None, None, 7, time.monotonic(), 300)
        outcome = monitor.observe(self._multi_house_state(enemy_defeated=True))
        self.assertEqual(outcome['kind'], 'win')
        self.assertIsNotNone(monitor.terminal_state)

    def test_monitor_game_over_without_winner_is_not_a_win(self):
        from dsh_rl.feedback import TerminalMonitor
        monitor = TerminalMonitor(None, None, 7, time.monotonic(), 300)
        outcome = monitor.observe(self._multi_house_state(enemy_defeated=False,
                                                          game_over=True))
        self.assertEqual(outcome['kind'], 'game_end')
        self.assertIsNone(monitor.terminal_state)


if __name__=='__main__':unittest.main()
