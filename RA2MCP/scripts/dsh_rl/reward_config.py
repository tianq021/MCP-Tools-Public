"""Editable, validated reward contract and per-episode accounting."""
import copy
import hashlib
import json
import math
import pathlib

CONFIG_PATH = pathlib.Path(__file__).resolve().parents[2] / 'config/rewards.json'
# key, label, measurement, score, unit, step cap, episode cap, max scoring steps
# V4 reward contract (2026-10-30).  ``killed_hp_credit`` and the three
# kill/destroy items are fed by the reward-only truth tracker; ``attack`` now
# only counts attack commands whose target actually took damage shortly
# afterwards.  ``timeout`` is an opt-in experiment item (disabled by default in
# both shipped configs) and must be validated against a control run.
SPECS = [
    ('opponent_hp', '敌方掉血', 'HP', 2., 100., None, None, None),
    ('killed_hp_credit', '击杀补偿（击杀前最后可见生命）', 'HP', 2., 100., None, None, None),
    ('enemy_kill_infantry', '击杀敌方步兵', '个', 6., 1., None, None, None),
    ('enemy_kill_vehicle', '击杀敌方载具', '个', 12., 1., None, None, None),
    ('enemy_building_destroy', '摧毁敌方建筑', '座', 40., 1., None, None, None),
    ('first_kill', '首次击杀', '次', 5., 1., None, None, 1),
    ('first_building_destroy', '首次摧毁建筑', '次', 10., 1., None, None, 1),
    ('own_hp', '己方损失', 'HP', -.6, 100., None, 20., None),
    ('invalid', '无效指令', '条', -.1, 1., None, None, None),
    ('victory', '确认胜利', '次', 500., 1., None, None, 1),
    ('defeat', '确认战败', '次', -300., 1., None, None, 1),
    ('timeout', '超时结束（实验项，默认关闭）', '次', -50., 1., None, None, 1),
    ('time', '时间成本', '秒', -.002, 1., None, None, None),
    ('advance', '前出推进（逐战斗单位接近敌方出生点）', '格', .1, 1., .3, 20., None),
    ('first_advance', '首次抵达敌方基地附近', '次', 5., 1., None, None, 1),
    ('first_enemy_sight', '首次发现敌方建筑', '次', 2., 1., None, None, 1),
    ('progress', '侦察接近反馈', '势函数变化', .08, 1., None, None, None),
    ('exploration', '新增探图', '格', .001, 1., .02, .8, None),
    ('construction_yard', '首次展开基地', '新类型', .3, 1., None, None, None),
    ('first_building_type', '首次建筑类型', '新类型', .05, 1., None, None, None),
    ('first_unit_type', '首次单位类型', '新类型', .1, 1., None, None, None),
    ('unit_produced', '新造单位', '个', .02, 1., .05, .5, None),
    ('attack', '有效攻击指令（目标随后掉血）', '次', .1, 1., .1, 1., None),
    ('first_damage', '首次命中敌人', '次', 3., 1., None, None, 1),
    ('income', '资金净增加', '资金', 1., 10000., .1, 2., None),
]


def defaults():
    return {'schema_version': 1, 'rules': {k: dict(enabled=True, score=s, unit=u,
            step_cap=sc, episode_cap=ec, max_events=me) for k, _, _, s, u, sc, ec, me in SPECS}}


def migrate(data):
    """Fill reward items added after this config file was written.

    A running config may predate new terms (advance / milestones). Missing keys
    get the current default so training never crashes on an old saved file;
    unknown keys are still rejected because they signal a wrong/foreign config.
    """
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('奖励配置版本不兼容')
    rules = data.get('rules')
    if not isinstance(rules, dict):
        raise ValueError('奖励配置缺少 rules')
    known = {s[0] for s in SPECS}
    unknown = set(rules) - known
    if unknown:
        raise ValueError(f'奖励配置包含未知项目：{sorted(unknown)}')
    if set(rules) == known:
        return data
    merged = defaults()
    for key in known:
        if key in rules:
            merged['rules'][key] = rules[key]
    return merged


def validate(data):
    if data.get('schema_version') != 1 or set(data.get('rules', {})) != {s[0] for s in SPECS}:
        raise ValueError('奖励配置版本或项目不完整')
    for key, rule in data['rules'].items():
        if set(rule) != {'enabled', 'score', 'unit', 'step_cap', 'episode_cap', 'max_events'}:
            raise ValueError(f'{key}: 配置字段不完整')
        if type(rule['enabled']) is not bool:
            raise ValueError(f'{key}: enabled 必须为布尔值')
        for field in ('score', 'unit', 'step_cap', 'episode_cap', 'max_events'):
            value = rule[field]
            if value is None and field in ('step_cap', 'episode_cap', 'max_events'):
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f'{key}/{field}: 请输入有限数字')
            if field == 'unit' and value <= 0:
                raise ValueError(f'{key}: 计分单位必须大于 0')
            if field in ('step_cap', 'episode_cap', 'max_events') and value < 0:
                raise ValueError(f'{key}/{field}: 上限不能为负数')
            if field == 'max_events' and int(value) != value:
                raise ValueError(f'{key}: 次数必须为整数')
    return copy.deepcopy(data)


def revision(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load(path=CONFIG_PATH):
    raw = json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))
    return validate(migrate(raw))


def save(data, path=CONFIG_PATH):
    data = validate(data)
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return revision(data)


class RewardLedger:
    def __init__(self, config):
        self.config = validate(config)
        self.revision = revision(self.config)
        self.used = {key: 0. for key in self.config['rules']}
        self.events = {key: 0 for key in self.config['rules']}

    def apply(self, quantities):
        result = {}
        for key, rule in self.config['rules'].items():
            quantity = quantities.get(key, 0.)
            if not math.isfinite(quantity):
                raise ValueError(f'non-finite reward quantity: {key}')
            value = quantity * rule['score'] / rule['unit'] if rule['enabled'] else 0.
            if rule['max_events'] is not None and self.events[key] >= rule['max_events']:
                value = 0.
            magnitude = abs(value)
            if rule['step_cap'] is not None:
                magnitude = min(magnitude, rule['step_cap'])
            if rule['episode_cap'] is not None:
                magnitude = min(magnitude, max(0., rule['episode_cap'] - self.used[key]))
            result[key] = math.copysign(magnitude, value)
            self.used[key] += magnitude  # positive and negative values cannot cancel the cap
            if magnitude > 0:
                self.events[key] += 1
        return result
