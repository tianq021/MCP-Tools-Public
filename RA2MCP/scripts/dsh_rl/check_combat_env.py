"""Sanity check for the combat-first env: scripted economy + military-only mask."""
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_episode import lockfile  # noqa: E402
from dsh_rl.combat_env import RA2CombatEnv  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402

held = lockfile.acquire(ROOT / "build" / "check-combat.lock")
env = None
try:
    env = RA2CombatEnv(ROOT / "reports/check-combat/episodes", max_seconds=90,
                       decision_seconds=0.5, game_dir=None, port=14521, instance=0,
                       kill_stale=True)
    obs, info = env.reset()
    row = env.current
    own = [o for o in row.get("own_objects", []) if S.placed(o)]
    buildings = [o.get("type_name") for o in own if S.is_building(o)]
    units = [o for o in own if not S.is_building(o) and not S.is_mcv(o)]
    print("after warmup: money", int(row.get("money", 0)),
          "buildings", buildings, "units", [o.get("type_name") for o in units])
    mask = env.action_masks()
    print("mask: total", int(mask.sum()), "unit-actions", int(mask[S.UNIT_START:].sum()))
    rng = np.random.default_rng(0)
    for i in range(60):
        mask = env.action_masks()
        valid = np.flatnonzero(mask)
        action = int(valid[rng.integers(len(valid))]) if len(valid) else 0
        obs, reward, term, trunc, info = env.step(action)
        if i % 20 == 0:
            row = env.current
            own = [o for o in row.get("own_objects", []) if S.placed(o)]
            print(f"step{i} t={row.get('t'):.1f}s money={int(row.get('money', 0))} "
                  f"buildings={sum(1 for o in own if S.is_building(o))} "
                  f"units={sum(1 for o in own if not S.is_building(o) and not S.is_mcv(o))} "
                  f"reward={reward:.3f}", flush=True)
        if term or trunc:
            break
finally:
    if env is not None:
        try:
            env.close()
        except BaseException:  # noqa: BLE001
            pass
    lockfile.release(held)
