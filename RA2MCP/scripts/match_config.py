"""Editable real-game match settings stored in config/spawn.ini.

Only settings that are understood from the bundled CnCNet 5 spawner source and
the game's own options are exposed.  Settings that change the action contract
(player country, tech level, map terrain) are shown read-only: changing them
requires a fresh model because the production catalogue / environment changes.
"""
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config/spawn.ini'
TEST_GAME_PATH = ROOT / 'test-game/spawn.ini'
HISTORY_DIR = ROOT / 'config/spawn-history'
MAPS_DIR = ROOT / "test-game/Maps/Yuri's Revenge"
MAP_META_PATH = ROOT / 'config/match-map.json'
TEAMS_PATH = ROOT / 'config/match-teams.json'

# Country list from the game's own rulesmd.ini ([Countries] order, [Sides] groups).
# name_cn is a label for this window, name is the game's display name.
COUNTRIES = [
    (0, '美国', 'America', 'Americans', '盟军'),
    (1, '亚洲联盟', 'Asian Alliance', 'Alliance', '盟军'),
    (2, '法国', 'France', 'French', '盟军'),
    (3, '德国', 'Germany', 'Germans', '盟军'),
    (4, '英国', 'Britain', 'British', '盟军'),
    (5, '非洲帝国', 'African Empire', 'Africans', '苏联'),
    (6, '阿拉伯联盟', 'Arab Union', 'Arabs', '苏联'),
    (7, '拉美联盟', 'Latin Confederation', 'Confederation', '苏联'),
    (8, '俄罗斯', 'Russia', 'Russians', '苏联'),
    (9, '尤里', 'Yuri', 'YuriCountry', '尤里'),
]
COUNTRY_LABELS = [(index, f'{index} {cn}（{side}）')
                  for index, cn, en, _key, side in COUNTRIES]
TEAM_OPTIONS = [(0, '无（各自为战）'), (1, '阵营 1'), (2, '阵营 2'), (3, '阵营 3'), (4, '阵营 4')]
ALLY_KEYS = ('One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight')

COLORS = [(0, '金'), (1, '红'), (2, '蓝'), (3, '绿'), (4, '橙'), (5, '青'), (6, '紫'), (7, '粉')]
DIFFICULTIES = [(0, '简单'), (1, '普通'), (2, '困难')]
AI_COUNTS = [(1, '1 个（2 人图）'), (2, '2 个（4 人图）'), (3, '3 个（4 人图）')]

# Map used for each AI count: the 2-player training map stays for 1 AI,
# extra AIs need a map with enough start waypoints.
MAP_BY_AI = {1: '2peaks.map', 2: '4_divide_and_conquer.map', 3: '4_divide_and_conquer.map'}

BOOLS = [
    ('Bases', '基地模式', '关闭后没有基地车，模型无法展开基地，不建议',
     '开始对局时是否发放基地车（MCV）。\n'
     '开：双方各有一台基地车，可以展开成建造厂并发展基地（训练必需）。\n'
     '关：没有基地车，只能造兵、不能建基地，模型无法展开基地，不建议关闭。'),
    ('Superweapons', '超级武器', None,
     '是否允许建造超级武器（盟军闪电风暴、苏联核弹、尤里心灵控制器等）。\n'
     '开：AI 和模型在满足条件后都能造超武，后期可能出现大规模杀伤。\n'
     '关：禁用所有超武，对局更可控。'),
    ('Crates', '随机箱子', None,
     '地图上是否随机刷新补给箱（钱、单位、升级、全图视野等）。\n'
     '开：双方都可能捡到箱子的随机奖励，增加不确定性。\n'
     '关：不刷新箱子，对局更可预测，训练更稳定。'),
    ('ShortGame', '快速结束（摧毁全部即胜）', '打开后对局更短，胜负反馈更密集',
     '胜负判定方式。\n'
     '开（快速结束）：摧毁对方全部建筑就判胜，不必清光残余单位，对局明显更短、胜负反馈更密集。\n'
     '关：常规规则，需要把对方单位和建筑都清掉（或对方投降）才结束。'),
    ('MCVRedeploy', '基地车可重新部署', None,
     '是否允许基地车（MCV）重新打包、移动到别处再展开。\n'
     '开：可以迁移基地，方便换位置。\n'
     '关：基地车展开后不能重新打包移动。'),
    ('FogOfWar', '战争迷雾', '改变可见性，会改变模型看到的信息',
     '视野规则。\n'
     '开：有战争迷雾，未探索/未侦察区域不可见。\n'
     '关：没有战争迷雾（黑幕仍会随探索揭开）。\n'
     '注意：改变它会让模型看到的信息完全不同，训练数据不可比，建议保持当前值。'),
    ('HarvesterTruce', '矿车休战', None,
     '是否让采矿车（矿车）不互相攻击。\n'
     '开：矿车之间不打，经济更稳定。\n'
     '关：矿车可互相攻击（通常没有实际战斗价值）。'),
    ('BridgeDestroy', '可摧毁桥梁', '改变地图通行性',
     '地图上的桥梁是否可以被摧毁。\n'
     '开：桥可被炸断，会改变地面单位的通行路线。\n'
     '关：桥梁不可摧毁，地图路线固定。'),
    ('MultiEngineer', '多工程师占领', None,
     '是否允许用多个工程师占领同一建筑。\n'
     '开：可以多工程师同时占领、加快占领速度。\n'
     '关：单人工程师按常规速度占领。'),
    ('AlliesAllowed', '允许结盟', None,
     '多人规则里是否允许玩家之间结盟。\n'
     '单机打电脑时影响小；有多个真人玩家时决定能否点“结盟”。'),
    ('BuildOffAlly', '盟友建造', None,
     '是否允许在盟友的建筑附近建造。\n'
     '开：可以贴着盟友基地建造，联合作战时更方便。\n'
     '关：只能在自己基地范围附近建造。'),
]

