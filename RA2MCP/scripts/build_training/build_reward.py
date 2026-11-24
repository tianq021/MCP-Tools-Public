"""独立建设奖励 v1：识别“新的己方已放置建筑”事件并计分。

设计约束（任务书第四步）：
- 奖励只来自 building_created = +1.0；原战斗/单位/经济/胜负奖励全部禁用。
- 触发条件：建筑属于玩家、真实存在于世界（on_map、非 in_limbo、health>0）、已成功放置。
- 通过至少 2 次连续观测确认后才发奖，避免单帧误判。
- 同一建筑实体（addr, type_name, cell）只发一次奖，防止重复领奖。
- 与 V4 战斗奖励完全独立：本模块只读取
  training/build_curriculum/reward_build_v1.json，不读写 config/rewards*.json。

用法（离线单元测试见 scripts/build_training/test_build_reward.py）：
    from build_reward import BuildRewardLedger, load_config
    ledger = BuildRewardLedger(load_config())
    ledger.set_episode('ep-1', log_path=Path('build-events.jsonl'))
    events = ledger.observe(row['own_objects'], hero_ptr=hero_self, step=1,
                            frame=frame, t=t)
"""
import hashlib
import json
import math
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'training' / 'build_curriculum' / 'reward_build_v1.json'

# 本模块允许出现的规则项；其余战斗奖励一律不允许进入本奖励契约。
ALLOWED_RULES = ('building_created',)
REQUIRED_FIELDS = {'enabled', 'score', 'unit', 'step_cap', 'episode_cap',
                   'max_events', 'description'}
CONFIRM_MIN, CONFIRM_MAX = 2, 10


class BuildRewardConfigError(ValueError):
    """独立建设奖励配置不合法。"""


def validate_config(data):
    if not isinstance(data, dict):
        raise BuildRewardConfigError('config must be an object')
    if data.get('schema_version') != 'build_reward_v1':
        raise BuildRewardConfigError('schema_version must be build_reward_v1')
    rules = data.get('rules')
    if not isinstance(rules, dict) or set(rules) != set(ALLOWED_RULES):
        raise BuildRewardConfigError(
            'rules must contain exactly %s (no combat reward items allowed)' % (ALLOWED_RULES,))
    rule = rules['building_created']
    if set(rule) != REQUIRED_FIELDS:
        raise BuildRewardConfigError(
            'building_created fields must be exactly %s' % sorted(REQUIRED_FIELDS))
    if type(rule['enabled']) is not bool:
        raise BuildRewardConfigError('building_created.enabled must be bool')
    if not isinstance(rule['unit'], str) or not rule['unit'].strip():
        raise BuildRewardConfigError('building_created.unit must be a non-empty label string')
    if type(rule['score']) not in (int, float) or not math.isfinite(rule['score']):
        raise BuildRewardConfigError('building_created.score must be a finite number')
    for field in ('step_cap', 'episode_cap', 'max_events'):
        value = rule[field]
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise BuildRewardConfigError('building_created.%s must be null or >= 0' % field)
    confirm = data.get('event_confirmation')
    if not isinstance(confirm, dict):
        raise BuildRewardConfigError('event_confirmation missing')
    frames = confirm.get('required_consecutive_observations')
    if type(frames) is not int or not (CONFIRM_MIN <= frames <= CONFIRM_MAX):
        raise BuildRewardConfigError(
            'required_consecutive_observations must be int in [%d, %d]' % (CONFIRM_MIN, CONFIRM_MAX))
    if not isinstance(confirm.get('dedupe_key'), str) or not confirm['dedupe_key']:
        raise BuildRewardConfigError('dedupe_key missing')
    for field in ('require_placed', 'require_owner_is_hero'):
        if type(confirm.get(field)) is not bool:
            raise BuildRewardConfigError('event_confirmation.%s must be bool' % field)
    if type(data.get('include_construction_yard_from_mcv')) is not bool:
        raise BuildRewardConfigError('include_construction_yard_from_mcv must be bool')
    disabled = data.get('disabled_original_rewards')
    if not isinstance(disabled, list) or not all(isinstance(x, str) for x in disabled):
        raise BuildRewardConfigError('disabled_original_rewards must be list[str]')
    if 'building_created' in disabled:
        raise BuildRewardConfigError('building_created must not be listed as disabled')
    protected = data.get('protected_originals')
    if not isinstance(protected, dict):
        raise BuildRewardConfigError('protected_originals missing')
    return data


def load_config(path=CONFIG_PATH):
    raw = json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))
    return validate_config(raw)


