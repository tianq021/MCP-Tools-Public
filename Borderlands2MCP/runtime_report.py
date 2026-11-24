"""Read a manual game snapshot; never claim it is current live state."""
import datetime
import json
import math
from pathlib import Path

DEFAULT_SNAPSHOT = Path(r'F:\SteamLibrary\steamapps\common\Borderlands 2\Binaries\Win32\Mods\SessionInspector\diagnostics\latest.json')


def filter_units(data, radius_uu=5000, target=''):
    radius = float(radius_uu)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError('范围请输入大于 0 的有限数值（游戏坐标单位 UU）')
    query = target.strip().casefold()
    source = data.get('nearby_units', {})
    rows = [u for u in source.get('units', [])
            if u['distance_uu'] <= radius and (not query or query in ' '.join(
                str(u.get(k) or '') for k in ('snapshot_unit_id', 'display_name', 'object_path', 'object', 'ai_type_name', 'ai_class', 'balance')).casefold())]
    return rows


def read_snapshot(path=DEFAULT_SNAPSHOT, radius_uu=5000, target=''):
    path = Path(path)
    if not path.is_file():
        return ('还没有游戏内快照。用启动器下次启动后，进入角色地图按 F6。\n'
                'F9 开关经验观察；F6 导出记录。不会额外发放经验。\n'
                '模块还需实机验证；若没有生成文件，请查看游戏 Launch.log。')
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('快照超出 4 MB，未读取')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('read_only') is not True:
        raise ValueError('无法识别快照格式')
    timestamp = datetime.datetime.fromisoformat(data['utc'].replace('Z', '+00:00'))
    age = max(0, int((datetime.datetime.now(datetime.timezone.utc) - timestamp).total_seconds()))
    lines = [f'手动快照：{timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")}（{age} 秒前）',
             '这不是实时连接；移动或推进任务后请重新按 F6。',
             f'地图资源名：{data.get("map_package") or "未知"}',
             f'角色在地图中：{data.get("in_world")}',
             '坐标：' + json.dumps(data.get('position'), ensure_ascii=False),
             '角色原始字段：' + json.dumps(data.get('player_fields'), ensure_ascii=False),
             '玩家血量/护盾：' + json.dumps({k:data.get('character',{}).get(k) for k in ('health','max_health','shield','max_shield')},ensure_ascii=False),
             '', '任务目标（空值=未知；状态码和进度对应关系尚待验证）']
    missions = data.get('missions', {})
    if not missions.get('available'):
        lines.append('任务追踪器暂不可用，可能还在菜单或加载中。')
    all_missions = missions.get('missions', [])
    selected = [m for m in all_missions if m.get('tracked') or m.get('progress_available')]
    selected.sort(key=lambda m: not m.get('tracked'))
    lines.append(f'显示追踪或有角色进度记录的 {len(selected)} 条；完整快照含 {len(all_missions)} 条定义，不等于已接任务数。')
    for mission in selected:
        lines.append(('【正在追踪】' if mission.get('tracked') else '') +
                     str(mission.get('definition')) + ' 状态原值=' + str(mission.get('status_raw')))
        lines.append(json.dumps(mission.get('text_fields'), ensure_ascii=False))
        for objective in mission.get('objectives', []):
            lines.append('  目标：' + str(objective.get('definition')))
            lines.append('  ' + json.dumps(objective.get('observed_fields'), ensure_ascii=False))
        lines.append('进度原值：' + (json.dumps(mission.get('progress_raw'))
                                   if mission.get('progress_available') else '未知'))
        if mission.get('objectives_truncated'):
            lines.append('目标过多，仅展示前 128 项。')
    if missions.get('truncated'):
        lines.append('任务过多，仅展示前 256 项。')
    units = data.get('nearby_units', {})
    filtered = filter_units(data, radius_uu, target)
    lines.extend(['', f'附近单位：范围 {radius_uu} UU（不是米），名称/路径筛选：{target or "全部"}',
                  '敌对关系尚未验证，列表可能包含友方/中立 NPC。以下也只是快照值。'])
    if not units.get('available'):
        lines.append('单位数据不可用：' + str(units.get('reason', '请更新模块后重新按 F6')))
    elif not filtered:
        lines.append('当前快照内无匹配单位，不代表地图上没有该敌人。')
    for unit in filtered:
        lines.append(str(unit.get('display_name') or unit.get('ai_type_name') or unit.get('ai_class')))
        lines.append(f'距离 {unit.get("distance_uu")} UU；等级 {unit.get("level")}；生命 {unit.get("health")}/{unit.get("max_health")}；护盾 {unit.get("shield")}/{unit.get("max_shield")}')
        lines.append('对玩家友好：' + str(unit.get('friendly_to_player', '未知')) + '（非友好不等于敌对）')
        lines.append('对象路径：' + str(unit.get('object_path') or unit.get('object')))
        lines.append('本次快照编号：' + str(unit.get('snapshot_unit_id') or '旧版快照无唯一编号'))
    if units.get('scan_truncated') or units.get('results_truncated'):
        lines.append('单位扫描或结果已截断，不能视为全地图列表。')
    events = data.get('xp_events_retained', [])
    lines.extend(['', f'经验观察开启：{data.get("xp_observation_enabled")}；保留 {len(events)} 条事件',
                  '观察值是函数执行前的参数，尚未证明等于实际到账经验；未发放奖励。',
                  json.dumps(events[-10:], ensure_ascii=False, indent=2), '', '完整记录：' + str(path)])
    return '\n'.join(lines)


if __name__ == '__main__':
    print(read_snapshot())
