import asyncio
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    root=Path(__file__).resolve().parents[1]
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(root/'mcp/bfmcp_server.py')])) as (r,w):
        async with ClientSession(r,w) as client:
            await client.initialize()
            result=await client.call_tool('export_map',dict(map_id='断崖营救',load=True))
            print(result.model_dump_json())
            if result.isError: raise RuntimeError('Export failed')
asyncio.run(main())
