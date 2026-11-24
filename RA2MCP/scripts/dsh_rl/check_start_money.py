"""Quick check: starting money of human and AI after spawn.ini Credits change."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_episode import lockfile  # noqa: E402
from dsh_rl.full_env import RA2FullEnv  # noqa: E402

held = lockfile.acquire(ROOT / "build" / "check-money.lock")
env = None
try:
    env = RA2FullEnv(ROOT / "reports/check-money/episodes", max_seconds=60,
                     decision_seconds=0.5, game_dir=None, port=14521, instance=0,
                     kill_stale=True)
    obs, info = env.reset()
    print("hero money:", int(env.hero.money))
    for h in env.state.houses:
        print("house", h.array_index, repr(h.name), "human", h.current_player,
              "money", int(h.money), "defeated", h.defeated)
finally:
    if env is not None:
        try:
            env.close()
        except BaseException:  # noqa: BLE001
            pass
    lockfile.release(held)
