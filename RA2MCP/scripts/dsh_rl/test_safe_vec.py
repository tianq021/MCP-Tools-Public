import multiprocessing
import time
import unittest
import numpy as np
import gymnasium as gym
from dsh_rl.safe_vec import SafeSubprocVecEnv, BoundedPipe


class TinyEnv(gym.Env):
    observation_space = gym.spaces.Box(0, 1, (1,), dtype=np.float32)
    action_space = gym.spaces.Discrete(2)

    def reset(self, *, seed=None, options=None):
        return np.zeros(1, dtype=np.float32), {}

    def step(self, action):
        return np.zeros(1, dtype=np.float32), 0., False, False, {}


class TestSafeVec(unittest.TestCase):
    def test_worker_death_does_not_block_shutdown(self):
        env = SafeSubprocVecEnv([TinyEnv, TinyEnv], start_method='spawn')
        env.reset()
        env.processes[0].terminate()
        env.processes[0].join(5)
        env.waiting = True  # Reproduce old close() waiting for a missing result.
        started = time.monotonic()
        env.close()
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(all(not p.is_alive() for p in env.processes))

    def test_silent_pipe_times_out(self):
        left, right = multiprocessing.Pipe()
        try:
            with self.assertRaises(TimeoutError):
                BoundedPipe(left, .05).recv()
        finally:
            left.close()
            right.close()


if __name__ == '__main__':
    unittest.main()
