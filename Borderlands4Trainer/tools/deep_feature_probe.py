"""Second-stage read-only BL4 probe.

Consumes the same reflected runtime layouts as feature_probe.py, then follows the
native dispatch one layer deeper: selected virtual-function slots, known rel32
calls from reflected thunks, player-state progress/experience fields, and a
small set of experience/progress assets. Nothing is written to the game.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from process_reader import Reader, game_pids, modules
from reflection import Reflection
from feature_probe import (
    load_json,
    sha256,
    property_of,
    safe_name,
    outer_chain,
    read_simple_property,
    locate_local_player,
)

CAPTURE_BYTES = 1024

ASSET_NAMES = {
    "Att_ContractRewards_Experience_Multiplier",
    "Att_EnemyTagExperienceMultiplier",
    "DiscoveredExperienceRewardMultiplier",
    "ExperiencePoints",
    "ExperiencePointsRequiredForNextLevel",
    "ExperiencePointsRequiredForPreviousLevel",
    "CharacterProgressPoints",
    "ProgressPoints",
    "ProgressPointsContainer",
    "ProgressPointsSpent",
    "TotalProgressPoints",
    "GbxRewardData_ProgressPoints",
    "GbxProgressPointsContainer",
    "GbxProgressPointsAmount",
    "PlayerExperienceState",
}

FUNCTION_NAMES = {
    "Server_AddCurrency",
    "GiveCurrency",
    "Server_SpendProgressPoints",
    "AwardKillExperienceToParty",
    "BP_GetExperienceLevel",
    "BP_SetExperienceLevel",
    "BP_UnlockExperienceType",
    "OnRep_ExperienceState",
}

PLAYER_FIELD_WORDS = (
    "experience",
    "progress",
    "point",
    "skill",
    "level",
)


def in_main(address: int, main: dict) -> bool:
    return main["base"] <= address < main["base"] + main["size"]


def capture(reader: Reader, address: int, main: dict, size: int = CAPTURE_BYTES):
    row = {
        "address": address,
        "in_main_module": in_main(address, main),
        "capture_size": size,
    }
    if row["in_main_module"]:
        row["rva"] = address - main["base"]
    try:
        row["bytes"] = reader.read(address, size).hex()
    except OSError as error:
        row["error"] = str(error)
    return row


def rel32_call(reader: Reader, instruction: int, main: dict):
    raw = reader.read(instruction, 5)
    if raw[0] != 0xE8:
        return {
            "instruction": instruction,
            "error": f"expected E8 rel32 call, got {raw.hex()}",
        }
    disp = struct.unpack_from("<i", raw, 1)[0]
    target = instruction + 5 + disp
    return {
        "instruction": instruction,
        "instruction_rva": instruction - main["base"] if in_main(instruction, main) else None,
        "raw": raw.hex(),
        "target": capture(reader, target, main),
    }


def find_descriptors(r: Reflection):
    found = {}
    assets = []
    for index, obj in r.objects_iter():
        try:
            name = safe_name(r, obj)
            cls = r.u64(obj + 16)
            class_name = safe_name(r, cls)
            if name in FUNCTION_NAMES and class_name == "Function":
                native = r.u64(obj + 0xD8)
                found[name] = {
                    "index": index,
                    "object": obj,
                    "native": native,
                    "properties": list(r.props(obj)),
                    "outer_chain": outer_chain(r, obj),
                }
            if name in ASSET_NAMES:
                item = {
                    "index": index,
                    "address": obj,
                    "name": name,
                    "class_name": class_name,
                    "class_address": cls,
                    "outer_chain": outer_chain(r, obj),
                    "properties": [],
                }
                try:
                    props = list(r.props(obj if class_name in {"Class", "ScriptStruct", "Function"} else cls))
                    for prop in props:
                        p = dict(prop)
                        # Runtime decode only for non-descriptor live objects.
                        if class_name not in {"Class", "ScriptStruct", "Function"}:
                            p["runtime"] = read_simple_property(r, obj, prop)
                        item["properties"].append(p)
                except (OSError, ValueError, UnicodeError) as error:
                    item["properties_error"] = str(error)
                assets.append(item)
        except (OSError, ValueError, UnicodeError):
            continue
    return found, assets


def find_local_controller_and_graphs(r: Reflection):
    local = locate_local_player(r)
    controller = local["controller"]
    state = local["player_state"]

    # PlayerState fields are especially useful for finding actual XP/progress state.
    player_state_fields = []
    if state:
        state_cls = r.u64(state + 16)
        for prop in r.props(state_cls):
            if any(word in prop["name"].casefold() for word in PLAYER_FIELD_WORDS):
                p = dict(prop)
                p["runtime"] = read_simple_property(r, state, prop)
                player_state_fields.append(p)

    graphs = []
    for index, obj in r.objects_iter():
        try:
            cls = r.u64(obj + 16)
            if safe_name(r, cls) != "GbxProgressGraph":
                continue
            if safe_name(r, obj) == "Default__GbxProgressGraph":
                continue
            graphs.append({"index": index, "address": obj, "name": safe_name(r, obj)})
            if len(graphs) >= 64:
                break
        except (OSError, ValueError, UnicodeError):
            continue
    return local, player_state_fields, graphs


def main():
    config = load_json(ROOT / "trainer_config.json")
    expected = config["expected_executable"]
    baseline = load_json(ROOT / "research" / "local_baseline.json")
    expected_hash = baseline["executables"][1]["sha256"]
    if sha256(expected) != expected_hash:
        raise ValueError("Executable version changed; refusing stale native research")

    pids = game_pids()
    if len(pids) != 1:
        raise ValueError(f"Expected one Borderlands4.exe process; found {len(pids)}")

    signatures = load_json(ROOT / "research" / "reflection_signatures.local.json")
    with Reader(pids[0], expected) as reader:
        mains = [m for m in modules(reader.pid) if m["path"].casefold() == expected.casefold()]
        if len(mains) != 1:
            raise ValueError("Main module not uniquely identified")
        main = mains[0]
        r = Reflection(reader, main["base"], signatures)
        functions, assets = find_descriptors(r)
        local, player_state_fields, graphs = find_local_controller_and_graphs(r)

        required = {"Server_AddCurrency", "GiveCurrency", "Server_SpendProgressPoints", "AwardKillExperienceToParty"}
        missing = sorted(required - set(functions))
        if missing:
            raise ValueError(f"Required functions not found: {missing}")

        controller = local["controller"]
        controller_vtable = r.u64(controller)
        currency_virtual_target = r.u64(controller_vtable + 0xFA8)

        # GbxProgressGraph.Server_SpendProgressPoints thunk dispatches [vtable+0x2C8].
        progress_targets = {}
        for graph in graphs:
            try:
                vtable = r.u64(graph["address"])
                target = r.u64(vtable + 0x2C8)
                key = str(target)
                row = progress_targets.setdefault(key, {
                    "target": capture(reader, target, main),
                    "instances": [],
                })
                row["instances"].append({**graph, "vtable": vtable})
            except OSError as error:
                graph["error"] = str(error)

        award_native = functions["AwardKillExperienceToParty"]["native"]
        give_native = functions["GiveCurrency"]["native"]

        result = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "process": reader.identity(),
            "main_module": main,
            "functions": {
                name: {
                    **row,
                    "native_capture": capture(reader, row["native"], main),
                }
                for name, row in functions.items()
            },
            "dispatch_targets": {
                "Server_AddCurrency_vtable_0xFA8": {
                    "controller": controller,
                    "controller_vtable": controller_vtable,
                    "target": capture(reader, currency_virtual_target, main),
                },
                "Server_SpendProgressPoints_vtable_0x2C8": list(progress_targets.values()),
                # Offsets are guarded by opcode checks and originate from the captured reflected thunks.
                "AwardKillExperienceToParty_call_0x7D": rel32_call(reader, award_native + 0x7D, main),
                "GiveCurrency_call_0xE8": rel32_call(reader, give_native + 0xE8, main),
            },
            "local_player": local,
            "player_state_interesting_fields": player_state_fields,
            "named_assets": assets,
            "notes": [
                "No game memory was written.",
                "Virtual targets are resolved from the live local controller/progress-graph vtables.",
                "The next analysis step is to identify authoritative add/subtract XP, currency and progress-point instructions inside these deeper targets.",
            ],
        }
        reader.require_alive()

    output = ROOT / "research" / "deep_feature_probe.local.json"
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output)

    print(json.dumps({
        "output": str(output),
        "pid": pids[0],
        "read_only": True,
        "function_count": len(functions),
        "named_asset_count": len(assets),
        "progress_graph_instances": len(graphs),
        "progress_dispatch_targets": len(result["dispatch_targets"]["Server_SpendProgressPoints_vtable_0x2C8"]),
        "currency_dispatch_rva": result["dispatch_targets"]["Server_AddCurrency_vtable_0xFA8"]["target"].get("rva"),
        "award_inner_rva": result["dispatch_targets"]["AwardKillExperienceToParty_call_0x7D"].get("target", {}).get("rva"),
        "give_currency_inner_rva": result["dispatch_targets"]["GiveCurrency_call_0xE8"].get("target", {}).get("rva"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
