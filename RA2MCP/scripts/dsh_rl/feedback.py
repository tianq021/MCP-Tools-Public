"""Preserve observed outcomes; an unavailable outcome is never a zero-score loss."""
import asyncio
import time

from ra2yrproto import ra2yr
from dsh_episode import schema
from dsh_episode.hostility import is_opponent

FEEDBACK_VERSION = "terminal_feedback_v2"


class FeedbackUnavailable(RuntimeError):
    """A real next state/outcome could not be obtained. Exclude it from PPO."""


def outcome_flags(state, hero_id):
    """Extract every terminal flag of the original human house from a raw state.

    ``GetGameState`` exposes all houses, including the computer and neutral
    houses, so ``enemies_all_defeated`` can be derived reliably (the RL feedback
    path previously only looked at the human house, which made victories
    invisible unless the engine happened to set ``IsWinner`` first).
    """
    house = next((h for h in state.houses if int(h.self) == int(hero_id)), None)
    enemies = [h for h in state.houses
               if int(h.self) != int(hero_id)
               and not getattr(h, "is_human_player", False)
               and is_opponent({"owner": getattr(h, "name", "")})]
    return {
        "human_defeated": bool(house is not None and house.defeated),
        "human_is_loser": bool(house is not None and getattr(house, "is_loser", False)),
        "human_is_winner": bool(house is not None and getattr(house, "is_winner", False)),
        "human_is_game_over": bool(house is not None and getattr(house, "is_game_over", False)),
        "enemies_all_defeated": bool(enemies) and all(bool(h.defeated) for h in enemies),
    }


class TerminalMonitor:
    """Read through the existing client while the learner/other replicas wait.

    Runs on the environment's persistent I/O loop. It issues no game orders,
    never changes rewards, and keeps the first real terminal snapshot sticky.
    """
    def __init__(self, client, reader, house_id, started, max_seconds, interval=.25):
        self.client, self.reader, self.house_id = client, reader, house_id
        self.started, self.max_seconds, self.interval = started, max_seconds, interval
        self.state = self.terminal_state = None
        self.elapsed = self.terminal_elapsed = None
        self.outcome = None
        self.errors = []
        self.task = None

    def observe(self, state):
        elapsed = time.monotonic() - self.started
        self.state, self.elapsed = state, elapsed
        flags = outcome_flags(state, self.house_id)
        outcome = schema.classify_outcome(
            stage_name=ra2yr.LoadStage.Name(state.stage),
            elapsed_s=elapsed, max_seconds=self.max_seconds or None, **flags)
        house = next((h for h in state.houses if int(h.self) == int(self.house_id)), None)
        if self.terminal_state is None and outcome['kind'] in ('win', 'defeat', 'timeout'):
            # A timeout still needs a real observation with the original house.
            if house is not None:
                self.terminal_state, self.terminal_elapsed = state, elapsed
                self.outcome = outcome
        return outcome

    async def run(self):
        while self.terminal_state is None:
            try:
                state = await self.reader(self.client, strict=True)
                self.observe(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors.append({'elapsed_s': round(time.monotonic()-self.started, 3),
                                    'type': type(exc).__name__, 'message': str(exc)})
                self.errors = self.errors[-8:]
            await asyncio.sleep(self.interval)

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
