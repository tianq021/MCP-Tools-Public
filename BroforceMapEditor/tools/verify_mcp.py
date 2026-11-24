import asyncio
import json
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from map_authoring import new_expedition

async def main():
    server = Path(__file__).resolve().parents[1] / 'mcp/bfmcp_server.py'
    async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(server)])) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            print('tools:', [t.name for t in (await client.list_tools()).tools])
            calls = [('create_map', {'map_id':'jungle_outposts_04', 'project':new_expedition()}),
                               ('validate_map', {'map_id':'jungle_outposts_04'}),
                               ('export_map', {'map_id':'jungle_outposts_04', 'load':True}),
                               ('get_game_state', {})]
            if '--load' in sys.argv:
                calls = [('load_map', {'map_id':'jungle_outposts_04'}), ('get_game_state', {})]
            if '--probe' in sys.argv:
                calls = [('get_runtime_entities', {})]
            for name, args in calls:
                if '--v5' in sys.argv and 'map_id' in args:
                    args['map_id'] = 'jungle_outposts_05'
                result = await client.call_tool(name, args)
                print(name, result.model_dump_json())
                if result.isError:
                    raise RuntimeError(f'{name} failed')

if __name__ == '__main__':
    asyncio.run(main())
