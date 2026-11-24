"""Reward-only ground-truth tracker for kills, destroys, damage and repairs.

The tracker consumes the *full* bridge object array (every house, including
objects hidden behind shroud) and is used **exclusively for training rewards**.
It never contributes to the model observation and never writes per-object
hidden information into the episode JSONL.

Contract (see reports/v4-reward-system.md):

* identity = ``(addr, type_name, owner)``.  Native addresses are reused after
  destruction (verified in the 75-episode audit: 50 enemy / 35 own reuse cases),
  so a bare pointer is not an identity.
* a removal is only reported after the identity stayed absent for
  ``confirm_frames`` consecutive captures, which tolerates transient bridge
  parse misses (the C++ parser silently skips objects it fails to parse).
* ``in_limbo`` objects are not tracked (transport/garrison/production limbo).
* ownership/type changes at the same address (MCV -> construction yard, a
  captured building, address reuse) are reported as ``identity_changes`` and
  never pay a kill or an own-loss reward.
* an enemy removal counts as a kill only when the object was observed taking
  damage during its tracked lifetime.  A full-health disappearance is reported
  as ``sold_like_removals`` (sale / scripted despawn / unknown) and pays
  nothing — object disappearance is never blindly treated as a kill.
* the last observed HP of a confirmed kill is reported as ``killed_hp_credit``
  so the killing blow is not lost from the damage reward.
* repairs/heals are not penalised and the healed HP is rewarded again when it
  is damaged again (known limitation documented in the report).
"""
from dsh_episode.hostility import is_opponent

KIND_INFANTRY = "infantry"
KIND_VEHICLE = "vehicle"
KIND_BUILDING = "building"
KIND_OTHER = "other"

_ABSTRACT_KIND = {
    "ABSTRACT_TYPE_INFANTRY": KIND_INFANTRY,
    "ABSTRACT_TYPE_UNIT": KIND_VEHICLE,
    "ABSTRACT_TYPE_AIRCRAFT": KIND_VEHICLE,
    "ABSTRACT_TYPE_BUILDING": KIND_BUILDING,
}

_KILL_KINDS = (KIND_INFANTRY, KIND_VEHICLE, KIND_BUILDING)

_EVENT_KEYS = (
    "enemy_damage_hp", "own_damage_hp", "own_removed_hp",
    "kills_infantry", "kills_vehicle", "building_destroyed", "killed_hp_credit",
    "sold_like_removals", "identity_changes", "reappear_cancels", "mcv_removals",
    "limbo_transitions", "unidentified_removals",
)


def _abstract_name(value):
    try:
        from ra2yrproto import ra2yr
        return ra2yr.AbstractType.Name(value)
    except Exception:  # noqa: BLE001 - proto enum alone in unit tests
        return str(value)


