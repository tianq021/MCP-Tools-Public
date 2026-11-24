"""建设训练 V2 环境：RA2FullEnv + BUILD_ONLY 动作掩码 + 预部署基地 + V2 独立奖励。

关键设计（任务书第二/三/五节）：
- 301 动作 / 1801 观测保持不变；action_masks() 只放开：
    * 0            = 等待（noop）
    * 2..97        = PRODUCE（建筑/单位生产，来自真实动作映射 full_spec 的槽位）
    * 98..129      = PLACE（建筑放置，4 待放置槽 × 8 方向）
  屏蔽 DEPLOY(1) 与单位控制(130..300)。全范围内仍叠加原生合法性掩码（无效动作被屏蔽）。
- Episode 开始由环境执行脚本化 warmup：展开 MCV → 生产并放置 Power Plant，
  使模型从「已部署的基地」开始学习建设（第一阶段不要求模型学 MCV 展开；
  模型控制期间不存在任何固定脚本代替决策）。
- 预部署完成后才启动 V2 奖励账本并对当前实体做 prime()，因此初始 CY/电厂与初始单位不计入奖励。
- 奖励只来自 build_reward_v2（building_created +1.0 / unit_created +0.2），V4 战斗奖励禁用。

用法（由 train_build_v2.py 构造）：
    env = RA2BuildEnvV2(out/'episodes', max_seconds=100, decision_seconds=0.2,
                        game_dir='test-game-build', port=14531, instance=8)
"""
import asyncio
import json
import pathlib
import time

import numpy as np

import build_reward_v2 as BRV2
from dsh_rl import full_spec as S
from dsh_rl.full_env import RA2FullEnv

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'training' / 'build_curriculum' / 'reward_build_v2.json'

_DISABLED_TICK_KEYS = ('opponent_hp', 'own_hp', 'invalid', 'terminal', 'time')

# 允许的动作区段（来自真实动作映射常量，不硬编码猜测索引）
ALLOWED_ACTION_RANGES = (
    (0, 1),                       # noop 等待
    (S.PRODUCE_START, S.UNIT_START),  # PRODUCE + PLACE
)