# key spec: every spec maps one UI row to one or more spawn.ini keys.
SPECS = [
    dict(section='Settings', keys=['Color'], label='玩家颜色', kind='enum', options=COLORS,
         note='房主（模型）颜色',
         help='模型（人类玩家）的房子颜色，只影响画面，不进入模型观测特征。\n'
              '调色板：0 金、1 红、2 蓝、3 绿、4 橙、5 青、6 紫、7 粉。\n'
              '写入 spawn.ini 的 [Settings] Color。改完下一局生效，不影响训练。'),
    dict(section='HouseColors', keys=['Multi1', 'Multi2'], label='电脑颜色', kind='enum', options=COLORS,
         note='所有电脑槽位写同一颜色',
         help='电脑（AI）的颜色，所有电脑统一写同一个颜色。\n'
              '调色板：0 金、1 红、2 蓝、3 绿、4 橙、5 青、6 紫、7 粉。\n'
              '写入 [HouseColors] Multi1..Multi(N+1)。只影响画面，不进入模型观测。'),
    dict(section='Settings', keys=['AIPlayers'], label='电脑数量', kind='enum', options=AI_COUNTS,
         note='>1 时自动换成 4 人地图',
         help='对局里电脑玩家的数量（1～3 个）。\n'
              '当前训练地图 Twin Peaks 只有 2 个出生点，只能放 1 个电脑；\n'
              '选 2～3 个时会自动把地图换成 4_divide_and_conquer.map（4 个出生点），\n'
              '并同步改写 PlayerCount / UIMapName / test-game/spawnmap.ini。\n'
              '注意：地图换了模型看到的地形分布会变，可以继续训练但评估要分开看；\n'
              '多电脑目前是实验特性，建议先确认稳定再长期使用。'),
    dict(section='Settings', keys=['AIDifficulty'], label='电脑难度', kind='enum', options=DIFFICULTIES,
         help='电脑 AI 的难度：0 简单 / 1 普通 / 2 困难。\n'
              '难度越高，电脑发展越快、进攻越积极，对局压力越大。\n'
              '它不改变模型动作空间，可以随时改；但不同难度的分数不能直接横比。'),
    dict(section='Settings', keys=['Credits'], label='初始资金', kind='int', minimum=0, maximum=100000,
         help='双方开局资金（Credits），默认 10000。\n'
              '影响经济起点：资金越高，开局造得越快、对局越早进入战斗。\n'
              '改成 0 也不是“没钱”，只是开局没有额外资金；一般保持默认即可。'),
    dict(section='Settings', keys=['UnitCount'], label='初始单位', kind='int', minimum=0, maximum=10,
         help='开局赠送的额外单位数量（UnitCount），一般 0～10，默认 0。\n'
              '0 = 只有基地车；数字越大开局兵力越多，开局就不平衡、训练意义下降。'),
    dict(section='Settings', keys=['GameSpeed'], label='游戏速度', kind='int', minimum=0, maximum=6,
         note='1 = 最快（约 62 帧/秒）；数字越大越慢，6 约 10 帧/秒',
         help='游戏自身的运行速度。实测：1 = 最快（约 62 帧/秒），数字越大越慢，6 约 10 帧/秒。\n'
              '它不会提高训练吞吐量，只会放慢游戏；训练用的时间成本按真实秒计算，调慢会让奖励失真。\n'
              '建议保持 1。提高吞吐量请用决策间隔（-DecisionSeconds）。'),
    dict(section='Settings', keys=['Seed'], label='随机种子', kind='int', minimum=0, maximum=2 ** 31 - 1,
         help='游戏随机数种子（Seed），影响出生点、箱子等随机结果。\n'
              '默认 20261028 固定，方便复现；一般不用改。'),
] + [
    dict(section='Settings', keys=[key], label=label, kind='bool', note=note, help=help_text)
    for key, label, note, help_text in BOOLS
] + [
    # Derived from AIPlayers on save; kept in the spec so validation accepts it.
    dict(section='Settings', keys=['PlayerCount'], label='玩家数量', kind='int',
         minimum=1, maximum=8, hidden=True),
    # Player country comes from the '阵营 / 国家' panel; hidden from the generic rows.
    dict(section='Settings', keys=['Side'], label='玩家国家', kind='enum',
         options=COUNTRY_LABELS, hidden=True),
]

