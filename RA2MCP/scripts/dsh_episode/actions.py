"""Pre-order liveness guard.

The native order path rejects stale object addresses with `native_error: object
not found` (addresses change when units move/die between observation and send).
This module filters an intent against a *fresh* state snapshot right before the
command is sent, so the runner does not send doomed orders and the episode file
keeps an explicit record of what was dropped and why.
"""
from collections import Counter

LIVE = "live"


def live_addresses(st):
    """Return {'techno': set, 'all': set} of live addresses in a GameState."""
    techno, all_objs = set(), set()
    for o in st.objects:
        if int(o.health) <= 0:
            continue
        addr = int(o.pointer_self)
        all_objs.add(addr)
        if not bool(getattr(o, "in_limbo", False)):
            techno.add(addr)
    return {"techno": techno, "all": all_objs}


def guard_intent(intent, live):
    """Filter stale addresses from an intent.

    Returns (sanitized_intent, stats) where stats =
      {filtered: [addrs], dropped: [addrs], fatal: bool, reason: str|None}
    Fatal means the intent cannot be executed at all (no live actors / target).
    """
    action = intent.get("action")
    out = dict(intent)
    filtered, dropped = [], []
    fatal, reason = False, None
    addrs = [int(a) for a in intent.get("object_addresses") or []]

    if action == "PLACE":
        b = intent.get("building_address")
        if b is None or int(b) not in live["all"]:
            fatal, reason = True, "stale_building_address"
        return out, {"filtered": [], "dropped": [int(b)] if b else [],
                      "fatal": fatal, "reason": reason}

    if action == "PRODUCE":
        return out, {"filtered": [], "dropped": [], "fatal": False, "reason": None}

    if addrs:
        kept = [a for a in addrs if a in live["techno"]]
        dropped = [a for a in addrs if a not in live["techno"]]
        filtered = dropped
        out["object_addresses"] = kept
        if not kept:
            fatal, reason = True, "no_live_actors"
        elif dropped:
            reason = "partial_stale_actors"

    if action == "ATTACK":
        target = intent.get("target_address")
        if target is None or int(target) not in live["techno"]:
            fatal, reason = True, "stale_target"
    return out, {"filtered": filtered, "dropped": dropped,
                  "fatal": fatal, "reason": reason}


def summarize_guard_stats(stats_list):
    c = Counter()
    for s in stats_list:
        if s.get("fatal"):
            c[s.get("reason") or "fatal"] += 1
        elif s.get("dropped"):
            c["partial_stale_actors"] += 1
    return dict(c)
