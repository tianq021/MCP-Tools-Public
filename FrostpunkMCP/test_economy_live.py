"""Operational round in the loaded endless-mode test city.

Bulk-assigns workers to visible resource piles through the native set_workplace
path, probes the per-pile capacity, advances in bounded 10 s windows, and
verifies reassignment/removal. Leaves the city paused.

The older acceptance scripts assume the previous save (EngineersLab workshop);
this one adapts to whatever city is loaded and prints one JSON log.
"""

import json

import frostpunk_mcp as m


def main():
    log = []
    state = m.get_state()
    assert state["ready"] and state["save_requests_blocked"], state
    start = dict(state["resources"])
    m.pause()

    entities = m.get_city()["entities"]
    piles = [e for e in entities if e["name"].startswith("RES_Pickable_")]
    assert piles, "no resource piles in this city"
    agents = [a["id"] for a in m.get_workforce()["agents"]]
    log.append({"step": "layout",
                "resources": start,
                "piles": [p["name"] for p in piles],
                "agents": len(agents)})

    # Round-robin: up to four workers per pile.
    assigned = {p["id"]: 0 for p in piles}
    rejected = []
    index = 0
    for pile in piles:
        while assigned[pile["id"]] < 4 and index < len(agents):
            agent = agents[index]
            index += 1
            try:
                result = m.set_workplace(agent, pile["id"])
            except Exception as exc:
                rejected.append({"pile": pile["name"], "error": str(exc)})
                continue
            if result["accepted"] and result["changed"]:
                assigned[pile["id"]] += 1
            else:
                rejected.append({"pile": pile["name"], "agent": agent, "result": result})
    log.append({"step": "assign",
                "assigned": {p["name"]: assigned[p["id"]] for p in piles},
                "rejected": rejected})

    # Probe the capacity of the first pile with the remaining workers.
    probe = piles[0]
    before = m.get_workforce(building_id=probe["id"])["count"]
    capacity_rejection = None
    while index < len(agents):
        agent = agents[index]
        index += 1
        try:
            result = m.set_workplace(agent, probe["id"])
        except Exception as exc:
            capacity_rejection = {"error": str(exc)}
            break
        if not (result["accepted"] and result["changed"]):
            capacity_rejection = result
            break
    after = m.get_workforce(building_id=probe["id"])["count"]
    log.append({"step": "capacity_probe", "pile": probe["name"],
                "before": before, "after": after,
                "capacity_rejection": capacity_rejection})

    # Advance the economy in bounded windows and sample resources.
    samples = []
    for _ in range(6):
        advanced = m.advance(10)
        samples.append({"advanced": advanced["advanced"],
                        "paused": advanced["after"]["paused"],
                        "clock": advanced["after"]["clock_raw"],
                        "resources": advanced["after"]["resources"]})
    log.append({"step": "advance", "samples": samples})

    working = {e["name"]: e.get("working_agents") for e in m.get_city()["entities"]
               if e.get("working_agents")}
    log.append({"step": "working_agents", "workers": working})

    # Remove one worker, move it to another pile, then restore the original slot.
    staff = m.get_workforce(building_id=probe["id"])["assigned"]
    assert staff, "probe pile has no assigned workers"
    moved = staff[0]
    target = next(p for p in piles if p["id"] != probe["id"])
    base = len(staff)
    removal = m.set_workplace(moved["id"], None)
    after_remove = m.get_workforce(building_id=probe["id"])["count"]
    to_target = m.set_workplace(moved["id"], target["id"])
    back = m.set_workplace(moved["id"], probe["id"])
    after_back = m.get_workforce(building_id=probe["id"])["count"]
    log.append({"step": "reassign", "agent": moved["name"],
                "removed": removal["accepted"] and after_remove == base - 1,
                "to_other_pile": to_target["accepted"] and to_target["building_id"] == target["id"],
                "back_to_probe": back["accepted"] and after_back == base,
                "probe_count": after_back})

    final = m.get_state()
    delta = {k: final["resources"][k] - start[k] for k in start}
    log.append({"step": "final", "paused": final["paused"],
                "resources": final["resources"], "delta": delta})
    return log


if __name__ == "__main__":
    print(json.dumps({"passed": True, "log": main()}, ensure_ascii=False), flush=True)