def config_revision(config):
    canonical = json.dumps(config, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def is_placed_building(obj):
    """己方对象里“真实存在且已放置”的建筑。"""
    if str(obj.get('abstract') or '') != 'ABSTRACT_TYPE_BUILDING':
        return False
    if not bool(obj.get('on_map')):
        return False
    if bool(obj.get('in_limbo')):
        return False
    return int(obj.get('health') or 0) > 0


def entity_identity(obj):
    cell = obj.get('cell') or [0, 0]
    return (int(obj.get('addr') or 0), str(obj.get('type_name') or '?'),
            int(cell[0]), int(cell[1]))


class BuildRewardLedger:
    """按 episode 累计的建设奖励账本（独立于 V4 RewardLedger）。"""

    def __init__(self, config):
        self.config = validate_config(config)
        self.revision = config_revision(self.config)
        self.rule = self.config['rules']['building_created']
        self.confirm_frames = int(self.config['event_confirmation']
                                  ['required_consecutive_observations'])
        self.include_cy = bool(self.config['include_construction_yard_from_mcv'])
        self.episode_id = None
        self.log_path = None
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self, episode_id=None):
        self.episode_id = episode_id
        self.candidates = {}
        self.awarded = set()
        self.events = []
        self.totals = {'building_created': 0.0}
        self.counts = {'building_created': 0, 'mcv_deploy': 0, 'production': 0,
                       'skipped_non_placed': 0, 'skipped_owner_mismatch': 0,
                       'skipped_construction_yard_disabled': 0}

    def set_episode(self, episode_id, log_path=None):
        self.reset(episode_id)
        if log_path is not None:
            self.log_path = pathlib.Path(log_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def set_log_path(self, log_path):
        self.log_path = pathlib.Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------- events
    def _log(self, record):
        if self.log_path is None:
            return
        with self.log_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')

    def observe(self, own_objects, hero_ptr, step, frame, t, wall=None):
        """用一帧己方对象观测更新账本；返回本帧新确认的奖励事件列表。"""
        wall = wall or time.strftime('%Y-%m-%dT%H:%M:%S')
        new_events = []
        seen_placed = set()
        for obj in own_objects or []:
            owner_ptr = obj.get('owner_ptr')
            if self.config['event_confirmation']['require_owner_is_hero'] and \
                    owner_ptr is not None and hero_ptr and int(owner_ptr) != int(hero_ptr):
                self.counts['skipped_owner_mismatch'] += 1
                continue
            if not is_placed_building(obj):
                if str(obj.get('abstract') or '') == 'ABSTRACT_TYPE_BUILDING':
                    self.counts['skipped_non_placed'] += 1
                continue
            type_name = str(obj.get('type_name') or '?')
            is_cy = 'Construction Yard' in type_name
            if is_cy and not self.include_cy:
                self.counts['skipped_construction_yard_disabled'] += 1
                continue
            identity = entity_identity(obj)
            seen_placed.add(identity)
            if identity in self.awarded:
                continue
            rec = self.candidates.get(identity)
            if rec is None:
                rec = {'confirmations': 0, 'first_step': int(step),
                       'first_frame': int(frame), 'type_name': type_name,
                       'addr': int(obj.get('addr') or 0),
                       'cell': list(identity[2:])}
                self.candidates[identity] = rec
            rec['confirmations'] += 1
            rec['last_step'] = int(step)
            rec['last_frame'] = int(frame)
            if rec['confirmations'] < self.confirm_frames:
                continue
            # 一个确认的新建筑 = 一个计分单位（unit 字段仅作人类可读标签）。
            via = 'mcv_deploy' if is_cy else 'production'
            value = float(self.rule['score']) if self.rule['enabled'] else 0.0
            if self.rule['max_events'] is not None and \
                    self.counts['building_created'] >= self.rule['max_events']:
                value = 0.0
            if self.rule['episode_cap'] is not None:
                value = min(value, max(0.0, self.rule['episode_cap']
                                       - self.totals['building_created']))
            if self.rule['step_cap'] is not None:
                value = min(value, float(self.rule['step_cap']))
            event = {
                'ts': wall,
                'event': 'building_created',
                'reward_rule': 'building_created',
                'reward_version': self.config['reward_version'],
                'reward_revision': self.revision,
                'episode_id': self.episode_id,
                'step': int(step),
                'frame': int(frame),
                't': round(float(t or 0.0), 3),
                'addr': rec['addr'],
                'type_name': rec['type_name'],
                'cell': rec['cell'],
                'via': via,
                'confirmations': rec['confirmations'],
                'first_seen_step': rec['first_step'],
                'first_seen_frame': rec['first_frame'],
                'confirmed_frame': int(frame),
                'reward': round(value, 6),
            }
            self.awarded.add(identity)
            self.candidates.pop(identity, None)
            self.totals['building_created'] += value
            self.counts['building_created'] += 1
            self.counts[via] += 1
            self.events.append(event)
            new_events.append(event)
            self._log(event)
        # 「连续观测」：本帧未再出现的候选实体丢弃确认进度，避免跨帧拼接确认。
        for identity in list(self.candidates):
            if identity not in seen_placed:
                del self.candidates[identity]
        return new_events

    # -------------------------------------------------------------- diagnostics
    def summary(self):
        return {
            'reward_version': self.config['reward_version'],
            'reward_revision': self.revision,
            'episode_id': self.episode_id,
            'totals': dict(self.totals),
            'counts': dict(self.counts),
            'awarded_entities': len(self.awarded),
            'pending_candidates': len(self.candidates),
            'confirm_frames': self.confirm_frames,
            'include_construction_yard_from_mcv': self.include_cy,
        }
