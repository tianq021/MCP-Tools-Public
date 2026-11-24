"""Correlated, non-retrying local runtime requests."""
import json
import os
import time
import uuid
from pathlib import Path
from character_tools import DIAGNOSTICS


def request(operation='status', arguments=None, expected=None, timeout=4, folder=DIAGNOSTICS):
    inbox = Path(folder) / 'commands'
    inbox.mkdir(parents=True, exist_ok=True)
    # Older observers slice before filtering. Archive terminal/expired inputs
    # to unblock them; preserve all receipts and never reissue a mutation.
    archive = inbox / 'archive'
    for old in inbox.glob('*.json'):
        receipt = old.with_suffix('.result')
        claimed = old.with_suffix('.claimed')
        terminal = receipt.is_file()
        try:
            data = json.loads(old.read_text(encoding='utf-8'))
            expired = time.time() - data.get('time', time.time()) > 30
        except (OSError, ValueError, TypeError):
            continue
        if terminal or (expired and not claimed.exists()):
            archive.mkdir(exist_ok=True)
            try:
                old.rename(archive / old.name)
            except OSError:
                pass
    token = uuid.uuid4().hex
    payload = {'id': token, 'time': time.time(), 'operation': operation,
               'arguments': arguments or {}, 'expected': expected}
    temporary = inbox / (token + '.tmp')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    os.replace(temporary, inbox / (token + '.json'))
    deadline = time.monotonic() + timeout
    response = inbox / (token + '.result')
    while time.monotonic() < deadline:
        if response.is_file():
            result = json.loads(response.read_text(encoding='utf-8'))
            if result.get('id') != token:
                raise ValueError('响应编号不匹配')
            return result
        time.sleep(.1)
    return {'id': token, 'status': 'unavailable' if operation == 'status' else 'unknown',
            'error': ('角色查询未收到响应：请确认已进入地图、游戏未暂停，且Mod查询已开启。0.5.0存在Hook注册问题，请更新至0.5.1。'
                      if operation == 'status' else '未收到响应，操作结果未知；不要重复提交。请查看对应 result 文件。'),
            'result_file': str(response)}


def item_definition(code, level):
    from bl2toolkit.parts import decode_resolved
    if type(level) is not int or not 1 <= level <= 80:
        raise ValueError('装备等级必须为1至80')
    info = decode_resolved(code)
    resolved = info['resolved']
    core = [(resolved[k] or {}).get('path') for k in ('type', 'balance', 'manufacturer')]
    if not all(core):
        raise ValueError('装备核心资产无法解析')
    parts = resolved['parts']
    if len(parts) != 11:
        raise ValueError('只支持标准11部件装备，暂不生成扩展黑枪')
    return {'weapon': bool(info['is_weapon']),
            'definition': core + [level] + [p['path'] for p in parts] + [level, -1]}


def register(mcp):
    @mcp.tool()
    def bl2_runtime_status() -> dict:
        """查询游戏内技能与会话身份；需要 SessionInspector 0.5。"""
        return request()

    @mcp.tool()
    def bl2_runtime_action(operation: str, arguments: dict, expected: dict) -> dict:
        """明确授权后执行技能/曲线操作。expected 必须来自刚查询的 identity。超时不得重试。"""
        if operation not in ('skill_points', 'skill_set', 'scaler', 'scaler_restore'):
            raise ValueError('不支持的操作')
        return request(operation, arguments, expected)

    @mcp.tool()
    def bl2_runtime_give_item(code: str, level: int, expected: dict, dry_run: bool = True) -> dict:
        """从BL2代码生成一件标准装备到背包；默认仅预览。游戏内需开启运行时修改。"""
        args = item_definition(code, level)
        return {'preview': args} if dry_run else request('give_item', args, expected)
