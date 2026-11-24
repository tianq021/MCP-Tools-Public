"""Opponent identification for the verified offline, no-alliance skirmish.

The bridge does not expose alliance relations. This is deliberately limited to
the no-alliance test configuration; multiplayer cooperation needs native data.
"""
NON_OPPONENTS = {"neutral", "special", "civilian", "<human player>", "?", ""}


def is_opponent(obj):
    return str(obj.get("owner") or "").casefold() not in NON_OPPONENTS