def snapshot_from_state(state, type_names):
    """Convert one ``GetGameState`` reply into plain truth records.

    ``type_names`` maps native type-class pointer -> type name (from the initial
    catalogue).  Records contain no visibility filtering: they are the reward
    truth, not the model observation.
    """
    records = []
    for obj in state.objects:
        try:
            coordinates = obj.coordinates
            cell = [int(coordinates.x) // 256, int(coordinates.y) // 256]
        except Exception:  # noqa: BLE001 - off-map objects have no coordinates
            cell = [0, 0]
        records.append({
            "addr": int(obj.pointer_self),
            "type": type_names.get(int(getattr(obj, "pointer_technotypeclass", 0)), "?"),
            "owner": int(getattr(obj, "pointer_house", 0)),
            "health": int(obj.health),
            "abstract": _abstract_name(getattr(obj, "object_type", 0)),
            "in_limbo": bool(getattr(obj, "in_limbo", False)),
            "on_map": bool(getattr(obj, "on_map", False)),
            "cell": cell,
        })
    return records


def _kind_of(record):
    return _ABSTRACT_KIND.get(record.get("abstract") or "", KIND_OTHER)


def empty_events():
    out = {key: 0.0 for key in _EVENT_KEYS}
    for key in ("kills_infantry", "kills_vehicle", "building_destroyed",
                "sold_like_removals", "identity_changes", "reappear_cancels",
                "mcv_removals", "limbo_transitions", "unidentified_removals"):
        out[key] = 0
    out["enemy_damage_addrs"] = []
    out["kill_events"] = []
    return out


class TruthTracker:
    """Stateful per-episode object tracker used only by the reward path."""

    def __init__(self, max_hp=None, hero_owner=None, confirm_frames=2):
        self.max_hp = {str(k): int(v) for k, v in (max_hp or {}).items() if v}
        self.hero_owner = hero_owner
        self.confirm_frames = max(1, int(confirm_frames))
        self.reset()

    # -- lifecycle -----------------------------------------------------
    def reset(self):
        self.frame = 0
        self.live = {}       # identity -> last record
        self.pending = {}    # identity -> consecutive missing frames
        self.damage = {}     # identity -> damage observed during tracked life
        self.houses = {}
        self.totals = empty_events()

    # -- helpers -------------------------------------------------------
    def _key(self, record):
        return (int(record["addr"]), str(record.get("type") or "?"),
                int(record.get("owner") or 0))

    def _side(self, record):
        owner = int(record.get("owner") or 0)
        if owner == self.hero_owner:
            return "own"
        name = self.houses.get(owner, "?")
        return "enemy" if is_opponent({"owner": name}) else "neutral"

    def _add_totals(self, events):
        for key in _EVENT_KEYS:
            self.totals[key] = self.totals.get(key, 0) + events.get(key, 0)
        self.totals["enemy_damage_addrs"] = (
            self.totals.get("enemy_damage_addrs", []) + events.get("enemy_damage_addrs", []))
        self.totals["kill_events"] = (
            self.totals.get("kill_events", []) + events.get("kill_events", []))

    # -- main entry ----------------------------------------------------
    def observe(self, records, houses=None):
        """Feed one captured frame; returns the incremental events of that frame."""
        self.frame += 1
        if houses is not None:
            self.houses = {int(k): str(v) for k, v in houses.items()}
        out = empty_events()
        current = {}
        limbo_keys = set()
        for record in records:
            if int(record.get("health", 0)) <= 0:
                continue
            if not record.get("on_map") or record.get("in_limbo"):
                # Transport/garrison/production limbo: an object that leaves the
                # live set must never be confirmed as a kill or an own loss.
                limbo_keys.add(self._key(record))
                continue
            current[self._key(record)] = record
        current_by_addr = {rec["addr"]: key for key, rec in current.items()}

        # (1) ownership/type changes at the same address: no kill, no loss.
        for key, record in list(self.live.items()):
            if key in current:
                continue
            replacement = current_by_addr.get(record["addr"])
            if replacement is not None and replacement != key:
                self.live.pop(key, None)
                self.damage.pop(key, None)
                self.pending.pop(key, None)
                out["identity_changes"] += 1

        # (2) damage deltas for identities present in both frames.
        for key, record in current.items():
            previous = self.live.get(key)
            if previous is None:
                continue
            delta = int(previous["health"]) - int(record["health"])
            if delta <= 0:
                continue
            side = self._side(record)
            if side == "enemy":
                out["enemy_damage_hp"] += delta
                out["enemy_damage_addrs"].append((record["addr"], delta))
                self.damage[key] = self.damage.get(key, 0) + delta
            elif side == "own":
                out["own_damage_hp"] += delta

        # (3) track live set for the next frame.
        for key, record in current.items():
            self.live[key] = record
            self.damage.setdefault(key, 0)

        # (4) removals: require several consecutive missing captures.
        for key, record in list(self.live.items()):
            if key in current:
                if key in self.pending:
                    self.pending.pop(key, None)
                    out["reappear_cancels"] += 1
                continue
            if key in limbo_keys:
                # Entered transport/garrison/limbo: not a kill, not an own loss.
                self.live.pop(key, None)
                self.damage.pop(key, None)
                self.pending.pop(key, None)
                out["limbo_transitions"] += 1
                continue
            missing = self.pending.get(key, 0) + 1
            if missing >= self.confirm_frames:
                self._confirm_removal(key, record, out)
                self.live.pop(key, None)
                self.damage.pop(key, None)
                self.pending.pop(key, None)
            else:
                self.pending[key] = missing

        self._add_totals(out)
        return out

    # -- event materialisation ----------------------------------------
    def _confirm_removal(self, key, record, out):
        side = self._side(record)
        kind = _kind_of(record)
        type_name = str(record.get("type") or "?")
        if side == "enemy":
            damage = self.damage.get(key, 0)
            max_hp = self.max_hp.get(type_name, 0)
            damaged = damage > 0 or (max_hp > 0 and int(record["health"]) < max_hp)
            if kind not in _KILL_KINDS:
                out["unidentified_removals"] += 1
                return
            if not damaged:
                # Full-health disappearance: sale / scripted removal / unknown.
                out["sold_like_removals"] += 1
                return
            out["killed_hp_credit"] += float(record["health"])
            if kind == KIND_BUILDING:
                out["building_destroyed"] += 1
            elif kind == KIND_INFANTRY:
                out["kills_infantry"] += 1
            else:
                out["kills_vehicle"] += 1
            out["kill_events"].append({
                "kind": kind, "type": type_name,
                "last_health": int(record["health"]), "frame": self.frame})
        elif side == "own":
            if "Construction Vehicle" in type_name:
                # MCV -> construction yard conversion: never an own-unit loss.
                out["mcv_removals"] += 1
                return
            if kind in _KILL_KINDS:
                out["own_removed_hp"] += float(record["health"])
