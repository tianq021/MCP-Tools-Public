"""Conservative rewards from legal observations; no credit for neutral damage."""
import math
from dsh_episode.hostility import is_opponent

ACTION_NAMES = ("STOP", "ATTACK_VISIBLE", "SCOUT_START", "RETREAT")
REWARD_VERSION = "hostile_hp_scout_v2"


def hp_losses(before, after, opponents=False):
    key = "visible_enemies" if opponents else "own_objects"
    old = {o["addr"]: o for o in before.get(key, []) if not opponents or is_opponent(o)}
    new = {o["addr"]: o for o in after.get(key, []) if not opponents or is_opponent(o)}
    loss = 0
    for addr, current in new.items():
        previous = old.get(addr)
        if previous and (previous.get("type_name"), previous.get("owner")) == (current.get("type_name"), current.get("owner")):
            loss += max(0, previous["health"] - current["health"])
    # Enemy disappearance may be fog/cloaking, so it never earns kill credit.
    # Own objects are recorded even in limbo; disappearance after warmup is loss.
    if not opponents:
        loss += sum(o["health"] for addr, o in old.items() if addr not in new)
    return float(loss)


def reward_parts(before, after, kind="running", invalid=0):
    dealt = hp_losses(before, after, opponents=True)
    lost = hp_losses(before, after)
    return {"opponent_hp": dealt / 100.0, "own_hp": -lost / 100.0,
            "invalid": -0.1 * invalid,
            "terminal": 10.0 if kind == "win" else -10.0 if kind == "defeat" else 0.0,
            "time": -0.002 * max(0.0, after.get("t", 0) - before.get("t", 0))}


def military_intent(action, observation, public_starts):
    fighters = [o for o in observation.get("own_objects", [])
                if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY" and o.get("on_map")
                and not o.get("in_limbo")]
    fighters = fighters[:8]
    if not fighters:
        return None
    addrs = [o["addr"] for o in fighters]
    base = next((o["cell"] for o in observation.get("own_objects", [])
                 if o.get("type_name") == "Allied Construction Yard" and o.get("on_map")), None)
    if action == 0:
        return {"action": "STOP", "object_addresses": addrs}
    if action == 1:
        enemies = [e for e in observation.get("visible_enemies", [])
                   if is_opponent(e) and e.get("cell_shrouded") is False
                   and e.get("cloak_state", 0) not in (1, 2)
                   and e.get("abstract") != "ABSTRACT_TYPE_AIRCRAFT"]
        if not enemies:
            return None
        def distance(e):
            return min(math.dist(e["cell"], o["cell"]) for o in fighters)
        target = min(enemies, key=distance)
        return {"action": "ATTACK", "object_addresses": addrs,
                "target_address": target["addr"], "target_type": target.get("type_name"),
                "coordinates": [int(c * 256 + 128) for c in target["cell"]]}
    if action == 2 and base:
        starts = [s for s in public_starts if math.dist(s, base) > 12]
        if not starts:
            return None
        goal = min(starts, key=lambda s: min(math.dist(s, o["cell"]) for o in fighters))
    elif action == 3 and base:
        goal = base
    else:
        return None
    return {"action": "MOVE", "object_addresses": addrs,
            "coordinates": [int(c * 256 + 128) for c in goal]}


def scouting_potential(observation, public_starts):
    fighters = [o for o in observation.get("own_objects", [])
                if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY" and o.get("on_map")]
    base = next((o["cell"] for o in observation.get("own_objects", [])
                 if o.get("type_name") == "Allied Construction Yard"), None)
    goals = [s for s in public_starts if base and math.dist(s, base) > 12]
    if not fighters or not goals:
        return 0.0
    center = [sum(o["cell"][i] for o in fighters) / len(fighters) for i in (0, 1)]
    return -min(2.0, min(math.dist(center, s) for s in goals) / 100.0)


def progress_reward(before, after, public_starts, kind="running", gamma=0.99):
    # Potential-based shaping, using public map starts only. Truncation keeps
    # the next potential; true terminal states have zero potential.
    old = scouting_potential(before, public_starts)
    new = 0.0 if kind in ("win", "defeat", "game_end") else scouting_potential(after, public_starts)
    return 0.1 * (gamma * new - old)
