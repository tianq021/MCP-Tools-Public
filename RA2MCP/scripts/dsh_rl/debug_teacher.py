"""Debug the scripted teacher against the real game for ~45 seconds."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_episode import lockfile  # noqa: E402
from dsh_rl.full_env import RA2FullEnv  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402
from dsh_rl.scripted_policy import ScriptedTeacher, intent_label  # noqa: E402


def main():
    seconds = float(sys.argv[1] if len(sys.argv) > 1 else 60)
    seconds = max(55.0, seconds)
    steps_max = int(seconds / 0.5)
    out = ROOT / "reports/teacher-debug/20261030-debug"
    held = lockfile.acquire(ROOT / "build" / "dsh-experiment.lock")
    env = None
    try:
        env = RA2FullEnv(out / "episodes", max_seconds=seconds, decision_seconds=0.5,
                         game_dir=None, port=14521, instance=0, render_off=False)
        obs, info = env.reset()
        teacher = ScriptedTeacher()
        for step in range(steps_max):
            mask = env.action_masks()
            action, label = teacher.choose(env)
            row = env.current
            money = int(row.get("money", 0))
            own = [o for o in row.get("own_objects", []) if S.placed(o)]
            buildings = [o.get("type_name", "") for o in own if S.is_building(o)]
            pending = S.pending(row)
            true_actions = [i for i, m in enumerate(mask) if m and i != 0]
            desc = []
            if true_actions:
                for a in true_actions[:8]:
                    try:
                        intent = env.full.intent(a, row, env.starts, env.map_soa)
                    except Exception:  # noqa: BLE001
                        intent = None
                    if intent:
                        if intent["action"] == "PRODUCE":
                            desc.append(f"{a}:PRODUCE {intent['production_type']}")
                        elif intent["action"] == "PLACE":
                            desc.append(f"{a}:PLACE {intent.get('production_type')}")
                        else:
                            desc.append(f"{a}:{intent['action']}")
            print(f"step{step:3d} t={row.get('t', 0):6.1f}s money={money:6d} "
                  f"mask={int(mask.sum())} buildings={buildings} pending={len(pending)} "
                  f"-> action={action} label={label}", flush=True)
            if step % 5 == 0:
                print(f"        legal: {desc}", flush=True)
            obs, reward, term, trunc, info = env.step(int(action))
            if term or trunc:
                print("[debug] episode ended", flush=True)
                break
    finally:
        if env is not None:
            try:
                env.close()
            except BaseException:  # noqa: BLE001
                pass
        lockfile.release(held)


if __name__ == "__main__":
    main()
