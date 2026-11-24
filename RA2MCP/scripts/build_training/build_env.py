"""建设训练专用环境：RA2FullEnv（301 动作 / 1801 观测，模型自主建造）

与 V4 战斗奖励完全隔离：
- 奖励只来自 build_reward.build_reward_v1（每座新的己方已放置建筑 +1.0）
- 不读取、不修改 config/rewards.json / config/rewards-combat.json
- V4 的 RewardLedger 仍会在父类中构造（用于兼容 action-contract/catalog 校验），
  但其奖励不会被使用；本类覆写 _tick_rewards / _complete_reward_parts / _publish_reward_config。
- 动作空间与观测维度、模型结构、动作掩码均不改动（ACTION_N=301, OBS_DIM=1801）。

用法（由训练入口 train_build_v1.py 构造）：
    env = RA2BuildEnv(out/'episodes', max_seconds=75, decision_seconds=0.5,
                      game_dir='test-game-build', port=14531, instance=9)
"""
import json
import pathlib

import build_reward as BR
from dsh_rl.full_env import RA2FullEnv

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'training' / 'build_curriculum' / 'reward_build_v1.json'

# 与父类 _step 初始化 parts 的键保持一致；全部强制为 0（V4 战斗项禁用）。
_DISABLED_TICK_KEYS = ('opponent_hp', 'own_hp', 'invalid', 'terminal', 'time')


class RA2BuildEnv(RA2FullEnv):
    """无敌人建设训练环境（模型自主完成展开/建造/放置，奖励仅 building_created）。"""

    reward_version = 'build_reward_v1'

    def __init__(self, *args, **kwargs):
        self.build_reward_config = BR.load_config(CONFIG_PATH)
        self.build_reward_revision = BR.config_revision(self.build_reward_config)
        self.build_ledger = None
        self.build_step_events = 0
        self.build_log_path = None
        super().__init__(*args, **kwargs)
        self.build_log_path = self.out_dir.parent / 'build-events.jsonl'
        self.build_ledger = BR.BuildRewardLedger(self.build_reward_config)
        self.build_ledger.set_log_path(self.build_log_path)

    # ------------------------------------------------------------ reward plumbing
    def _publish_reward_config(self, config_path):
        """父类在 _initialize_controller 中调用；这里写的是本环境自己的配置快照。"""
        snap_dir = self.out_dir.parent / 'reward-configs'
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / ('build-' + self.build_reward_revision + '.json')).write_text(
            json.dumps(self.build_reward_config, ensure_ascii=False, indent=2),
            encoding='utf-8')
        active = {
            'reward_version': self.reward_version,
            'revision': self.build_reward_revision,
            'config_path': str(CONFIG_PATH),
            'episode_number': self.episode_count,
            'config': self.build_reward_config,
            'v4_combat_rewards': 'DISABLED (not read into the reward sum)',
        }
        temporary = self.out_dir.parent / 'active-build-rewards.json.tmp'
        temporary.write_text(json.dumps(active, ensure_ascii=False, indent=2),
                             encoding='utf-8')
        temporary.replace(self.out_dir.parent / 'active-build-rewards.json')

    def _control_metadata(self):
        return 'build_full', {
            'fixed_economy': False, 'warmup': False, 'build_stage': True,
            'reward_version': self.reward_version,
            'reward_revision': self.build_reward_revision,
            'reward_config': self.build_reward_config,
            'v4_combat_rewards': 'disabled',
            'action_contract': self.full.manifest(),
        }

    def _tick_rewards(self, before, after, invalid):
        # V4 战斗奖励在此环境中全部禁用（时间/血量/无效指令/胜负都不计分）。
        return {key: 0.0 for key in _DISABLED_TICK_KEYS}

    def _complete_reward_parts(self, before, after, parts):
        hero_ptr = int(self.hero.self) if getattr(self, 'hero', None) is not None else 0
        events = self.build_ledger.observe(
            after.get('own_objects') or [], hero_ptr=hero_ptr,
            step=self.transitions, frame=int(after.get('frame') or 0),
            t=after.get('t'))
        self.build_step_events += len(events)
        value = sum(float(event['reward']) for event in events)
        return {'building_created': value}

    async def _warmup_controller(self):
        await super()._warmup_controller()
        # 奖励确认从正式决策开始时计数（warmup 阶段不产生奖励）。
        self.build_ledger.set_episode(self.writer.episode_id, self.build_log_path)
        self.build_step_events = 0

    def _result_metrics_extra(self):
        ledger = getattr(self, 'build_ledger', None)
        summary = ledger.summary() if ledger is not None else {}
        return {
            'build_reward_version': self.reward_version,
            'build_reward_revision': self.build_reward_revision,
            'build_reward_total': summary.get('totals', {}).get('building_created', 0.0),
            'buildings_created_awarded': summary.get('counts', {}).get('building_created', 0),
            'buildings_via_mcv_deploy': summary.get('counts', {}).get('mcv_deploy', 0),
            'buildings_via_production': summary.get('counts', {}).get('production', 0),
            'build_reward_pending_candidates': summary.get('pending_candidates', 0),
            'build_reward_events': len(getattr(ledger, 'events', []) or []) if ledger else 0,
            'build_reward_event_log': str(self.build_log_path) if self.build_log_path else None,
            'build_steps_with_events': int(self.build_step_events),
        }

    def build_diagnostics(self):
        """训练脚本读取的实时建设奖励诊断。"""
        ledger = getattr(self, 'build_ledger', None)
        base = ledger.summary() if ledger is not None else {}
        base['current_episode_id'] = getattr(getattr(self, 'writer', None), 'episode_id', None)
        base['current_buildings_total'] = len(ledger.awarded) if ledger else 0
        base['event_log'] = str(self.build_log_path) if self.build_log_path else None
        base['config_path'] = str(CONFIG_PATH)
        return base
