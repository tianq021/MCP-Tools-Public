"""Bounded IPC and shutdown for real-game workers; never fabricate a sample."""
import subprocess
import time
from stable_baselines3.common.vec_env import SubprocVecEnv


class FeedbackBatchInterrupted(RuntimeError):
    """Discard the unfinished rollout before PPO receives an unknown outcome."""
    def __init__(self, observations, dones, infos):
        super().__init__('Real-game feedback unavailable; unfinished rollout excluded')
        self.observations, self.dones, self.infos = observations, dones, infos


def resume_after_feedback(model, interruption):
    """Resume at actual post-step/reset observations without crossing episodes."""
    discarded = model.rollout_buffer.pos * model.n_envs
    model.rollout_buffer.reset()
    model._last_obs = interruption.observations
    model._last_episode_starts = interruption.dones
    return discarded


class BoundedPipe:
    def __init__(self, connection, timeout=120):
        self.connection, self.timeout = connection, timeout

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def recv(self):
        if not self.connection.poll(self.timeout):
            raise TimeoutError('Real-game worker did not respond within IPC deadline')
        return self.connection.recv()


class SafeSubprocVecEnv(SubprocVecEnv):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.remotes = tuple(BoundedPipe(r) for r in self.remotes)

    def step_wait(self):
        observations, rewards, dones, infos = super().step_wait()
        if any(info.get('feedback_valid') is False for info in infos):
            # SubprocVecEnv has already reset the failed replica. No fake
            # terminal observation/reward is exposed to PPO's rollout buffer.
            raise FeedbackBatchInterrupted(observations, dones, infos)
        return observations, rewards, dones, infos

    def close(self):
        if self.closed:
            return
        # Do not recv pending step results here: one worker may already be dead.
        for remote in self.remotes:
            try:
                remote.send(('close', None))
            except (BrokenPipeError, EOFError, OSError):
                pass
        deadline = time.monotonic() + 15
        for process in self.processes:
            process.join(max(0, deadline - time.monotonic()))
        for process in self.processes:
            if process.is_alive():
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=10)
                process.join(2)
        for remote in self.remotes:
            remote.close()
        self.waiting = False
        self.closed = True
