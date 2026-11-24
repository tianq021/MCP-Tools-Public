"""Offline tests: one dead game replica must not kill the shared training run."""
import pathlib
import sys
import tempfile
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/dsh_train"))
import spec_v2  # noqa: E402
from dsh_rl.env import RA2MicroEnv  # noqa: E402
from dsh_episode.runner import GameStateReadError

OBS = (spec_v2.OBS_DIM,)


class FakeGameEnv(RA2MicroEnv):
    """No game: launch/step behaviour is scripted to reproduce crash patterns."""

    RESET_RETRY_DELAYS = (0.0, 0.0)

    def __init__(self, out, fail_resets=0):
        super().__init__(out, max_seconds=180, decision_seconds=0.05)
        self.fail_resets = fail_resets
        self.launches = 0
        self.cleaned = 0
        self.transport_feedback_failures = True

    def _features(self):
        return np.ones(OBS, dtype=np.float32)

    async def _reset(self):
        self.launches += 1
        self.finished = False
        if self.fail_resets > 0:
            self.fail_resets -= 1
            raise GameStateReadError("bridge/INGAME timeout")
        return np.ones(OBS, dtype=np.float32), {"episode_id": f"fake-{self.launches}"}

    async def _step(self, action):
        raise GameStateReadError("real game state unavailable; refusing fabricated transition")

    async def _stop_client(self):
        pass

    def _cleanup_game(self):
        self.cleaned += 1
        self.runner = None


class CrashCacheEnv(FakeGameEnv):
    def __init__(self, out):
        super().__init__(out)
        self.calls = 0

    async def _step(self, action):
        self.calls += 1
        if self.calls == 1:
            return np.zeros(OBS, dtype=np.float32), 0.5, False, False, {}
        raise GameStateReadError("real game state unavailable; refusing fabricated transition")


class TestCrashResilience(unittest.TestCase):
    def test_unknown_feedback_is_excluded_transport_not_a_terminal_loss(self):
        with tempfile.TemporaryDirectory() as td:
            env = FakeGameEnv(td)
            obs, _ = env.reset()
            self.assertEqual(obs.shape, OBS)
            obs, reward, terminated, truncated, info = env.step(0)
            self.assertFalse(terminated)
            self.assertTrue(truncated)
            self.assertEqual(reward, 0.0)
            self.assertFalse(info["feedback_valid"])
            self.assertEqual(obs.shape, OBS)
            # The next reset must be able to relaunch a fresh replica.
            env.reset()
            self.assertEqual(env.launches, 2)
            env.close()

    def test_reset_retries_transient_launch_failure(self):
        with tempfile.TemporaryDirectory() as td:
            env = FakeGameEnv(td, fail_resets=2)
            obs, _ = env.reset()
            self.assertEqual(obs.shape, OBS)
            self.assertEqual(env.launches, 3)
            env.close()

    def test_reset_gives_up_after_bounded_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            env = FakeGameEnv(td, fail_resets=10)
            with self.assertRaises(RuntimeError):
                env.reset()
            self.assertEqual(env.launches, 3)
            env.close()

    def test_programming_errors_are_not_swallowed(self):
        with tempfile.TemporaryDirectory() as td:
            env = FakeGameEnv(td)
            env.reset()

            async def broken(action):
                raise IndexError("bug in action selection")

            env._step = broken
            with self.assertRaises(IndexError):
                env.step(0)
            env.close()

    def test_crash_returns_last_real_observation(self):
        with tempfile.TemporaryDirectory() as td:
            env = CrashCacheEnv(td)
            env.reset()
            obs, reward, terminated, _, _ = env.step(0)  # real step
            self.assertFalse(terminated)
            self.assertEqual(float(obs[0]), 0.0)
            obs, reward, terminated, _, info = env.step(1)  # game dies
            self.assertFalse(terminated)
            self.assertFalse(info["feedback_valid"])
            # No fabricated next state: exactly the last real observation.
            np.testing.assert_array_equal(obs, np.zeros(OBS, dtype=np.float32))
            env.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