class RA2BuildEnvV2(RA2FullEnv):
    """BUILD_ONLY 建设训练环境（预部署基地 + 建筑/单位生产奖励）。"""

    reward_version = 'build_reward_v2'
    setup_mode = 'predeployed'      # predeployed | mcv_only（第二阶段预留）
    setup_timeout_s = 90.0
    setup_deploy_retry_s = 1.2
    setup_place_retry = 6

    # ------------------------------------------------------------------ lifecycle
    def __init__(self, *args, setup_mode='predeployed', reward_config_path=None,
                 power_curriculum_masks=True, cap_curriculum_masks=True,
                 allow_unit_control=False, unit_control_mode=None,
                 focus_mcv_until_new_base=False, mcv_clear_deploy_only=False,
                 mcv_production_focus=False, focus_mcv_mask_noop=False, **kwargs):
        self.reward_config_path = pathlib.Path(reward_config_path) \
            if reward_config_path else BRV2.CONFIG_PATH
        self.build_reward_config = BRV2.load_config(self.reward_config_path)
        self.build_reward_revision = BRV2.config_revision(self.build_reward_config)
        # V2.2 训练期课程掩码（仍由模型决定建造内容/时机，只屏蔽无效或无收益动作）：
        #  - 缺电时只允许生产供电建筑（修复电力）
        #  - 某类型达到本局奖励上限后屏蔽再生产（防止同类刷屏/耗电失控）
        self.power_curriculum_masks = bool(power_curriculum_masks)
        self.cap_curriculum_masks = bool(cap_curriculum_masks)
        # V2.3/V2.4：单位控制课程
        #   off      = BUILD_ONLY（默认）
        #   full     = 开放全部单位控制（观察采矿车/战斗单位控制）
        #   mcv_only = 无 MCV 时专注建设；有 MCV 时只开放 MCV 的 STOP/MOVE/DEPLOY
        mode = unit_control_mode
        if mode is None:
            mode = 'full' if allow_unit_control else 'off'
        if mode not in ('off', 'full', 'mcv_only'):
            raise ValueError('unit_control_mode must be off/full/mcv_only')
        if setup_mode not in ('predeployed', 'none', 'mcv_only'):
            raise ValueError('setup_mode must be predeployed/none/mcv_only')
        self.unit_control_mode = mode
        self.allow_unit_control = mode != 'off'
        self.order_stats = {'deploy': 0, 'stop': 0, 'move': 0, 'attack': 0, 'other': 0,
                            'by_role': {'miner': 0, 'combat': 0, 'mcv': 0, 'unknown': 0},
                            'by_type': {}}
        self.new_base_events = []
        self._deploy_tracking = {}
        self._pending_deploy = None
        self._masked_action_fallbacks = 0
        # V2.8 MCV 展开专项课程
        self.focus_mcv_until_new_base = bool(focus_mcv_until_new_base)
        self.mcv_clear_deploy_only = bool(mcv_clear_deploy_only)
        self.mcv_production_focus = bool(mcv_production_focus)
        self.focus_mcv_mask_noop = bool(focus_mcv_mask_noop)
        self._new_base_this_episode = False
        self.setup_mode = setup_mode
        self.setup_info = {}
        self.setup_events = []
        self.build_ledger = None
        self.build_log_path = None
        self.setup_log_path = None
        super().__init__(*args, **kwargs)
        self.build_log_path = self.out_dir.parent / 'build-events.jsonl'
        self.setup_log_path = self.out_dir.parent / 'setup-events.jsonl'
        self.build_ledger = BRV2.BuildRewardLedgerV2(self.build_reward_config)
        self.build_ledger.set_log_path(self.build_log_path)

    # ------------------------------------------------------------- reward plumbing
    def _publish_reward_config(self, config_path):
        snap_dir = self.out_dir.parent / 'reward-configs'
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / ('build_v2-' + self.build_reward_revision + '.json')).write_text(
            json.dumps(self.build_reward_config, ensure_ascii=False, indent=2),
            encoding='utf-8')
        active = {
            'reward_version': self.reward_version,
            'revision': self.build_reward_revision,
            'config_path': str(self.reward_config_path),
            'episode_number': self.episode_count,
            'config': self.build_reward_config,
            'v1_build_rewards': 'superseded by V2 (V1 files untouched)',
            'v4_combat_rewards': 'DISABLED',
        }
        temporary = self.out_dir.parent / 'active-build-rewards-v2.json.tmp'
        temporary.write_text(json.dumps(active, ensure_ascii=False, indent=2),
                             encoding='utf-8')
        temporary.replace(self.out_dir.parent / 'active-build-rewards-v2.json')

    def _control_metadata(self):
        return 'build_v2_full', {
            'fixed_economy': False, 'warmup': 'scripted_predeploy_base',
            'build_only_mask': self.unit_control_mode == 'off',
            'unit_control_mode': self.unit_control_mode,
            'reward_version': self.reward_version,
            'reward_revision': self.build_reward_revision,
            'reward_config': self.build_reward_config,
            'v4_combat_rewards': 'disabled',
            'action_contract': self.full.manifest(),
        }

    def _tick_rewards(self, before, after, invalid):
        return {key: 0.0 for key in _DISABLED_TICK_KEYS}

    def _complete_reward_parts(self, before, after, parts):
        self._apply_pending_deploy()
        hero_ptr = int(self.hero.self) if getattr(self, 'hero', None) is not None else 0
        hero_row = {}
        for h in (after.get('houses') or []):
            if int(h.get('ptr') or 0) == hero_ptr:
                hero_row = h
                break
        events = self.build_ledger.observe(
            after.get('own_objects') or [], hero_ptr=hero_ptr,
            step=self.transitions, frame=int(after.get('frame') or 0),
            t=after.get('t'),
            power_output=int(hero_row.get('power_output') or 0),
            power_drain=int(hero_row.get('power_drain') or 0))
        for event in events:
            if event.get('kind') == 'building' and \
                    'Construction Yard' in str(event.get('type_name')):
                self.new_base_events.append({
                    'episode_id': event.get('episode_id'),
                    'frame': event.get('frame'), 'addr': event.get('addr'),
                    'cell': event.get('cell'), 'reward': event.get('reward'),
                    'new_base_bonus': event.get('new_base_bonus')})
                self._new_base_this_episode = True
        building = sum(float(e['reward']) for e in events if e['kind'] == 'building')
        unit = sum(float(e['reward']) for e in events if e['kind'] == 'unit')
        return {'building_created': building, 'unit_created': unit}

    # ------------------------------------------------------------------ action mask
    def _hero_house_row(self):
        if getattr(self, 'current', None) is None or getattr(self, 'hero', None) is None:
            return {}
        ptr = int(self.hero.self)
        for h in self.current.get('houses') or []:
            if int(h.get('ptr') or 0) == ptr:
                return h
        return {}

    def _power_state(self):
        row = self._hero_house_row()
        return int(row.get('power_output') or 0), int(row.get('power_drain') or 0)

    @staticmethod
    def _type_kind(t):
        return 'building' if t.get('type') == S.KINDS[0] else 'unit'

    def _apply_curriculum_masks(self, mask):
        """V2.2：电力修复约束 + 类型奖励上限约束（BUILD_ONLY 之上）。"""
        types = self.full.types if getattr(self, 'full', None) is not None else []
        ledger = getattr(self, 'build_ledger', None)
        counts = ledger.per_type_counts if ledger is not None else {'building': {}, 'unit': {}}
        out, drain = self._power_state()
        deficit = drain > 0 and out < drain
        rules = self.build_reward_config['rules']
        bcap = rules['building_created'].get('per_type_episode_cap')
        ucap = rules['unit_created'].get('per_type_episode_cap')
        masked_by_power = masked_by_cap = 0
        for slot, t in enumerate(types):
            action = S.PRODUCE_START + slot
            if not mask[action]:
                continue
            kind = self._type_kind(t)
            is_power_building = (kind == 'building'
                                 and float(t.get('power_bonus') or 0) > 0)
            if deficit and self.power_curriculum_masks and not is_power_building:
                mask[action] = False
                masked_by_power += 1
                continue
            if self.cap_curriculum_masks:
                name = t.get('name')
                if kind == 'building':
                    reached = bcap is not None and \
                        counts['building'].get(name, 0) >= int(bcap)
                    # 缺电时修复电力优先，即使该电厂类型已达上限也允许
                    if reached and not (deficit and is_power_building):
                        mask[action] = False
                        masked_by_cap += 1
                else:
                    reached = ucap is not None and \
                        counts['unit'].get(name, 0) >= int(ucap)
                    if reached:
                        mask[action] = False
                        masked_by_cap += 1
        self._last_curriculum_mask = {
            'power_deficit': bool(deficit),
            'power_output': int(out), 'power_drain': int(drain),
            'masked_by_power': masked_by_power, 'masked_by_cap': masked_by_cap,
            'power_masks_enabled': self.power_curriculum_masks,
            'cap_masks_enabled': self.cap_curriculum_masks,
        }
        return mask

    def _mcv_deploy_area_clear(self, mcv_cell, radius=3):
        """V2.8：部署区域是否清空（半径 radius 格内无己方建筑/单位）。"""
        try:
            cx, cy = int(mcv_cell[0]), int(mcv_cell[1])
        except Exception:  # noqa: BLE001
            return False
        for o in (self.current or {}).get('own_objects', []):
            if not (o.get('on_map') and not o.get('in_limbo')):
                continue
            if str(o.get('abstract')) not in ('ABSTRACT_TYPE_BUILDING',
                                             'ABSTRACT_TYPE_INFANTRY',
                                             'ABSTRACT_TYPE_UNIT'):
                continue
            c = o.get('cell') or [0, 0]
            if max(abs(int(c[0]) - cx), abs(int(c[1]) - cy)) <= radius:
                return False
        return True

    def _apply_mcv_only_masks(self, mask, native_mask):
        """V2.4：有 MCV 时只开放 MCV 的 STOP/MOVE 与 DEPLOY，其它单位控制保持屏蔽。

        防呆（V2.5）：MCV 原地连续 DEPLOY 失败（引擎会静默忽略无效位置）时，
        暂时屏蔽 DEPLOY 强制模型先移动 MCV 换个位置再展开，避免刷指令把桥接打挂。
        """
        own = {int(o.get('addr') or 0): str(o.get('type_name') or '')
               for o in (self.current or {}).get('own_objects', [])}
        cells = {int(o.get('addr') or 0): list(o.get('cell') or [-1, -1])
                 for o in (self.current or {}).get('own_objects', [])}
        mcv_addrs = [a for a, t in own.items() if 'Construction Vehicle' in t]
        if not mcv_addrs:
            return mask
        if native_mask[1]:
            mask[1] = True
        for action in range(S.UNIT_START, S.ACTION_N):
            if not native_mask[action]:
                continue
            try:
                intent = self.full.intent(action, self.current, self.starts, self.map_soa)
            except Exception:  # noqa: BLE001
                continue
            if not intent:
                continue
            addrs = [int(a) for a in (intent.get('object_addresses') or [])]
            if not addrs:
                continue
            types = [own.get(a, '') for a in addrs]
            if types and all('Construction Vehicle' in t for t in types):
                mask[action] = True
        # 部署防呆：同一 MCV 在同一格连续尝试 >=2 次 → 先屏蔽 DEPLOY，逼它移动
        if mask[1]:
            first = next(iter(self._deploy_tracking), None)
            frame = int((self.current or {}).get('frame') or 0)
            for addr in mcv_addrs:
                rec = self._deploy_tracking.get(addr)
                current_cell = cells.get(addr)
                if rec is None:
                    continue
                moved = current_cell != rec.get('cell')
                if moved:
                    self._deploy_tracking[addr] = {'count': 0, 'cell': current_cell,
                                                   'frame': frame}
                    continue
                if rec.get('count', 0) >= 2:
                    mask[1] = False
                    break
        # V2.8：部署位置必须周围清空（半径 3 格），否则屏蔽 DEPLOY 强制先移动
        clear_map = {}
        if self.mcv_clear_deploy_only:
            clear_map = {addr: self._mcv_deploy_area_clear(cells.get(addr) or [0, 0])
                         for addr in mcv_addrs}
            if not any(clear_map.values()):
                mask[1] = False
        focus = bool(self.focus_mcv_until_new_base and mcv_addrs
                     and not self._new_base_this_episode)
        if focus:
            # MCV 已产出、新基地尚未建立 → 屏蔽建设动作，只允许操作 MCV（移动/停止/部署）与等待
            for action in range(S.PRODUCE_START, S.UNIT_START):
                mask[action] = False
            if self.focus_mcv_mask_noop:
                # V2.8c：连等待也屏蔽，强制模型每一步都操作 MCV
                mask[0] = False
                if not mask.any():
                    mask[0] = True
        self._last_mcv_curriculum = {
            'focus_mcv': focus,
            'mcv_addrs': list(mcv_addrs),
            'deploy_area_clear': clear_map,
            'deploy_allowed': bool(mask[1]),
            'new_base_this_episode': self._new_base_this_episode,
        }
        return mask

    def _apply_mcv_production_focus(self, mask):
        """V2.8b：战车工厂已建成且尚无 MCV 时，只允许生产 MCV（其它单位生产屏蔽）。"""
        own = (self.current or {}).get('own_objects', [])
        if any('Construction Vehicle' in str(o.get('type_name')) and o.get('on_map')
               and not o.get('in_limbo') for o in own):
            return mask
        if self._new_base_this_episode:
            return mask
        has_factory = any('War Factory' in str(o.get('type_name')) and o.get('on_map')
                          and not o.get('in_limbo') for o in own)
        if not has_factory:
            return mask
        masked = 0
        for slot, t in enumerate(self.full.types if self.full else []):
            if self._type_kind(t) == 'building':
                continue
            if 'Construction Vehicle' in str(t.get('name')):
                continue
            action = S.PRODUCE_START + slot
            if mask[action]:
                mask[action] = False
                masked += 1
        self._last_mcv_production_focus = {'active': True, 'masked_actions': masked}
        return mask

    def action_masks(self):
        """BUILD_ONLY / full / mcv_only 三种单位控制课程，再叠加 V2.2 课程掩码。"""
        base = super().action_masks()
        if self.unit_control_mode == 'full':
            # 开放完整原生掩码：DEPLOY(1) 与单位控制(130..300) 均可用
            mask = base.copy()
        else:
            mask = np.zeros_like(base)
            for start, stop in ALLOWED_ACTION_RANGES:
                mask[start:stop] = base[start:stop]
            if self.unit_control_mode == 'mcv_only':
                mask = self._apply_mcv_only_masks(mask, base)
        if self.power_curriculum_masks or self.cap_curriculum_masks:
            try:
                mask = self._apply_curriculum_masks(mask)
            except Exception as exc:  # noqa: BLE001 - 掩码失败不得中断训练
                self._last_curriculum_mask = {'error': repr(exc)[:300]}
        if self.mcv_production_focus:
            try:
                mask = self._apply_mcv_production_focus(mask)
            except Exception:  # noqa: BLE001
                pass
        return mask

    def build_mask_stats(self):
        """诊断：当前 BUILD_ONLY 掩码的构成（用于训练期评估与故障定位）。"""
        try:
            mask = self.action_masks()
            base = super().action_masks()
            produce_start, place_start, unit_start = S.PRODUCE_START, S.PLACE_START, S.UNIT_START
            produce_slots = [i for i in range(produce_start, place_start) if mask[i]]
            names = {}
            for i in produce_slots:
                slot = i - produce_start
                if slot < len(self.full.types):
                    names[i] = self.full.types[slot]['name']
            return {
                'allowed_total': int(mask.sum()),
                'noop': bool(mask[0]),
                'produce_allowed': int(mask[produce_start:place_start].sum()),
                'place_allowed': int(mask[place_start:unit_start].sum()),
                'deploy_allowed_by_base_mask': bool(base[1]),
                'unit_control_allowed_by_base_mask': int(base[unit_start:].sum()),
                'produce_available_names': sorted(names.values()),
                'building_productions_available': sorted(set(names.values())),
                'blocked_total': int((~mask).sum()),
                'curriculum': dict(getattr(self, '_last_curriculum_mask', {})),
                'power_output': int(self._power_state()[0]),
                'power_drain': int(self._power_state()[1]),
            }
        except Exception as exc:  # noqa: BLE001
            return {'error': repr(exc)[:300]}

    # ------------------------------------------------------- V2.3 单位控制统计
    @staticmethod
    def _classify_unit(type_name):
        name = str(type_name or '')
        if 'Miner' in name or 'Harvester' in name:
            return 'miner'
        if 'Construction Vehicle' in name:
            return 'mcv'
        if 'Construction Yard' in name or 'Building' in name:
            return 'building'
        return 'combat'

    def _describe_order(self, intent):
        """把一次模型动作解析为「对哪些单位下了什么命令」（用于观察控制对象）。"""
        info = {'order_action': None, 'ordered_unit_types': [],
                'ordered_roles': [], 'ordered_count': 0}
        if not intent:
            return info
        action = str(intent.get('action') or '')
        info['order_action'] = action
        addrs = [int(a) for a in (intent.get('object_addresses') or [])]
        if not addrs:
            return info
        own = {int(o.get('addr') or 0): str(o.get('type_name') or '?')
               for o in (self.current or {}).get('own_objects', [])}
        types = [own.get(a, '?') for a in addrs]
        info.update({'ordered_unit_types': types[:9],
                     'ordered_addrs': addrs,
                     'ordered_roles': [r for r in sorted({self._classify_unit(t)
                                                          for t in types})
                                       if r != 'building'],
                     'ordered_count': len(addrs)})
        return info

    def _record_order_stats(self, info):
        if not self.allow_unit_control or not info:
            return
        action = info.get('order_action')
        key = {'DEPLOY': 'deploy', 'STOP': 'stop', 'MOVE': 'move',
               'ATTACK': 'attack'}.get(action, 'other')
        self.order_stats[key] = self.order_stats.get(key, 0) + 1
        for type_name in info.get('ordered_unit_types') or []:
            self.order_stats['by_type'][type_name] = \
                self.order_stats['by_type'].get(type_name, 0) + 1
        for role in info.get('ordered_roles') or []:
            if role in self.order_stats['by_role']:
                self.order_stats['by_role'][role] += 1
        # 记录 MCV 部署尝试（延迟到决策步结束才生效，避免掩码在 _select_intent 校验前变化）
        if action == 'DEPLOY':
            own = {int(o.get('addr') or 0): {'cell': list(o.get('cell') or [-1, -1]),
                                             'type': str(o.get('type_name') or '')}
                   for o in (self.current or {}).get('own_objects', [])}
            frame = int((self.current or {}).get('frame') or 0)
            self._pending_deploy = {
                'frame': frame,
                'items': [(addr, own[addr]['cell']) for addr in
                          (info.get('ordered_addrs') or [])
                          if addr in own and 'Construction Vehicle' in own[addr]['type']]}

    def _apply_pending_deploy(self):
        """把上一步的 DEPLOY 尝试记入防呆跟踪（同一格连续失败后强制先移动）。"""
        pending = getattr(self, '_pending_deploy', None)
        if not pending:
            return
        for addr, cell in pending.get('items') or []:
            rec = self._deploy_tracking.setdefault(
                addr, {'count': 0, 'cell': cell, 'frame': pending.get('frame', 0)})
            rec['count'] = rec.get('count', 0) + 1
            rec['frame'] = pending.get('frame', 0)
            rec['cell'] = cell
        self._pending_deploy = None

    def _select_intent(self, action):
        """V2：动态掩码可能在下发前变化（如 MCV 部署防呆），非法动作安全回退为 noop。"""
        try:
            valid = bool(self.action_masks()[int(action)])
        except Exception:  # noqa: BLE001
            valid = True
        if not valid:
            self._masked_action_fallbacks += 1
            return None
        self.last_action = int(action)
        return self.full.intent(int(action), self.current, self.starts, self.map_soa)

    def _action_details(self, action):
        details = super()._action_details(action)
        info = self._describe_order(details.get('selected_intent'))
        details.update(info)
        try:
            self._record_order_stats(info)
        except Exception:  # noqa: BLE001
            pass
        return details

    # ------------------------------------------------------------------- warmup
    def _setup_event(self, name, **data):
        record = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'event': name,
                  'episode_id': getattr(getattr(self, 'writer', None), 'episode_id', None),
                  **data}
        self.setup_events.append(record)
        if self.setup_log_path is not None:
            with self.setup_log_path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + '\n')
        print('[setup] %s %s' % (name, json.dumps(data, ensure_ascii=False)), flush=True)

    def _own_placed(self, needle_building=None, needle_unit=None, abstract=None):
        for obj in self.current.get('own_objects', []):
            if int(obj.get('health') or 0) <= 0:
                continue
            if not (obj.get('on_map') and not obj.get('in_limbo')):
                continue
            name = str(obj.get('type_name') or '')
            if needle_building and needle_building in name:
                return obj
            if needle_unit and needle_unit in name:
                return obj
            if abstract and obj.get('abstract') == abstract:
                return obj
        return None

    async def _send_setup_intent(self, intent, label):
        if intent is None:
            self._setup_event('setup_skip', label=label, reason='intent_none')
            return None
        records = await self.runner.execute(self.state, self.hero, intent)
        for record in records:
            record['decision_source'] = 'scripted_setup'
            try:
                self.writer.add_row(record)
            except Exception:  # noqa: BLE001
                pass
            if record.get('type') != 'action':
                self.invalid_orders += 1
            else:
                error = record.get('error')
                if error and 'already exists' not in error:
                    self.invalid_orders += 1
                elif not error:
                    self.orders_ok += 1
        rec = records[0] if records else {}
        self._setup_event('setup_command', label=label,
                          action=intent.get('action'),
                          error=rec.get('error'), ack_ms=rec.get('ack_ms'),
                          coordinates=rec.get('coordinates'),
                          frame=self.current.get('frame'))
        return rec

    async def _setup_predeployed_base(self):
        """脚本化预部署：展开 MCV -> 建造并放置电厂。模型控制从部署好的基地开始。"""
        t0 = time.monotonic()
        deadline = t0 + self.setup_timeout_s
        info = {'mode': self.setup_mode, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        self.setup_info = info
        await self._capture()
        info['start_frame'] = int(self.current.get('frame') or 0)
        info['start_money'] = int(self.current.get('money') or 0)

        # 1) 展开 MCV -> Construction Yard（重试：部分帧发送会被引擎忽略）
        deploy_attempts = 0
        cy = None
        while time.monotonic() < deadline:
            cy = self._own_placed(needle_building='Construction Yard')
            if cy is not None:
                break
            mcv = self._own_placed(needle_unit='Construction Vehicle')
            if mcv is None:
                await asyncio.sleep(0.3)
                await self._capture()
                continue
            intent = self.full.intent(1, self.current, self.starts, self.map_soa)
            await self._send_setup_intent(intent, 'deploy_mcv')
            deploy_attempts += 1
            await asyncio.sleep(self.setup_deploy_retry_s)
            await self._capture()
        info['deploy_attempts'] = deploy_attempts
        cy = self._own_placed(needle_building='Construction Yard')
        if cy is None:
            info['success'] = False
            info['error'] = 'mcv deploy failed (no construction yard)'
            self._setup_event('setup_failed', **{k: info[k] for k in ('error',)})
            raise RuntimeError('scripted_predeploy: ' + info['error'])
        info['construction_yard'] = {'addr': int(cy.get('addr') or 0),
                                     'cell': cy.get('cell'),
                                     'frame': int(self.current.get('frame') or 0)}

        # 2) 生产并放置 Power Plant
        pp_name = None
        for candidate in ('Allied Power Plant', 'Power Plant'):
            slot = next((i for i, t in enumerate(self.full.types)
                         if t.get('name') == candidate), None)
            if slot is not None:
                pp_name = candidate
                pp_slot = slot
                break
        if pp_name is None:
            info['success'] = False
            info['error'] = 'power plant type not in production catalog'
            self._setup_event('setup_failed', error=info['error'])
            raise RuntimeError('scripted_predeploy: ' + info['error'])
        pp_ptr = int(self.full.types[pp_slot]['pointer_self'])
        info['power_plant_type'] = pp_name
        # 发送生产（若已存在则跳过）
        pp_already = self._own_placed(needle_building='Power Plant')
        if pp_already is None:
            action = S.PRODUCE_START + pp_slot
            sent = 0
            while time.monotonic() < deadline:
                mask = self.full.masks(self.current, self.starts, self.map_soa)
                if mask[action]:
                    intent = self.full.intent(action, self.current, self.starts, self.map_soa)
                    await self._send_setup_intent(intent, 'produce_power_plant')
                    sent += 1
                    break
                await asyncio.sleep(0.3)
                await self._capture()
            # 等待生产完成（工厂 completed / building 在 limbo）
            pending_pp = None
            while time.monotonic() < deadline:
                ready = S.pending(self.current)
                pending_pp = next((o for o in ready
                                   if 'Power Plant' in str(o.get('type_name') or '')), None)
                if pending_pp is not None:
                    break
                await asyncio.sleep(0.4)
                await self._capture()
            if pending_pp is None:
                info['success'] = False
                info['error'] = 'power plant production timeout'
                self._setup_event('setup_failed', error=info['error'])
                raise RuntimeError('scripted_predeploy: ' + info['error'])
            info['power_plant_produced_frame'] = int(self.current.get('frame') or 0)
            # 放置到建造厂附近：用真实 PLACE 动作（原生 PlaceQuery 选合法格）
            placed = None
            anchor_base = cy.get('cell') or [0, 0]
            for attempt in range(self.setup_place_retry):
                ready = S.pending(self.current)
                which = next((i for i, o in enumerate(ready)
                              if int(o.get('addr') or 0) == int(pending_pp.get('addr') or 0)),
                             None)
                if which is None:
                    break
                for direction in (3, 2, 4, 1, 5, 0, 6, 7):
                    action_idx = S.PLACE_START + which * len(S.DIRECTIONS) + direction
                    intent = self.full.intent(action_idx, self.current, self.starts,
                                              self.map_soa)
                    if intent is None:
                        continue
                    rec = await self._send_setup_intent(intent, 'place_power_plant')
                    if rec and not rec.get('error'):
                        await asyncio.sleep(0.8)
                        await self._capture()
                        placed = self._own_placed(needle_building='Power Plant')
                        if placed is not None:
                            break
                if placed is not None:
                    break
                await asyncio.sleep(0.5)
                await self._capture()
            if placed is None:
                info['success'] = False
                info['error'] = 'power plant placement failed'
                self._setup_event('setup_failed', error=info['error'])
                raise RuntimeError('scripted_predeploy: ' + info['error'])
            info['power_plant'] = {'addr': int(placed.get('addr') or 0),
                                   'cell': placed.get('cell'),
                                   'frame': int(self.current.get('frame') or 0)}
        else:
            info['power_plant'] = {'addr': int(pp_already.get('addr') or 0),
                                   'cell': pp_already.get('cell'),
                                   'frame': int(self.current.get('frame') or 0),
                                   'already_present': True}

        # 3) 稳定一小段，确认基地存在、记录资金/电力
        await asyncio.sleep(0.6)
        await self._capture()
        hero = self.hero
        info.update({
            'success': True,
            'duration_s': round(time.monotonic() - t0, 2),
            'end_frame': int(self.current.get('frame') or 0),
            'end_money': int(getattr(hero, 'money', 0)),
            'power_output': int(getattr(hero, 'power_output', 0)),
            'power_drain': int(getattr(hero, 'power_drain', 0)),
            'own_buildings': [str(o.get('type_name')) for o in
                              self.current.get('own_objects', [])
                              if o.get('abstract') == 'ABSTRACT_TYPE_BUILDING'
                              and o.get('on_map') and not o.get('in_limbo')],
        })
        self._setup_event('setup_complete', **info)
        return info

    async def _warmup_controller(self):
        await super()._warmup_controller()
        if self.setup_mode == 'predeployed':
            await self._setup_predeployed_base()
        elif self.setup_mode == 'mcv_only':
            pass  # 第二阶段预留：不预部署，保留 MCV 让模型自己展开
        elif self.setup_mode == 'none':
            self.setup_info = {'mode': 'none', 'success': True,
                               'note': '无脚本化预部署：真实对局从原始开局开始'}
        else:
            raise RuntimeError('unknown setup_mode: %r' % self.setup_mode)
        # warmup 阶段不产生奖励：重置账本并把当前基地/单位登记为 preexisting
        self.build_ledger.set_episode(self.writer.episode_id, self.build_log_path)
        self.build_ledger.prime(self.current.get('own_objects') or [],
                                hero_ptr=int(self.hero.self))
        self.last_action = None
        self._deploy_tracking = {}
        self._new_base_this_episode = False
        self._setup_event('reward_ledger_started', episode_id=self.writer.episode_id,
                          primed_entities=self.build_ledger.summary()['preexisting_entities'],
                          setup_success=self.setup_info.get('success'))

    # ------------------------------------------------------------------ diagnostics
    def build_diagnostics(self):
        ledger = getattr(self, 'build_ledger', None)
        base = ledger.summary() if ledger is not None else {}
        own = self.current.get('own_objects', []) if getattr(self, 'current', None) else []
        buildings = [o for o in own if o.get('abstract') == 'ABSTRACT_TYPE_BUILDING'
                     and o.get('on_map') and not o.get('in_limbo')]
        units = [o for o in own if o.get('abstract') in
                 ('ABSTRACT_TYPE_INFANTRY', 'ABSTRACT_TYPE_UNIT')
                 and o.get('on_map') and not o.get('in_limbo')]
        base.update({
            'current_episode_id': getattr(getattr(self, 'writer', None), 'episode_id', None),
            'current_buildings': len(buildings),
            'current_building_types': sorted({str(o.get('type_name')) for o in buildings}),
            'current_units': len(units),
            'current_unit_types': sorted({str(o.get('type_name')) for o in units}),
            'current_money': int(self.current.get('money') or 0),
            'setup': dict(self.setup_info),
            'mask_stats': self.build_mask_stats(),
            'event_log': str(self.build_log_path) if self.build_log_path else None,
            'config_path': str(self.reward_config_path),
            'allow_unit_control': self.allow_unit_control,
            'unit_control_mode': self.unit_control_mode,
            'order_stats': dict(self.order_stats),
            'new_base_events': list(self.new_base_events),
            'new_base_count': len(self.new_base_events),
            'masked_action_fallbacks': int(self._masked_action_fallbacks),
            'mcv_curriculum': dict(getattr(self, '_last_mcv_curriculum', {})),
            'mcv_production_focus': dict(getattr(self, '_last_mcv_production_focus', {})),
        })
        return base

    def _result_metrics_extra(self):
        ledger = getattr(self, 'build_ledger', None)
        summary = ledger.summary() if ledger is not None else {}
        info = self.setup_info or {}
        return {
            'build_reward_version': self.reward_version,
            'build_reward_revision': self.build_reward_revision,
            'build_reward_total': round(float(sum(summary.get('totals', {}).values())), 4),
            'building_reward_total': summary.get('totals', {}).get('building_created', 0.0),
            'unit_reward_total': summary.get('totals', {}).get('unit_created', 0.0),
            'buildings_awarded': summary.get('counts', {}).get('building_created', 0),
            'units_awarded': summary.get('counts', {}).get('unit_created', 0),
            'building_types_awarded': summary.get('per_type_counts', {}).get('building', {}),
            'unit_types_awarded': summary.get('per_type_counts', {}).get('unit', {}),
            'setup_success': bool(info.get('success')),
            'setup_duration_s': info.get('duration_s'),
            'setup_deploy_attempts': info.get('deploy_attempts'),
            'setup_end_money': info.get('end_money'),
            'build_reward_event_log': str(self.build_log_path) if self.build_log_path else None,
            'unit_control_enabled': self.allow_unit_control,
            'unit_orders_deploy': self.order_stats.get('deploy', 0),
            'unit_orders_stop': self.order_stats.get('stop', 0),
            'unit_orders_move': self.order_stats.get('move', 0),
            'unit_order_roles': dict(self.order_stats.get('by_role') or {}),
            'unit_orders_by_type': dict(self.order_stats.get('by_type') or {}),
            'new_bases_created': len(self.new_base_events),
            'new_base_events': list(self.new_base_events),
        }
