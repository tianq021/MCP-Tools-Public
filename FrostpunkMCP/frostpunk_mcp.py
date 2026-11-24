"""Frostpunk 1 engine control via MCP stdio; no desktop or image dependencies."""

from contextlib import contextmanager
import csv
import msvcrt
from pathlib import Path
import subprocess
import threading
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from takeover import Controller
from fp1 import BridgeError
from research_state import ResearchReadError
from city_state import city_state
import ability_control
import building_control
import workforce_control
import quest_state


mcp = FastMCP("frostpunk", instructions=(
    "Control the existing Frostpunk 1 save-blocked test city through native engine calls. "
    "Start with get_state. Read get_tasks for current quests, localized objectives and live progress; "
    "do not guess scenario/mode or substitute the definition catalog for current tasks. "
    "No screenshots or keyboard input are used. "
    "advance runs for at most 10 wall-clock seconds and pauses in finally. "
    "A false advanced flag means the simulation did not move. Research uses the game's eligibility checks. "
    "get_city returns stable building IDs; get_abilities exposes generator overdrive and other toggles. "
    "set_building_enabled and set_workplace use native rules and verify the result. "
    "Use get_workforce to get agent IDs; null building_id removes a work assignment. "
    "All mutations pause the game except bounded advance. Main-menu control is deferred. "
    "This version refuses ordinary save-enabled game processes."))
serial = threading.Lock()


def game_pid():
    output = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq Frostpunk.exe", "/FO", "CSV", "/NH"],
        text=True, errors="replace", timeout=5)
    pids = [int(row[1]) for row in csv.reader(output.splitlines())
            if row and row[0].lower() == "frostpunk.exe"]
    if len(pids) != 1:
        raise RuntimeError("expected exactly one running Frostpunk process")
    return pids[0]


@contextmanager
def connection():
    # Serialize whole operations across MCP sessions, including the advance/pause pair.
    with serial:
        with Path(__file__).with_name(".fp1-control.lock").open("a+b") as lock:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            controller = None
            try:
                pid = game_pid()
                controller = Controller(pid)
                yield pid, controller
            finally:
                if controller:
                    controller.close()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_state(include_catalog: bool = False) -> dict[str, Any]:
    """Read inventory, pause, research, workshop count and raw simulation clock; validate build and test guard."""
    try:
        with connection() as (pid, controller):
            state = controller.state(include_catalog)
            return {"ready": state["city_research_initialized"], "pid": pid, **state}
    except (OSError, RuntimeError, BridgeError, ResearchReadError) as exc:
        return {"ready": False, "error": str(exc)}


@mcp.tool()
def pause() -> dict[str, Any]:
    """Pause through the engine and verify that the simulation clock stops."""
    with connection() as (pid, controller):
        return {"pid": pid, **controller.pause()}


@mcp.tool()
def advance(seconds: float = 1.0) -> dict[str, Any]:
    """Run for 0 < seconds <= 10 wall-clock seconds, then pause; return both observed states."""
    with connection() as (pid, controller):
        return {"pid": pid, **controller.advance(seconds)}


@mcp.tool()
def start_research(technology: str) -> dict[str, Any]:
    """Request a catalog technology using normal engine eligibility and cost handling; stay paused."""
    with connection() as (pid, controller):
        return {"pid": pid, **controller.research(technology)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_city() -> dict[str, Any]:
    """Read stable building IDs, activity, enabled state, manpower and raw generator steam setting."""
    with connection() as (pid, controller):
        controller.require_city()
        return {'pid':pid,'entities':city_state(controller.reader)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_abilities(entity_id: str) -> dict[str, Any]:
    """Read normal engine visibility, unlock status and current toggles for a city entity."""
    with connection() as (_, controller):
        controller.require_city()
        return ability_control.abilities(controller,entity_id)


@mcp.tool()
def set_ability_enabled(entity_id: str, ability: str, enabled: bool) -> dict[str, Any]:
    """Set an available toggle such as Generator Overdrive using native eligibility and costs; pause first."""
    with connection() as (_, controller):
        return ability_control.set_enabled(controller,entity_id,ability,enabled)


@mcp.tool()
def set_building_enabled(entity_id: str, enabled: bool) -> dict[str, Any]:
    """Pause and set a building's activity switch, then verify the observed state."""
    with connection() as (_, controller):
        return building_control.set_enabled(controller,entity_id,enabled)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_workforce(building_id: str | None = None) -> dict[str, Any]:
    """Read assigned workers/automatons for one building, or all work-role agent IDs. Prefer a paused city."""
    with connection() as (_, controller):
        controller.require_city()
        if not controller.state()['paused']:
            raise RuntimeError('pause before reading workforce to get a consistent assignment snapshot')
        if building_id:return workforce_control.assigned(controller,building_id)
        return {'agents':[{'id':i,'name':n} for p,i,n in workforce_control.agent_rows(controller)]}


@mcp.tool()
def set_workplace(agent_id: str, building_id: str | None = None) -> dict[str, Any]:
    """Pause and assign an agent to a workplace, or remove assignment with null; engine checks apply."""
    with connection() as (_, controller):
        return workforce_control.set_workplace(controller,agent_id,building_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_tasks(include_completed: bool = False, include_hidden: bool = False) -> dict[str, Any]:
    """Read current native quests, localized texts, objective progress/activation and deadlines.

    Defaults to unfinished displayable tasks. Includes inactive future objectives explicitly
    marked inactive. include_hidden reveals internal quests; it is not the definition catalog.
    Times are decimal engine-tick strings, null when no deadline. Does not change pause or quests.
    """
    try:
        with connection() as (pid, controller):
            return {'pid': pid, **quest_state.get_tasks(controller, include_completed, include_hidden)}
    except (OSError, RuntimeError, BridgeError, ResearchReadError) as exc:
        return {'ready': False, 'error': str(exc)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
