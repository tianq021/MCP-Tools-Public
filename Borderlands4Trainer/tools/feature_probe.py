"""Read-only BL4 feature probe for currency, skill-point and experience research.

This tool does NOT write to the game process. It validates the pinned executable,
locates reflected Unreal objects/functions, records function parameter layouts,
and captures a small amount of native code for offline analysis.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from process_reader import Reader, game_pids, modules
from reflection import Reflection


TARGET_NAMES = {
    # Currency path.
    "GbxCurrencyFunctionLibrary",
    "GbxCurrencyManager",
    "GbxCurrencyHolder",
    "GiveCurrency",
    "Server_AddCurrency",
    "Client_ConsumePremiumCurrency",
    "Server_UpdatePremiumCurrency",
    # Skill/progress-point path.
    "GbxProgressGraph",
    "GbxProgressPointsAmount",
    "GbxProgressPointsContainer",
    "Server_SpendProgressPoints",
    # Experience path.
    "OakExperienceStatics",
    "AwardKillExperienceToParty",
    "BP_GetExperienceLevel",
    "BP_SetExperienceLevel",
    "BP_UnlockExperienceType",
    "OnRep_ExperienceState",
}

INTERESTING_PROPERTY_WORDS = (
    "currency",
    "progress",
    "point",
    "skill",
    "experience",
    "xp",
    "backpack",
    "pool",
    "spent",
    "level",
)

CURRENCY_IDS = {
    "cash": bytes.fromhex("5492b7bde81702000000000000000000"),
    "eridium": bytes.fromhex("cb244fcdd79c02000000000000000000"),
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def property_of(r: Reflection, cls: int, name: str, kind: str | None = None):
    matches = [p for p in r.props(cls) if p["name"] == name]
    if kind is not None:
        matches = [p for p in matches if p["kind"] == kind]
    if len(matches) != 1:
        raise ValueError(f"Expected one property {name!r}; found {len(matches)}")
    return matches[0]


def safe_name(r: Reflection, obj: int) -> str:
    try:
        return r.objname(obj)
    except (OSError, ValueError, UnicodeError):
        return "<unreadable>"


def outer_chain(r: Reflection, obj: int, limit: int = 8):
    result = []
    seen = set()
    current = obj
    for _ in range(limit):
        if not current or current in seen:
            break
        seen.add(current)
        try:
            outer = r.u64(current + 32)
        except OSError:
            break
        if not outer:
            break
        result.append({"address": outer, "name": safe_name(r, outer)})
        current = outer
    return result


def read_simple_property(r: Reflection, base: int, prop: dict):
    address = base + prop["offset"]
    kind = prop["kind"]
    try:
        if kind == "IntProperty":
            return {"address": address, "value": struct.unpack("<i", r.p.read(address, 4))[0]}
        if kind == "UInt32Property":
            return {"address": address, "value": struct.unpack("<I", r.p.read(address, 4))[0]}
        if kind == "UInt64Property":
            return {"address": address, "value": struct.unpack("<Q", r.p.read(address, 8))[0]}
        if kind == "FloatProperty":
            return {"address": address, "value": struct.unpack("<f", r.p.read(address, 4))[0]}
        if kind == "BoolProperty":
            return {"address": address, "raw": r.p.read(address, 4).hex()}
        if kind in ("ObjectProperty", "WeakObjectProperty"):
            ptr = r.u64(address)
            return {"address": address, "pointer": ptr, "object_name": safe_name(r, ptr) if ptr else "None"}
        if kind == "StructProperty":
            return {"address": address, "raw32": r.p.read(address, 32).hex()}
    except (OSError, ValueError, UnicodeError) as error:
        return {"address": address, "error": str(error)}
    return {"address": address, "note": "type not decoded"}


def locate_local_player(r: Reflection):
    controllers = []
    for _, obj in r.objects_iter():
        try:
            if r.i32(obj + 8) & 0x10:
                continue
            cls = r.u64(obj + 16)
            if safe_name(r, cls) != "OakPlayerController":
                continue
            player_prop = property_of(r, cls, "player", "ObjectProperty")
            player = r.u64(obj + player_prop["offset"])
            if player and safe_name(r, r.u64(player + 16)) == "OakLocalPlayer":
                controllers.append(obj)
        except (OSError, ValueError, UnicodeError):
            continue
    if len(controllers) != 1:
        raise ValueError(f"Expected one local OakPlayerController; found {len(controllers)}")

    pc = controllers[0]
    cls = r.u64(pc + 16)
    result = {
        "controller": pc,
        "controller_class": safe_name(r, cls),
        "interesting_properties": [],
    }

    props = list(r.props(cls))
    for prop in props:
        if any(word in prop["name"].casefold() for word in INTERESTING_PROPERTY_WORDS):
            item = dict(prop)
            item["runtime"] = read_simple_property(r, pc, prop)
            result["interesting_properties"].append(item)

    manager_prop = property_of(r, cls, "CurrencyManager", "ObjectProperty")
    state_prop = property_of(r, cls, "PlayerState", "ObjectProperty")
    manager = r.u64(pc + manager_prop["offset"])
    state = r.u64(pc + state_prop["offset"])
    result["currency_manager"] = manager
    result["player_state"] = state
    result["currency_manager_class"] = safe_name(r, r.u64(manager + 16)) if manager else "None"
    result["player_state_class"] = safe_name(r, r.u64(state + 16)) if state else "None"

    # CurrencyManager.currencies: capture the exact reflected entries we previously wrote.
    if manager:
        currencies = property_of(r, r.u64(manager + 16), "currencies", "ArrayProperty")
        inner = r.u64(currencies["field"] + 0x78)
        element_size = r.i32(inner + 0x34)
        currency_struct = r.u64(inner + 0x70)
        amount_off = property_of(r, currency_struct, "Amount", "UInt64Property")["offset"]
        type_off = property_of(r, currency_struct, "type", "StructProperty")["offset"]
        data, count, capacity = struct.unpack("<Qii", r.p.read(manager + currencies["offset"], 16))
        entries = []
        if element_size == 24 and 0 < count <= capacity <= 4096:
            for index in range(count):
                row = data + index * element_size
                type_id = r.p.read(row + type_off, 16)
                name = next((n for n, wanted in CURRENCY_IDS.items() if type_id == wanted), None)
                if name:
                    entries.append({
                        "name": name,
                        "row": row,
                        "amount_address": row + amount_off,
                        "amount": r.u64(row + amount_off),
                        "type_id": type_id.hex(),
                    })
        result["currency_array"] = {
            "data": data,
            "count": count,
            "capacity": capacity,
            "element_size": element_size,
            "amount_offset": amount_off,
            "type_offset": type_off,
            "entries": entries,
        }

    # Backpack path, already behavior-tested in the UI.
    if state:
        state_cls = r.u64(state + 16)
        container = property_of(r, state_cls, "BackpackContainer", "StructProperty")
        container_struct = r.u64(container["field"] + 0x70)
        maximum = property_of(r, container_struct, "MaxSize", "StructProperty")
        attribute_struct = r.u64(maximum["field"] + 0x70)
        value_off = property_of(r, attribute_struct, "Value", "IntProperty")["offset"]
        address = state + container["offset"] + maximum["offset"] + value_off
        result["backpack"] = {"address": address, "value": r.i32(address)}

    return result


def historical_currency_candidates(r: Reflection, current_identity: dict):
    path = ROOT / "research" / "cash_confirmed_candidates.local.json"
    if not path.exists():
        return {"available": False}
    previous = load_json(path)
    same = previous.get("process") == current_identity
    out = {"available": True, "same_process_identity": same, "addresses": []}
    if same:
        for address in previous.get("scan", {}).get("addresses", []):
            try:
                out["addresses"].append({"address": address, "u64": r.u64(address)})
            except OSError as error:
                out["addresses"].append({"address": address, "error": str(error)})
    return out


def record_object(r: Reflection, obj: int, index: int, main_base: int, main_size: int):
    cls = r.u64(obj + 16)
    class_name = safe_name(r, cls)
    record = {
        "index": index,
        "address": obj,
        "name": safe_name(r, obj),
        "class_name": class_name,
        "class_address": cls,
        "outer_chain": outer_chain(r, obj),
        "flags": r.i32(obj + 8),
    }
    try:
        descriptor_types = {"Function", "ScriptStruct", "Class"}
        props = list(r.props(obj if class_name in descriptor_types else cls))
        record["properties"] = props
    except (OSError, ValueError, UnicodeError) as error:
        record["properties_error"] = str(error)

    if class_name == "Function":
        try:
            native = r.u64(obj + 0xD8)
            record["native"] = native
            record["native_in_main_module"] = main_base <= native < main_base + main_size
            if record["native_in_main_module"]:
                record["native_rva"] = native - main_base
            # Small bounded capture only; enough for offline disassembly / thunk identification.
            record["native_bytes"] = r.p.read(native, 256).hex()
        except (OSError, ValueError) as error:
            record["native_error"] = str(error)
    return record


def main():
    config = load_json(ROOT / "trainer_config.json")
    expected = config["expected_executable"]
    baseline = load_json(ROOT / "research" / "local_baseline.json")
    if sha256(expected) != baseline["executables"][1]["sha256"]:
        raise ValueError("Executable version changed; refusing to apply old reflection research")

    pids = game_pids()
    if len(pids) != 1:
        raise ValueError(f"Expected one Borderlands4.exe process; found {len(pids)}")

    signatures = load_json(ROOT / "research" / "reflection_signatures.local.json")
    with Reader(pids[0], expected) as reader:
        mains = [m for m in modules(reader.pid) if m["path"].casefold() == expected.casefold()]
        if len(mains) != 1:
            raise ValueError("Main module not uniquely identified")
        main_module = mains[0]
        r = Reflection(reader, main_module["base"], signatures)

        matches = []
        live_progress_graphs = []
        failures = 0
        for index, obj in r.objects_iter():
            try:
                name = safe_name(r, obj)
                cls = r.u64(obj + 16)
                class_name = safe_name(r, cls)
                if name in TARGET_NAMES:
                    matches.append(record_object(r, obj, index, main_module["base"], main_module["size"]))
                if class_name == "GbxProgressGraph" and name != "Default__GbxProgressGraph" and len(live_progress_graphs) < 32:
                    item = {
                        "index": index,
                        "address": obj,
                        "name": name,
                        "class_name": class_name,
                        "properties": [],
                    }
                    for prop in r.props(cls):
                        if any(word in prop["name"].casefold() for word in INTERESTING_PROPERTY_WORDS):
                            p = dict(prop)
                            p["runtime"] = read_simple_property(r, obj, prop)
                            item["properties"].append(p)
                    live_progress_graphs.append(item)
            except (OSError, ValueError, UnicodeError):
                failures += 1

        identity = reader.identity()
        result = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "process": identity,
            "main_module": main_module,
            "matches": matches,
            "local_player": locate_local_player(r),
            "historical_currency_candidates": historical_currency_candidates(r, identity),
            "live_progress_graphs": live_progress_graphs[:32],
            "failed_object_reads": failures,
            "notes": [
                "No target memory was written.",
                "CurrencyManager amounts are known synchronized/cache values, not yet an authoritative write target.",
                "Function native bytes are captured only for offline identification/disassembly.",
            ],
        }
        reader.require_alive()

    output = ROOT / "research" / "feature_probe.local.json"
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output)

    summary = {
        "output": str(output),
        "pid": pids[0],
        "read_only": True,
        "matched_targets": [m["name"] for m in matches],
        "progress_graph_instances": len(result["live_progress_graphs"]),
        "cash": next((e["amount"] for e in result["local_player"].get("currency_array", {}).get("entries", []) if e["name"] == "cash"), None),
        "eridium": next((e["amount"] for e in result["local_player"].get("currency_array", {}).get("entries", []) if e["name"] == "eridium"), None),
        "backpack": result["local_player"].get("backpack", {}).get("value"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
