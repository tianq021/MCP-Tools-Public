"""Takeover session: staff every resource pile toward its observed capacity, then advance.

Adapts to the loaded endless-mode save-blocked test city, prints a JSON log, and
leaves the city paused. The per-pile worker cap (15) is what test_economy_live.py
measured by native assignment rejection in this build.
"""

import json

import frostpunk_mcp as m

CAPACITY = 15


def main():
    state = m.get_state()
    assert state["ready"] and state["save_requests_blocked"], state
    start = state["resources"]
    m.pause()

    entities = m.get_city()["entities"]
    piles = [e for e in entities if e["name"].startswith("RES_Pickable_")]
    agents = [a["id"] for a in m.get_workforce()["agents"]]
    staff = {p["id"]: {r["id"] for r in m.get_workforce(building_id=p["id"])["assigned"]}
             for p in piles}
    assigned = set().union(*staff.values())
    idle = [a for a in agents if a not in assigned]
    log = [{"step": "layout", "resources": start, "agents": len(agents),
            "idle": len(idle),
            "staff": {p["name"]: len(staff[p["id"]]) for p in piles}}]

    filled, full = [], []
    for pile in piles:
        while len(staff[pile["id"]]) < CAPACITY and idle:
            agent = idle.pop(0)
            result = m.set_workplace(agent, pile["id"])
            if result["accepted"] and result["changed"]:
                staff[pile["id"]].add(agent)
                filled.append({"agent": agent, "pile": pile["name"]})
            elif not result["accepted"]:
                full.append({"pile": pile["name"], "count": len(staff[pile["id"]])})
                break
    log.append({"step": "fill", "filled": len(filled), "full": full,
                "staff": {p["name"]: len(staff[p["id"]]) for p in piles},
                "idle_left": len(idle)})

    samples = []
    for _ in range(3):
        advanced = m.advance(10)
        samples.append({"advanced": advanced["advanced"],
                        "paused": advanced["after"]["paused"],
                        "resources": advanced["after"]["resources"]})
    log.append({"step": "advance", "samples": samples})

    working = {e["name"]: e.get("working_agents") for e in m.get_city()["entities"]
               if e.get("working_agents")}
    log.append({"step": "working_agents", "workers": working,
                "total_working": sum(working.values())})

    final = m.get_state()
    log.append({"step": "final", "paused": final["paused"],
                "resources": final["resources"],
                "delta": {k: final["resources"][k] - start[k] for k in start}})
    return log


if __name__ == "__main__":
    print(json.dumps({"passed": True, "log": main()}, ensure_ascii=False), flush=True)