LOCKED = [
    dict(section='Settings', keys=['TechLevel'], label='科技等级',
         reason='改科技等级会改变可生产类型，现有模型不能续训'),
]

# Baseline used by the editor's "填入基线默认值" button (current training setup).
BASELINE = {
    ('Settings', 'Side'): '0',
    ('Settings', 'Color'): '2',
    ('HouseColors', 'Multi1'): '1',
    ('HouseColors', 'Multi2'): '1',
    ('Settings', 'AIPlayers'): '1',
    ('Settings', 'PlayerCount'): '2',
    ('Settings', 'AIDifficulty'): '1',
    ('Settings', 'Credits'): '10000',
    ('Settings', 'UnitCount'): '0',
    ('Settings', 'GameSpeed'): '1',
    ('Settings', 'Seed'): '20261028',
    ('Settings', 'Bases'): 'Yes',
    ('Settings', 'Superweapons'): 'Yes',
    ('Settings', 'Crates'): 'No',
    ('Settings', 'ShortGame'): 'No',
    ('Settings', 'MCVRedeploy'): 'Yes',
    ('Settings', 'FogOfWar'): 'No',
    ('Settings', 'HarvesterTruce'): 'No',
    ('Settings', 'BridgeDestroy'): 'Yes',
    ('Settings', 'MultiEngineer'): 'No',
    ('Settings', 'AlliesAllowed'): 'No',
    ('Settings', 'BuildOffAlly'): 'Yes',
}


def _split_lines(text):
    return re.split(r'\r\n|\n|\r', text)


def parse(text):
    """Return {(section, key): value} preserving the file structure elsewhere."""
    values = {}
    section = None
    for line in _split_lines(text):
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip()
            continue
        if section and '=' in line:
            key, value = line.split('=', 1)
            values[(section, key.strip())] = value.strip()
    return values


def read_text(path=CONFIG_PATH):
    return pathlib.Path(path).read_text(encoding='utf-8-sig')


def load(path=CONFIG_PATH):
    return parse(read_text(path))


def read_map_info(path):
    """Parse a YR .map INI: display name, start waypoints and size."""
    text = pathlib.Path(path).read_text(encoding='utf-8-sig', errors='replace')
    section = None
    name, width, height = None, None, None
    starts = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1].strip()
            continue
        if '=' not in stripped or section is None:
            continue
        key, value = stripped.split('=', 1)
        key, value = key.strip(), value.strip()
        if section == 'Basic':
            if key == 'Name' and value:
                name = value
        elif section == 'Header':
            if key == 'Width' and value.isdigit():
                width = int(value)
            elif key == 'Height' and value.isdigit():
                height = int(value)
        elif section == 'Waypoints':
            try:
                index = int(key)
            except ValueError:
                continue
            if 0 <= index <= 7:
                starts.add(index)
    return {'file': pathlib.Path(path).name, 'name': name or pathlib.Path(path).stem,
            'players': len(starts), 'width': width, 'height': height}


