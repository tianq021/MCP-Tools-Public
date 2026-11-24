"""建设训练 V2 独立奖励账本：新建筑 +1.0 / 新单位 +0.2。

与 V1（build_reward.py）独立；V1 配置与代码保持不变。
设计要点（任务书第五节）：
- 建筑与单位分别统计；建筑、单位都要求连续 2 帧确认（防单帧误判）。
- 单位必须真实出现在游戏世界（on_map、非 in_limbo、health>0、归属玩家）。
- 同一实体只奖励一次；按 addr 维护存活世代处理地址复用；校验所有权。
- 只奖励本 Episode 中新产生的实体：Episode 开始时调用 prime() 登记已有实体，
  脚本化预部署的 Construction Yard / Power Plant 与初始单位不会获得奖励。
- 单位奖励可按类型设置每局上限（per_type_episode_cap）。
- 按钮点击 / 无效操作 / 移动 / 战斗 / 胜负均不计分（本账本根本不为这些事件计分）。

事件写入 JSONL（append-only），字段含 kind/type/addr/cell/frame/reward 等，供审计。
"""
import hashlib
import json
import math
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'training' / 'build_curriculum' / 'reward_build_v2_1.json'

ALLOWED_RULES = ('building_created', 'unit_created')
RULE_FIELDS = {'enabled', 'score', 'unit', 'step_cap', 'episode_cap', 'max_events',
               'confirm_frames', 'per_type_episode_cap', 'description'}
# V2.1/V2.3：建筑规则允许额外的“缺电时电厂额外奖励”“新基地额外奖励”字段；
# 单位规则允许“MCV 生产额外奖励”字段（鼓励造出可展开的新基地车）
EXTRA_RULE_FIELDS = {
    'building_created': {'deficit_bonus_score', 'new_base_bonus_score'},
    'unit_created': {'mcv_bonus_score'},
}
UNIT_ABSTRACTS = ('ABSTRACT_TYPE_INFANTRY', 'ABSTRACT_TYPE_UNIT')


class BuildRewardV2ConfigError(ValueError):
    """V2 奖励配置不合法。"""


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_config(data):
    if not isinstance(data, dict):
        raise BuildRewardV2ConfigError('config must be an object')
    if data.get('schema_version') != 'build_reward_v2':
        raise BuildRewardV2ConfigError('schema_version must be build_reward_v2')
    rules = data.get('rules')
    if not isinstance(rules, dict) or set(rules) != set(ALLOWED_RULES):
        raise BuildRewardV2ConfigError('rules must contain exactly %s' % (ALLOWED_RULES,))
    for key, rule in rules.items():
        required_fields = RULE_FIELDS
        allowed_fields = RULE_FIELDS | EXTRA_RULE_FIELDS.get(key, set())
        if not required_fields <= set(rule) or (set(rule) - allowed_fields):
            raise BuildRewardV2ConfigError(
                '%s fields must contain %s and only allow extra %s'
                % (key, sorted(required_fields),
                   sorted(EXTRA_RULE_FIELDS.get(key, set()))))
        if type(rule['enabled']) is not bool:
            raise BuildRewardV2ConfigError('%s.enabled must be bool' % key)
        if not isinstance(rule['unit'], str) or not rule['unit'].strip():
            raise BuildRewardV2ConfigError('%s.unit must be a non-empty label' % key)
        if not _finite(rule['score']) or rule['score'] < 0:
            raise BuildRewardV2ConfigError('%s.score must be finite and >= 0' % key)
        frames = rule['confirm_frames']
        if type(frames) is not int or not (1 <= frames <= 10):
            raise BuildRewardV2ConfigError('%s.confirm_frames must be int in [1,10]' % key)
        cap = rule['per_type_episode_cap']
        if cap is not None and (type(cap) not in (int, float) or not math.isfinite(cap)
                                or cap < 0):
            raise BuildRewardV2ConfigError('%s.per_type_episode_cap must be null or >= 0'
                                           % key)
        if 'deficit_bonus_score' in rule:
            bonus = rule['deficit_bonus_score']
            if not _finite(bonus) or bonus < 0:
                raise BuildRewardV2ConfigError(
                    '%s.deficit_bonus_score must be finite and >= 0' % key)
        if 'new_base_bonus_score' in rule:
            bonus = rule['new_base_bonus_score']
            if not _finite(bonus) or bonus < 0:
                raise BuildRewardV2ConfigError(
                    '%s.new_base_bonus_score must be finite and >= 0' % key)
        if 'mcv_bonus_score' in rule:
            bonus = rule['mcv_bonus_score']
            if not _finite(bonus) or bonus < 0:
                raise BuildRewardV2ConfigError(
                    '%s.mcv_bonus_score must be finite and >= 0' % key)
    if rules['building_created']['confirm_frames'] < 2:
        raise BuildRewardV2ConfigError('building_created requires >= 2 confirm frames')
    if rules['unit_created']['confirm_frames'] < 1:
        raise BuildRewardV2ConfigError('unit_created requires >= 1 confirm frame')
    dedupe = data.get('dedupe')
    if not isinstance(dedupe, dict):
        raise BuildRewardV2ConfigError('dedupe missing')
    for field in ('handle_address_reuse', 'require_placed', 'require_owner_is_hero',
                  'only_new_in_episode', 'initial_setup_excluded'):
        if type(dedupe.get(field)) is not bool:
            raise BuildRewardV2ConfigError('dedupe.%s must be bool' % field)
    disabled = data.get('disabled_original_rewards')
    if not isinstance(disabled, list) or not all(isinstance(x, str) for x in disabled):
        raise BuildRewardV2ConfigError('disabled_original_rewards must be list[str]')
    for key in ALLOWED_RULES:
        if key in disabled:
            raise BuildRewardV2ConfigError('%s must not be listed as disabled' % key)
    if not isinstance(data.get('protected_originals'), dict):
        raise BuildRewardV2ConfigError('protected_originals missing')
    return data


