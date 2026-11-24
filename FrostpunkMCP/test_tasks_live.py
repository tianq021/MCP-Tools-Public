"""Read-only live MCP and independent native getter comparison for current quests."""
import asyncio
import json
from pathlib import Path
import struct
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from frostpunk_mcp import connection
from quest_state import get_tasks, QUEST_SYSTEM_RVA


def compare_native():
    with connection() as (pid, c):
        before = c.state()
        assert before['paused'], 'run this verification with the city already paused'
        snapshot = get_tasks(c)
        full = get_tasks(c, include_completed=True, include_hidden=True)
        assert full['count'] == full['runtime_quest_count']
        assert snapshot['tasks'] == [q for q in full['tasks'] if q['displayable'] and q['status']=='in_progress']
        owner = c.reader.pointer(c.reader.base+QUEST_SYSTEM_RVA)
        for quest in full['tasks']:
            native = c.caller.invoke(0x1572E30, (owner, quest['id'])) & 255
            assert bool(native) == (quest['status'] == 'in_progress')
            for objective in quest['objectives']:
                output = c.caller.invoke(0x156F080, (owner, None, quest['id'], objective['index']), payload=b'\0'*8)
                current, target = struct.unpack('<ii', c.reader.read(output, 8))
                assert (current, target) == (objective['progress'], objective['target'])
                active = c.caller.invoke(0x1572F80, (owner, quest['id'], objective['index'])) & 255
                assert bool(active) == objective['active']
        after = c.state()
        assert before == after, 'task reads changed the paused game state'
        return snapshot


async def main():
    expected = compare_native()
    params = StdioServerParameters(command=sys.executable,
        args=['-B', str(Path(__file__).with_name('frostpunk_mcp.py'))])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert 'get_tasks' in {x.name for x in tools}
            tool = next(x for x in tools if x.name == 'get_tasks')
            assert tool.annotations.readOnlyHint
            response = await session.call_tool('get_tasks', {})
            assert not response.isError
            result = response.structuredContent
            assert result['ready'] and result['tasks'] == expected['tasks']
            for q in result['tasks']:
                assert q['localized'] and q['text']
                assert all(o['localized'] and o['text'] for o in q['objectives'])
            print(json.dumps({'passed': True, 'native_comparison': True,
                'state_unchanged': True, 'tools': len(tools), 'result': result}, ensure_ascii=True))


if __name__ == '__main__':
    asyncio.run(main())