def map_path(file_name, maps_dir=None):
    return pathlib.Path(maps_dir or MAPS_DIR) / file_name


def current_map(maps_dir=None, map_meta_path=MAP_META_PATH):
    """Map currently configured for the match (sidecar file, default 2peaks)."""
    maps_dir = pathlib.Path(maps_dir or MAPS_DIR)
    map_meta_path = pathlib.Path(map_meta_path)
    file_name = None
    try:
        data = json.loads(map_meta_path.read_text(encoding='utf-8-sig'))
        file_name = data.get('file')
    except (OSError, ValueError):
        pass
    candidates = [file_name, '2peaks.map']
    for candidate in candidates:
        if candidate and map_path(candidate, maps_dir).exists():
            return read_map_info(map_path(candidate, maps_dir))
    return {'file': file_name or '2peaks.map', 'name': '?', 'players': 0,
            'width': None, 'height': None}


def _normalize_bool(value):
    text = str(value).strip().lower()
    if text in ('yes', 'true', '1', 'on'):
        return 'Yes'
    if text in ('no', 'false', '0', 'off'):
        return 'No'
    raise ValueError(f'布尔值只能是 Yes/No：{value}')


def normalize(spec, value):
    if spec['kind'] == 'bool':
        return _normalize_bool(value)
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{spec['label']}：请输入整数") from None
    if spec['kind'] == 'int':
        if not spec['minimum'] <= number <= spec['maximum']:
            raise ValueError(f"{spec['label']}：范围 {spec['minimum']}～{spec['maximum']}")
    elif spec['kind'] == 'enum':
        allowed = {option for option, _ in spec['options']}
        if number not in allowed:
            raise ValueError(f"{spec['label']}：只能填 {sorted(allowed)}")
    return str(number)


def validate_updates(updates):
    """updates: {(section, key): value}; returns normalized updates."""
    by_key = {(spec['section'], key): spec for spec in SPECS for key in spec['keys']}
    result = {}
    for (section, key), value in updates.items():
        spec = by_key.get((section, key))
        if spec is None:
            raise ValueError(f'不支持的对局设置：{section}/{key}')
        result[(section, key)] = normalize(spec, value)
    return result


def set_values(text, updates, ending='\n'):
    lines = _split_lines(text)
    wanted = {key: value for key, value in updates.items()}
    seen = set()
    section = None
    output = []
    last_in_section = {}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1].strip()
        elif section and stripped and not stripped.startswith(';') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if (section, key) in wanted:
                value = wanted[(section, key)]
                line = f'{key}={value}'
                seen.add((section, key))
            last_in_section[section] = len(output)
        output.append(line)
    missing = [item for item in wanted if item not in seen]
    for (section, key) in sorted(missing, key=lambda item: (item[0], item[1])):
        insert_at = last_in_section.get(section)
        if insert_at is None:
            output.extend(['', f'[{section}]', f'{key}={wanted[(section, key)]}'])
        else:
            output.insert(insert_at + 1, f'{key}={wanted[(section, key)]}')
            last_in_section[section] = insert_at + 1
    return ending.join(output)