def load_config(path=CONFIG_PATH):
    return validate_config(json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig')))


def config_revision(config):
    canonical = json.dumps(config, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _placed(obj):
    return (bool(obj.get('on_map')) and not bool(obj.get('in_limbo'))
            and int(obj.get('health') or 0) > 0)


def _kind_of(obj):
    abstract = str(obj.get('abstract') or '')
    if abstract == 'ABSTRACT_TYPE_BUILDING':
        return 'building'
    if abstract in UNIT_ABSTRACTS:
        return 'unit'
    return None


class BuildRewardLedgerV2:
    """按 episode 累计的建设/生产奖励账本（独立于 V1 与 V4）。"""

    def __init__(self, config):
        self.config = validate_config(config)
        self.revision = config_revision(self.config)
        self.dedupe = self.config['dedupe']
        self.reset()

    # ---------------------------------------------------------------- lifecycle
    def reset(self, episode_id=None):
        self.episode_id = episode_id
        self.log_path = getattr(self, 'log_path', None)
        self._gen = {'building': {}, 'unit': {}}   # addr -> generation
        self._alive = {'building': set(), 'unit': set()}
        self._preexisting = set()
        self._candidates = {}
        self._awarded = set()
        self.events = []
        self.totals = {'building_created': 0.0, 'unit_created': 0.0}
        self.counts = {
            'building_created': 0, 'unit_created': 0,
            'building_capped': 0, 'unit_capped': 0,
            'power_plant_deficit_bonuses': 0,
            'new_base_created': 0,
            'mcv_production_bonuses': 0,
            'skipped_non_placed': 0, 'skipped_owner_mismatch': 0,
            'skipped_preexisting': 0, 'skipped_disabled': 0,
        }
        self.per_type_counts = {'building': {}, 'unit': {}}
        self.per_type_totals = {'building': {}, 'unit': {}}

    def set_episode(self, episode_id, log_path=None):
        self.reset(episode_id)
        if log_path is not None:
            self.log_path = pathlib.Path(log_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def set_log_path(self, log_path):
        self.log_path = pathlib.Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, record):
        if self.log_path is None:
            return
        with self.log_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')

    # ------------------------------------------------------------------ helpers
    def _update_generations(self, kind, current_addrs):
        """维护存活世代：addr 消失后再出现视为新实体（处理地址复用）。"""
        prev = self._alive[kind]
        for addr in current_addrs:
            if addr not in prev:
                self._gen[kind][addr] = self._gen[kind].get(addr, 0) + 1
        self._alive[kind] = set(current_addrs)

    @staticmethod
    def _identity(kind, addr, generation, type_name, cell):
        if kind == 'building':
            return (kind, int(addr), int(generation), str(type_name),
                    int(cell[0]), int(cell[1]))
        return (kind, int(addr), int(generation), str(type_name))

    def _entity_identity(self, kind, obj):
        addr = int(obj.get('addr') or 0)
        generation = self._gen[kind].get(addr, 1)
        cell = obj.get('cell') or [0, 0]
        return self._identity(kind, addr, generation, obj.get('type_name'), cell)

    def _collect(self, own_objects, hero_ptr):
        """返回 [(kind, obj, identity)] 与按 kind 的当前 addr 集合。"""
        collected, addrs = [], {'building': [], 'unit': []}
        for obj in own_objects or []:
            kind = _kind_of(obj)
            if kind is None:
                continue
            owner_ptr = obj.get('owner_ptr')
            if (self.dedupe['require_owner_is_hero'] and owner_ptr is not None
                    and hero_ptr and int(owner_ptr) != int(hero_ptr)):
                self.counts['skipped_owner_mismatch'] += 1
                continue
            if not _placed(obj):
                self.counts['skipped_non_placed'] += 1
                continue
            addrs[kind].append(int(obj.get('addr') or 0))
            collected.append((kind, obj))
        return collected, addrs

    def prime(self, own_objects, hero_ptr):
        """Episode 开始时登记已有实体：这些实体不发奖（初始地图/脚本化基地）。"""
        collected, addrs = self._collect(own_objects, hero_ptr)
        for kind in ('building', 'unit'):
            self._update_generations(kind, addrs[kind])
        for kind, obj in collected:
            identity = self._entity_identity(kind, obj)
            self._preexisting.add(identity)

    # ------------------------------------------------------------------- observe
    def observe(self, own_objects, hero_ptr, step, frame, t, wall=None,
                power_output=0, power_drain=0):
        wall = wall or time.strftime('%Y-%m-%dT%H:%M:%S')
        collected, addrs = self._collect(own_objects, hero_ptr)
        for kind in ('building', 'unit'):
            self._update_generations(kind, addrs[kind])
        seen = set()
        new_events = []
        for kind, obj in collected:
            identity = self._entity_identity(kind, obj)
            seen.add(identity)
            if identity in self._preexisting:
                self.counts['skipped_preexisting'] += 1
                continue
            if identity in self._awarded:
                continue
            rec = self._candidates.get(identity)
            if rec is None:
                rec = {'confirmations': 0, 'first_frame': int(frame)}
                self._candidates[identity] = rec
            rec['confirmations'] += 1
            rec['last_frame'] = int(frame)
            rule_key = 'building_created' if kind == 'building' else 'unit_created'
            rule = self.config['rules'][rule_key]
            if rec['confirmations'] < int(rule['confirm_frames']):
                continue
            type_name = str(obj.get('type_name') or '?')
            # 类型上限（单位）
            per_type_cap = rule.get('per_type_episode_cap')
            capped = (per_type_cap is not None and
                      self.per_type_counts[kind].get(type_name, 0) >= int(per_type_cap))
            value = 0.0
            deficit_bonus = 0.0
            new_base_bonus = 0.0
            mcv_bonus = 0.0
            is_new_base = (kind == 'building' and 'Construction Yard' in type_name)
            is_mcv_unit = (kind == 'unit' and 'Construction Vehicle' in type_name)
            if rule['enabled'] and not capped:
                value = float(rule['score'])
                if rule['max_events'] is not None and \
                        self.counts[rule_key] >= int(rule['max_events']):
                    value = 0.0
                if rule['episode_cap'] is not None:
                    value = min(value, max(0.0, float(rule['episode_cap'])
                                           - self.totals[rule_key]))
                if rule['step_cap'] is not None:
                    value = min(value, float(rule['step_cap']))
                # V2.1：缺电时新建电厂额外奖励（电力不足时给明确信用分配）
                bonus_score = float(rule.get('deficit_bonus_score') or 0.0)
                deficit = int(power_drain or 0) > 0 and \
                    int(power_output or 0) < int(power_drain or 0)
                if (kind == 'building' and 'Power Plant' in type_name
                        and deficit and bonus_score > 0 and value > 0):
                    deficit_bonus = bonus_score
                    value += deficit_bonus
                    self.counts['power_plant_deficit_bonuses'] += 1
                # V2.3：MCV 展开成的新基地（Construction Yard）额外奖励
                new_base_score = float(rule.get('new_base_bonus_score') or 0.0)
                if is_new_base and new_base_score > 0 and value > 0:
                    new_base_bonus = new_base_score
                    value += new_base_bonus
                    self.counts['new_base_created'] += 1
                # V2.5：生产 MCV（基地车）额外奖励，鼓励造出可展开的新基地车
                mcv_bonus_score = float(rule.get('mcv_bonus_score') or 0.0)
                if is_mcv_unit and mcv_bonus_score > 0 and value > 0:
                    mcv_bonus = mcv_bonus_score
                    value += mcv_bonus
                    self.counts['mcv_production_bonuses'] += 1
            else:
                if not rule['enabled']:
                    self.counts['skipped_disabled'] += 1
                else:
                    self.counts['unit_capped' if kind == 'unit'
                                else 'building_capped'] += 1
            cell = obj.get('cell') or [0, 0]
            event = {
                'ts': wall,
                'event': 'entity_created',
                'kind': kind,
                'reward_rule': rule_key,
                'reward_version': self.config['reward_version'],
                'reward_revision': self.revision,
                'episode_id': self.episode_id,
                'step': int(step),
                'frame': int(frame),
                't': round(float(t or 0.0), 3),
                'addr': int(obj.get('addr') or 0),
                'type_name': type_name,
                'cell': [int(cell[0]), int(cell[1])],
                'generation': int(self._gen[kind].get(int(obj.get('addr') or 0), 1)),
                'confirmations': int(rec['confirmations']),
                'first_seen_frame': int(rec['first_frame']),
                'confirmed_frame': int(frame),
                'reward': round(float(value), 6),
                'base_reward': round(float(rule['score'])
                                     if rule['enabled'] and not capped else 0.0, 6),
                'deficit_bonus': round(float(deficit_bonus), 6),
                'new_base_bonus': round(float(new_base_bonus), 6),
                'mcv_bonus': round(float(mcv_bonus), 6),
                'via': 'mcv_deploy' if is_new_base else 'production',
                'power_output': int(power_output or 0),
                'power_drain': int(power_drain or 0),
                'capped': bool(capped),
            }
            self._awarded.add(identity)
            self._candidates.pop(identity, None)
            self.events.append(event)
            new_events.append(event)
            self._log(event)
            if value > 0:
                self.totals[rule_key] += value
                self.counts[rule_key] += 1
                self.per_type_counts[kind][type_name] = \
                    self.per_type_counts[kind].get(type_name, 0) + 1
                self.per_type_totals[kind][type_name] = round(
                    self.per_type_totals[kind].get(type_name, 0.0) + value, 4)
        # 「连续观测」：本帧未再出现的候选丢弃确认进度
        for identity in list(self._candidates):
            if identity not in seen:
                del self._candidates[identity]
        return new_events

    # -------------------------------------------------------------- diagnostics
    def summary(self):
        return {
            'reward_version': self.config['reward_version'],
            'reward_revision': self.revision,
            'episode_id': self.episode_id,
            'totals': {k: round(v, 4) for k, v in self.totals.items()},
            'counts': dict(self.counts),
            'per_type_counts': {k: dict(v) for k, v in self.per_type_counts.items()},
            'per_type_totals': {k: dict(v) for k, v in self.per_type_totals.items()},
            'awarded_entities': len(self._awarded),
            'preexisting_entities': len(self._preexisting),
            'pending_candidates': len(self._candidates),
            'confirm_frames': {k: int(v['confirm_frames'])
                               for k, v in self.config['rules'].items()},
        }
