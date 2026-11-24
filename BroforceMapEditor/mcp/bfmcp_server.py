"""Broforce map MCP, official Python SDK 1.x, stdio transport."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from mcp.server.fastmcp import FastMCP
import map_authoring as author

mcp = FastMCP('Broforce Maps')

@mcp.tool()
def get_catalog() -> dict:
    """Return verified game enums for map authors."""
    return author.CATALOG

@mcp.tool()
def create_map(map_id: str, project: dict) -> dict:
    """Save a new validated map JSON. Tile strings top-down; doodad x/y bottom-up.
    Grounded entities must sit exactly one grid row above solid terrain.
    Project requires width, height, name, description, tiles, legend, doodads.
    Existing map ids are never overwritten.
    """
    return author.save_project(map_id, project)

@mcp.tool()
def validate_map(map_id: str) -> dict:
    """Check dimensions, enums, bounds and grounded entity support."""
    return author.validate(author.read_project(map_id))

@mcp.tool()
def export_map(map_id: str, load: bool = False) -> dict:
    """Build map through the running game, save BFC and optionally load it.
    Does not overwrite files or publish to Steam. Requires responsive game bridge.
    """
    return author.export_map(map_id, load)

@mcp.tool()
def get_game_state() -> dict:
    """Read current game state using the bridge."""
    with author.LOCK:
        author.bridge.ensure_ready()
        return author.command({'op':'state'})

@mcp.tool()
def load_map(map_id: str) -> dict:
    """Read a previously exported map into memory and load it without writing files."""
    with author.LOCK:
        p = author.read_project(map_id)
        author.bridge.ensure_ready()
        author.command({'op':'inspect_level', 'path':str(author.MAPS / (map_id + '.bfc')),
                        'name':p['name'], 'description':p['description']})
        return author.command({'op':'load'})

@mcp.tool()
def get_runtime_entities() -> dict:
    """Read live enemy positions and health after loading for spawn verification."""
    with author.LOCK:
        return author.command({'op':'runtime_entities'})

@mcp.tool()
def list_maps() -> list[str]:
    """List editable local map project ids."""
    return [p.stem for p in author.MAPS.glob('*.json') if not p.name.endswith('.receipt.json')]

if __name__ == '__main__':
    mcp.run(transport='stdio')
