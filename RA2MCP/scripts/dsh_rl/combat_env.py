"""Combat-first environment: the scripted economy runs, the model only fights.

Stage A of the curriculum: money/power/building/production are driven by the
existing RulePolicy fixed-economy scaffold, and the action mask exposes only the
military part of the full-control contract (STOP/MOVE/ATTACK/SCOut). The model
therefore learns combat without spending its credit on economy decisions. The
observation and action dimensions are unchanged, so weights can be transferred
to the full-control environment later (stage B: add economy back).

  DSH_COMBAT_ENV=1  makes train_shared.py use this class.
"""
import asyncio
import os
import time

import numpy as np

from dsh_rl import full_spec as S
from dsh_rl.full_env import RA2FullEnv
from dsh_rl.reward_config import load as load_rewards, RewardLedger
from dsh_episode.policy import RulePolicy

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
# 允许通过环境变量切换到新的奖励配置（默认仍是受保护的 V4 配置）。
COMBAT_REWARDS = ROOT / os.environ.get("DSH_COMBAT_REWARDS",
                                       "config/rewards-combat.json")


class RA2CombatEnv(RA2FullEnv):
    # 奖励版本随配置一起显式升级；旧 checkpoint 续训必须显式
    # --allow-reward-change，避免把不同奖励量纲的 return 混在一起。
    reward_version = (os.environ.get("DSH_REWARD_VERSION")
                      or RA2FullEnv.reward_version)
    min_combat_units = 4
    warmup_deadline_s = 120.0

    def _initialize_controller(self, initial, catalog):
        super()._initialize_controller(initial, catalog)
        # Fixed-economy scaffold: it owns deploy/build/produce choices.
        self.rule = RulePolicy(type_catalog=catalog)
        self.reward_ledger = RewardLedger(load_rewards(COMBAT_REWARDS))
        self._publish_reward_config(COMBAT_REWARDS)

    def _control_metadata(self):
        return "combat_full", {"fixed_economy": True, "warmup": True,
                               "combat_stage": True,
                               "reward_version": self.reward_version,
                               "reward_revision": self.reward_ledger.revision,
                               "reward_config": self.reward_ledger.config,
                               "action_contract": self.full.manifest()}

    async def _warmup_controller(self):
        """Let the scripted economy build a base and hand us a squad first."""
        await super()._warmup_controller()
        deadline = time.monotonic() + self.warmup_deadline_s
        while time.monotonic() < deadline and self.kind == "running":
            units = [o for o in self.current.get("own_objects", [])
                     if S.placed(o) and not S.is_building(o) and not S.is_mcv(o)]
            if len(units) >= self.min_combat_units:
                break
            await self._economy()
            await asyncio.sleep(0.5)
            await self._capture()

    async def _economy(self):
        """Run one scripted-economy decision; never let it break the episode."""
        try:
            intents = self.rule.step(self._context())
        except BaseException:  # noqa: BLE001
            return 0
        actions = [i for i in (intents or [])
                   if i.get("action") in ("DEPLOY", "PRODUCE", "PLACE")]
        if not actions:
            return 0
        return await self._commands(actions, "fixed_economy")

    def action_masks(self):
        base = super().action_masks()
        mask = np.zeros_like(base)
        mask[0] = True                    # noop always allowed
        mask[S.UNIT_START:] = base[S.UNIT_START:]   # military actions only
        return mask
