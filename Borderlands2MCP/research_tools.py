"""Read-only discovery tools. No game process, save, or mod mutations."""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'research-codex'
ALIASES = {
    '经验': 'experience xp exp', '任务': 'mission objective reward',
    '技能': 'skill', '掉落': 'loot drop', '传送': 'travel teleport',
    '重载': 'reload quickload', '装备': 'inventory gear weapon',
    '背包': 'backpack inventory', '平衡': 'scaling balance difficulty',
    '离线': 'offline shift', '事件': 'event bpd trigger',
}


def search_mods(query='', category='', limit=20):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('limit 必须为 1–100 的整数')
    if not isinstance(query, str) or not isinstance(category, str):
        raise ValueError('query 和 category 必须是文本')
    records = json.loads((DATA / 'sdk_mod_catalog.json').read_text(encoding='utf-8'))['records']
    terms = ALIASES.get(query.strip(), query.strip().lower()).split()
    found = []
    for entry in records:
        if 'error' in entry:
            continue
        if category and category.casefold() not in [c.casefold() for c in entry['categories']]:
            continue
        text = ' '.join([entry['name'], entry['excerpt_en'], *entry['categories']]).casefold()
        if terms and not any(term in text for term in terms):
            continue
        found.append(entry)
    return {'matched': len(found), 'returned': min(limit, len(found)),
            'scope': '上游目录快照，不代表全部模组，也不表示已安装或已实机验证。',
            'items': found[:limit]}


def feature_plan(query=''):
    if not isinstance(query, str):
        raise ValueError('query 必须是文本')
    data = json.loads((DATA / 'runtime_capabilities.json').read_text(encoding='utf-8'))
    needle = query.strip().casefold()
    items = [item for item in data['features'] if not needle or
             needle in json.dumps(item, ensure_ascii=False).casefold()]
    return {'status': '研究与接口计划，不能执行游戏操作', 'items': items}


def register(mcp):
    from character_tools import register as register_character
    register_character(mcp)
    from runtime_client import register as register_runtime
    register_runtime(mcp)
    @mcp.tool()
    def bl2_research_mod_search(query: str = '', category: str = '', limit: int = 20) -> dict:
        """只读搜索212条社区SDK模组元数据，支持经验/任务/掉落/传送等中文关键词；不安装。"""
        return search_mods(query, category, limit)

    @mcp.tool()
    def bl2_research_feature_plan(query: str = '') -> dict:
        """查询物品生成、任务/事件、额外经验等接口证据与未验证项；不是运行时执行工具。"""
        return feature_plan(query)


if __name__ == '__main__':
    from mcp.server.fastmcp import FastMCP
    server = FastMCP('bl2-research')
    register(server)
    server.run()
