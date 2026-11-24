"""One shared MCP service: python scripts/control_service.py (127.0.0.1:14522/mcp)."""
import asyncio
import contextlib
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp.server.fastmcp import FastMCP
from ra2control.controller import Controller
from ra2control.journal import Journal
from ra2control.native import Native

controller = None


@contextlib.asynccontextmanager
async def lifespan(app):
    global controller
    # Cross-process guard: even a differently configured HTTP port cannot start
    # a second native owner. Released automatically by Windows on process exit.
    guard = socket.socket()
    guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    guard.bind(('127.0.0.1', 14523))
    guard.listen(1)
    journal = Journal(ROOT / 'reports' / 'control.sqlite3')
    controller = Controller(Native(), journal)
    worker = asyncio.create_task(controller.run())
    try:
        yield {}
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        journal.db.close()
        guard.close()


mcp = FastMCP('RA2 Shared Controller', host='127.0.0.1', port=14522, json_response=True,
              lifespan=lifespan, instructions='Read status first. Only control explicitly leased player units. Never assume ACK means effect. Renew lease before expiry. Enemy data is withheld until visibility validation. Explicit emergency_stop gives back control; native human-input preemption is not implemented.')


@mcp.tool()
async def status(after_event: int = 0) -> dict:
    """Compact player-only snapshot, task progress, skill outcomes, and new events."""
    return controller.status(after_event)


@mcp.tool()
async def acquire_control(owner: str, unit_ids: list[int], ttl_seconds: int = 30) -> dict:
    """Acquire exclusive control of explicit player units. Returns token and match epoch."""
    return await controller.acquire(owner, unit_ids, ttl_seconds)


@mcp.tool()
async def renew_control(token: str, ttl_seconds: int = 30) -> dict:
    """Renew the existing lease; never silently steal another client's units."""
    return await controller.renew(token, ttl_seconds)


@mcp.tool()
async def release_control(token: str) -> dict:
    """Cancel local tasks, attempt STOP on scoped units, and release control."""
    return await controller.release(token)


@mcp.tool()
async def emergency_stop() -> dict:
    """Explicit manual takeover: revoke all controller tasks and attempt unit STOP."""
    return await controller.emergency_stop()


@mcp.tool()
async def unit_order(token: str, request_id: str, epoch: str, unit_ids: list[int], action: str, coordinates: list[int] | None = None) -> dict:
    """MOVE, STOP, DEPLOY. Coordinates are world leptons, 256 per cell. Reuse request_id only with identical arguments; unknown outcomes are not replayed."""
    return await controller.order(token, request_id, epoch, unit_ids, action, coordinates)


@mcp.tool()
async def run_waypoints(token: str, epoch: str, unit_ids: list[int], points: list[list[int]], duration_seconds: int = 30, retreat_hp_fraction: float = 0.6) -> dict:
    """Locally execute up to 32 waypoints without model roundtrips; observe arrivals, abort stuck/dead, issue retreat when damaged. This is not verified autonomous fog exploration."""
    return await controller.waypoints(token, epoch, unit_ids, points, duration_seconds, retreat_hp_fraction)


if __name__ == '__main__':
    mcp.run(transport='streamable-http')
