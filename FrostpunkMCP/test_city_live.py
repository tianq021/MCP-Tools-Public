"""Adaptive live test of the ten Frostpunk MCP tools against the currently loaded city.

The older acceptance scripts target the previous save (EngineersLab workshop,
Generator_000). This script adapts to whatever save-blocked city is loaded,
reverses every mutation it makes, and prints one JSON object per step.

Requirements: exactly one save-blocked Frostpunk process, a loaded city, the
game window focused enough for advance() to move the simulation clock.
"""

import json
import traceback

import frostpunk_mcp as m


steps = []


def record(name, **data):
    steps.append({"step": name, **data})


def expect_error(name, fn):
    try:
        fn()
    except Exception as exc:  # native rejections surface as RuntimeError/BridgeError/ValueError
        record(name, rejected=True, error=f"{type(exc).__name__}: {exc}")
        return
    raise AssertionError(f"{name} was not rejected")


def main():
    initial = m.get_state(include_catalog=True)
    assert initial["ready"] and initial["save_requests_blocked"], initial
    assert initial["catalog_count"] == len(initial["catalog"])
    assert {"Coal", "Wood", "Steel", "Raw Food"} <= set(initial["resources"])
    assert all(type(value) is int for value in initial["resources"].values())
    record("get_state", ready=initial["ready"], catalog=initial["catalog_count"],
           resources=initial["resources"], clock=initial["clock_raw"],
           paused=initial["paused"], workshops=initial["registered_research_buildings"])

    paused = m.pause()
    assert paused["paused"], paused
    record("pause", paused=paused["paused"], clock=paused["clock_raw"])

    entities = m.get_city()["entities"]
    assert entities, "no city entities"
    record("get_city", entities=len(entities),
           buildings=[e["name"] for e in entities if e.get("building")],
           toggleable=[e["name"] for e in entities if e.get("can_toggle")])

    generator = next(e for e in entities if e.get("generator"))
    abilities = m.get_abilities(generator["id"])
    toggle = next((a for a in abilities["abilities"]
                   if a["kind"] == "toggle" and a["visible"] and a["unlocked"]), None)
    if toggle is None:
        record("set_ability_enabled", skipped=True, reason="no visible toggle on generator")
    else:
        attempt = m.set_ability_enabled(generator["id"], toggle["name"], not toggle["enabled"])
        if attempt["accepted"]:
            restore = m.set_ability_enabled(generator["id"], toggle["name"], toggle["enabled"])
            assert restore["accepted"] and restore["enabled"] == toggle["enabled"], restore
            record("set_ability_enabled", ability=toggle["name"], toggled=True,
                   restored=restore["enabled"])
        else:
            now = next(a for a in m.get_abilities(generator["id"])["abilities"]
                       if a["name"] == toggle["name"])
            assert now["enabled"] == toggle["enabled"], now
            record("set_ability_enabled", ability=toggle["name"], toggled=False,
                   engine_rejected=True, state_unchanged=True)

    building = next(e for e in entities if e.get("can_toggle"))
    off = m.set_building_enabled(building["id"], not building["enabled"])
    assert off["changed"] and off["enabled"] == (not building["enabled"]), off
    on = m.set_building_enabled(building["id"], building["enabled"])
    assert on["enabled"] == building["enabled"], on
    record("set_building_enabled", building=building["name"], restored=on["enabled"])

    agents = m.get_workforce()["agents"]
    assert agents, "no work-role agents"
    expect_error("set_workplace invalid agent",
                 lambda: m.set_workplace("invalid", building["id"]))
    baseline = m.get_workforce(building_id=building["id"])
    agent = agents[0]
    try:
        assigned = m.set_workplace(agent["id"], building["id"])
    except Exception as exc:
        record("set_workplace", skipped=True, agent=agent["name"],
               reason=f"{type(exc).__name__}: {exc}", workplace=building["name"])
    else:
        if assigned.get("accepted") and assigned.get("changed"):
            removal = m.set_workplace(agent["id"], None)
            final_staff = m.get_workforce(building_id=building["id"])
            assert final_staff == baseline, (final_staff, baseline)
            record("set_workplace", agent=agent["name"], workplace=building["name"],
                   assign=assigned["changed"], remove=bool(removal.get("accepted")),
                   restored=True)
        else:
            record("set_workplace", skipped=True, agent=agent["name"],
                   reason=assigned, workplace=building["name"])

    before_research = m.get_state()
    if before_research["registered_research_buildings"] == 0 and before_research["active_technology"] is None:
        technology = next((n for n in initial["catalog"] if "Beacon" in n), initial["catalog"][0])
        refused = m.start_research(technology)
        assert refused["accepted"] is False, refused
        assert refused["state"]["active_technology"] is None
        assert refused["state"]["resources"] == before_research["resources"]
        record("start_research", technology=technology, accepted=False,
               reason=refused.get("reason"))

    mutant = m.get_state()
    assert mutant["resources"] == paused["resources"], (mutant["resources"], paused["resources"])
    assert mutant["paused"]

    advanced = m.advance(0.25)
    assert advanced["after"]["paused"], advanced
    record("advance", advanced=advanced["advanced"],
           clock=advanced["after"]["clock_raw"])

    invalid_before = m.get_state()
    expect_error("advance 11 s", lambda: m.advance(11))
    assert m.get_state()["clock_raw"] == invalid_before["clock_raw"]

    final = m.get_state()
    assert final["paused"], final
    record("final", paused=final["paused"], clock=final["clock_raw"],
           resources=final["resources"], resources_unchanged=final["resources"] == initial["resources"])
    return steps


if __name__ == "__main__":
    try:
        steps = main()
        print(json.dumps({"passed": True, "steps": steps}, ensure_ascii=False), flush=True)
    except Exception:
        traceback.print_exc()
        print(json.dumps({"passed": False, "steps": steps}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