def revision(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def file_revision(path=CONFIG_PATH):
    """Hash raw file bytes, matching the environment fingerprint in episode metadata."""
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _map_plan(updates, maps_dir):
    ai_count = int(updates.get(('Settings', 'AIPlayers'), '1'))
    file_name = MAP_BY_AI.get(ai_count)
    if file_name is None:
        raise ValueError(f'电脑数量 {ai_count} 暂无对应地图')
    path = map_path(file_name, maps_dir)
    if not path.exists():
        raise ValueError(f'地图文件不存在：{path}')
    info = read_map_info(path)
    if info['players'] < ai_count + 1:
        raise ValueError(f"地图 {info['file']} 只有 {info['players']} 个出生点，放不下 {ai_count} 个电脑")
    return path, info


def load_match_state(path=CONFIG_PATH, teams_path=TEAMS_PATH):
    """Player/AI country indices and team numbers for the '阵营 / 国家' panel."""
    values = load(path)
    ai_count = int(values.get(('Settings', 'AIPlayers'), '1'))
    player_country = int(values.get(('Settings', 'Side'), '0'))
    ai_countries = [int(values.get(('HouseCountries', f'Multi{index + 2}'), '0'))
                    for index in range(3)]
    teams = {'player': 0, 'ai': [0, 0, 0]}
    try:
        saved = json.loads(pathlib.Path(teams_path).read_text(encoding='utf-8-sig'))
        teams['player'] = int(saved.get('player', 0))
        ai_teams = saved.get('ai') or []
        for index in range(3):
            teams['ai'][index] = int(ai_teams[index]) if index < len(ai_teams) else 0
    except (OSError, ValueError, TypeError):
        pass
    return {'ai_count': ai_count, 'player_country': player_country,
            'ai_countries': ai_countries, 'teams': teams}


def _valid_country(value, label):
    index = int(value)
    if index not in {country[0] for country in COUNTRIES}:
        raise ValueError(f'{label}：国家索引只能是 0-9')
    return index


def _valid_team(value, label):
    team = int(value)
    if team not in {option[0] for option in TEAM_OPTIONS}:
        raise ValueError(f'{label}：阵营编号只能是 0（无）或 1-4')
    return team


def validate_match_state(match, ai_count):
    player_country = _valid_country(match['player_country'], '玩家')
    ai_countries = [_valid_country(value, f'电脑{index + 1}')
                    for index, value in enumerate(list(match['ai_countries'])[:ai_count])]
    if len(ai_countries) < ai_count:
        raise ValueError('电脑数量与电脑国家列表不一致')
    player_team = _valid_team(match['teams']['player'], '玩家')
    ai_teams = [_valid_team(match['teams']['ai'][index], f'电脑{index + 1}')
                for index in range(ai_count)]
    return {'player_country': player_country, 'ai_countries': ai_countries,
            'player_team': player_team, 'ai_teams': ai_teams}


def _strip_alliance_sections(text):
    """Remove any previous [MultiN_Alliances] blocks before writing new teams."""
    lines = _split_lines(text)
    output = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            skipping = re.fullmatch(r'Multi\d+_Alliances', stripped[1:-1].strip()) is not None
            if skipping:
                continue
        if skipping:
            continue
        output.append(line)
    return output


def apply_alliances(text, player_team, ai_teams, ending='\n'):
    """Write [MultiN_Alliances] blocks: 1-4 mean team numbers, 0 means no team."""
    lines = _strip_alliance_sections(text)
    teams = [player_team] + list(ai_teams)
    extra = []
    for house_id, team in enumerate(teams, start=1):
        if team <= 0:
            continue
        allies = [other for other, other_team in enumerate(teams, start=1)
                  if other != house_id and other_team == team]
        if not allies:
            continue
        extra += ['', f'[Multi{house_id}_Alliances]']
        for index, ally in enumerate(allies[:len(ALLY_KEYS)]):
            extra.append(f'HouseAlly{ALLY_KEYS[index]}={ally - 1}')
    while lines and not lines[-1].strip():
        lines.pop()
    return ending.join(lines + extra) + ending


def save(updates, path=CONFIG_PATH, copy_path=TEST_GAME_PATH, history_dir=HISTORY_DIR,
         maps_dir=None, map_meta_path=MAP_META_PATH, match=None, teams_path=TEAMS_PATH):
    """Validate, write spawn.ini, sync the map/spawn copies and keep history."""
    path = pathlib.Path(path)
    maps_dir = pathlib.Path(maps_dir or MAPS_DIR)
    map_meta_path = pathlib.Path(map_meta_path)
    teams_path = pathlib.Path(teams_path)
    normalized = validate_updates(updates)
    current = load(path)
    ai_count = int(normalized.get(('Settings', 'AIPlayers'),
                                  current.get(('Settings', 'AIPlayers'), '1')))
    source_map, info = _map_plan(normalized, maps_dir)
    match_state = validate_match_state(match, ai_count) if match else None
    normalized[('Settings', 'PlayerCount')] = str(ai_count + 1)
    normalized[('Settings', 'Scenario')] = 'spawnmap.ini'
    normalized[('Settings', 'UIMapName')] = info['name']
    if match_state:
        normalized[('Settings', 'Side')] = str(match_state['player_country'])
    # Every computer slot gets the chosen colour/handicap; countries use the
    # CnCNet layout (AI k -> Multi(k+1) with one human player).
    color = normalized.get(('HouseColors', 'Multi1'), current.get(('HouseColors', 'Multi1'), '1'))
    for index in range(1, ai_count + 2):
        normalized[('HouseColors', f'Multi{index}')] = color
        normalized.setdefault(('HouseCountries', f'Multi{index}'),
                              current.get(('HouseCountries', f'Multi{index}'), '0'))
        normalized.setdefault(('HouseHandicaps', f'Multi{index}'),
                              current.get(('HouseHandicaps', f'Multi{index}'), '0'))
    if match_state:
        for index, country in enumerate(match_state['ai_countries']):
            normalized[('HouseCountries', f'Multi{index + 2}')] = str(country)
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    ending = '\r\n' if b'\r\n' in raw else '\n'
    old_revision = hashlib.sha256(raw).hexdigest()
    new_text = set_values(text, normalized, ending=ending)
    if match_state:
        new_text = apply_alliances(new_text, match_state['player_team'],
                                   match_state['ai_teams'], ending=ending)
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / (old_revision + '.ini')).write_bytes(raw)
    temporary = path.with_suffix('.ini.tmp')
    temporary.write_bytes(new_text.encode('utf-8'))
    temporary.replace(path)
    if copy_path is not None:
        copy_path = pathlib.Path(copy_path)
        copy_path.write_bytes(new_text.encode('utf-8'))
        (copy_path.parent / 'spawnmap.ini').write_bytes(source_map.read_bytes())
    map_meta_path.write_text(json.dumps({'file': info['file'], 'name': info['name'],
                                        'players': info['players']}, ensure_ascii=False,
                                       indent=2), encoding='utf-8')
    if match_state:
        teams_path.write_text(json.dumps(
            {'player': match_state['player_team'], 'ai': match_state['ai_teams']},
            ensure_ascii=False, indent=2), encoding='utf-8')
    return file_revision(path), old_revision



