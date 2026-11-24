"""Short real interface validation. Scripted actions, ZERO model learning.

Confirms deploy/produce/place/unit orders and action masks, then exits the game.
This is not evidence that a neural policy has learned a construction order.
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_rl.full_env import RA2FullEnv
from dsh_rl import full_spec as S
from dsh_episode import lockfile
from sb3_contrib import MaskablePPO


class ValidationEnv(RA2FullEnv):
    def _control_metadata(self):
        name, params = super()._control_metadata()
        params.update(interface_test=True, optimizer_updates=0, controller="scripted_validation")
        return "full_interface_validation", params

    async def _commands(self, intents, source):
        return await super()._commands(intents, "interface_validation")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    held = lockfile.acquire(ROOT / "build/dsh-experiment.lock")
    env = None
    report = {"purpose": "interface_validation_only", "optimizer_updates": 0, "status": "starting"}
    try:
        env = ValidationEnv(out / "episodes", max_seconds=180, decision_seconds=0.5)
        x, info = env.reset()
        assert env.orders_ok == 0 and not hasattr(env, "rule"), "unexpected automatic economy commands"
        model = MaskablePPO("MlpPolicy", env, n_steps=4, batch_size=4, n_epochs=1, device="cpu", seed=7)
        mask = env.action_masks()
        for _ in range(32):
            action, _ = model.predict(x, action_masks=mask)
            assert mask[int(action)], "neural policy selected a masked action"
        report.update(observation_dim=len(x), action_count=int(env.action_space.n),
                      production_type_count=len(env.full.types), masked_sampling_pass=True,
                      initial_valid_actions=int(mask.sum()), catalog=env.full.manifest())
        step_count = 0
        def step(action):
            nonlocal step_count
            assert env.action_masks()[action], f"validation action masked: {action}"
            result = env.step(action)
            step_count += 1
            assert not result[2] and not result[3], "game ended during interface validation"
            return result

        def wait_for(check, seconds=25):
            deadline = time.monotonic() + seconds
            while not check():
                if time.monotonic() >= deadline:
                    raise RuntimeError("interface wait timeout")
                step(0)

        step(1)
        wait_for(lambda: S.base_cell(env.current) is not None)
        report["deploy_interface_pass"] = True
        tested = []
        for label in ("Allied Power Plant", "Allied Barracks"):
            slot = next(i for i, t in enumerate(env.full.types) if t["name"] == label)
            action = S.PRODUCE_START + slot
            wait_for(lambda: env.action_masks()[action])
            step(action)
            wait_for(lambda: any(o["type_name"] == label for o in S.pending(env.current)))
            pending_slot = next(i for i, o in enumerate(S.pending(env.current)) if o["type_name"] == label)
            step(S.PLACE_START + pending_slot * 8)
            wait_for(lambda: any(o["type_name"] == label and S.placed(o) for o in env.current["own_objects"]))
            tested.append(label)
        slot = next(i for i, t in enumerate(env.full.types) if t["name"] == "GI")
        wait_for(lambda: env.action_masks()[S.PRODUCE_START + slot])
        step(S.PRODUCE_START + slot)
        wait_for(lambda: any(o.get("type_name") == "GI" for o in S.units(env.current)))
        step(S.UNIT_START)  # STOP all controlled units.
        move = next(a for a in range(S.UNIT_START + 1, S.UNIT_START + 9) if env.action_masks()[a])
        step(move)
        assert model._n_updates == 0, "validation unexpectedly trained the model"
        report.update(status="passed", real_interface_steps=step_count, construction_tested=tested,
                      unit_production_tested="GI", unit_move_and_stop_pass=True,
                      native_orders_ok=env.orders_ok, native_errors=env.invalid_orders,
                      live_attack_tested=False, note="Attack target slots tested offline; no combat claim.")
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        try:
            if env:
                env.close()
        finally:
            lockfile.release(held)
            out.mkdir(parents=True, exist_ok=True)
            (out / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k != "catalog"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