def find_meta_hash(data, wanted):
    """Find a hash field (spawn_ini_sha256 / map_sha256) inside episode JSON."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == wanted and isinstance(value, str):
                return value
            found = find_meta_hash(value, wanted)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_meta_hash(value, wanted)
            if found:
                return found
    return None


def find_spawn_sha(data):
    return find_meta_hash(data, 'spawn_ini_sha256')


def expected_map_sha(maps_dir=None):
    info = current_map(maps_dir)
    path = map_path(info['file'], maps_dir)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def active_status():
    """Describe the live training run and whether the episode used the saved files."""
    active_path = ROOT / 'reports/rl/active-run.json'
    saved = file_revision(CONFIG_PATH)
    copy = file_revision(TEST_GAME_PATH) if TEST_GAME_PATH.exists() else None
    info = current_map()
    status = {'saved': saved, 'copy': copy, 'run_dir': None, 'episode': None,
              'episode_spawn': None, 'match': None, 'map': info,
              'map_expected': expected_map_sha(), 'map_used': None,
              'message': '暂未读取到训练进度'}
    try:
        active = json.loads(active_path.read_text(encoding='utf-8-sig'))
        run_dir = pathlib.Path(active['run_dir'])
        status['run_dir'] = str(run_dir)
        episodes = sorted((run_dir / 'episodes').glob('*.ckpt.json'),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        if episodes:
            status['episode'] = episodes[0].name.split('.')[0]
            try:
                data = json.loads(episodes[0].read_text(encoding='utf-8-sig'))
                status['episode_spawn'] = find_spawn_sha(data)
                status['map_used'] = find_meta_hash(data, 'map_sha256')
            except (OSError, ValueError):
                pass
        same_spawn = status['episode_spawn'] == saved
        same_map = status['map_used'] == status['map_expected']
        if status['episode_spawn'] is None:
            status['message'] = '已保存，等待下一局加载'
        elif same_spawn and same_map:
            status['match'] = 'current'
            status['message'] = '当前对局已在用已保存的设置'
        else:
            status['match'] = 'pending'
            status['message'] = '已保存的设置将在下一局生效（当前局用旧设置）'
    except (OSError, ValueError, KeyError):
        pass
    if copy is not None and copy != saved:
        status['message'] += '；test-game 副本与 config 不一致（保存一次即可同步）'
    return status


def display(spec, values):
    value = values.get((spec['section'], spec['keys'][0]))
    if value is None:
        return '—'
    if spec['kind'] == 'bool':
        return '开' if _normalize_bool(value) == 'Yes' else '关'
    if spec['kind'] == 'enum':
        for option, name in spec['options']:
            if str(option) == str(value):
                return f'{option} {name}'
    return str(value)
