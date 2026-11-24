"""Third-stage read-only BL4 probe.

Resolves the final native targets behind the already-validated reflected thunks
and decodes OakPlayerState.ExperienceState using the reflected
PlayerExperienceState layout. This script never opens the target with write
permissions and never writes game memory.
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
from feature_probe import load_json, sha256, safe_name, outer_chain, locate_local_player

CAPTURE_SMALL = 4096
CAPTURE_LARGE = 32768


def in_main(address: int, main: dict) -> bool:
    return main["base"] <= address < main["base"] + main["size"]


def capture(reader: Reader, address: int, main: dict, size: int):
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


def follow_rel32(reader: Reader, instruction: int, main: dict, opcodes=(0xE8, 0xE9), capture_size=CAPTURE_LARGE):
    raw = reader.read(instruction, 5)
    if raw[0] not in opcodes:
        return {
            "instruction": instruction,
            "instruction_rva": instruction - main["base"] if in_main(instruction, main) else None,
            "raw": raw.hex(),
            "error": f"expected rel32 opcode {opcodes}, got 0x{raw[0]:02x}",
        }
    disp = struct.unpack_from("<i", raw, 1)[0]
    target = instruction + 5 + disp
    return {
        "instruction": instruction,
        "instruction_rva": instruction - main["base"] if in_main(instruction, main) else None,
        "opcode": f"0x{raw[0]:02x}",
        "raw": raw.hex(),
        "target": capture(reader, target, main, capture_size),
    }


def find_function_and_structs(r: Reflection):
    functions = {}
    structs = {}
    interesting_names = []
    wanted_functions = {"Server_AddCurrency", "Server_SpendProgressPoints", "AwardKillExperienceToParty"}
    wanted_structs = {"PlayerExperienceState", "GbxProgressPointsContainer", "GbxProgressPointsAmount"}
    words = ("progresspoints", "skillpoints", "experiencepoints", "experience_multiplier", "experiencemultiplier")

    for index, obj in r.objects_iter():
        try:
            name = safe_name(r, obj)
            cls = r.u64(obj + 16)
            class_name = safe_name(r, cls)
            if name in wanted_functions and class_name == "Function":
                functions[name] = {
                    "index": index,
                    "object": obj,
                    "native": r.u64(obj + 0xD8),
                    "properties": list(r.props(obj)),
                    "outer_chain": outer_chain(r, obj),
                }
            if name in wanted_structs and class_name == "ScriptStruct":
                size = r.i32(obj + 0x58)
                structs[name] = {
                    "index": index,
                    "object": obj,
                    "size": size,
                    "properties": list(r.props(obj)),
                    "outer_chain": outer_chain(r, obj),
                }
            folded = name.casefold()
            if any(word in folded for word in words) and len(interesting_names) < 256:
                interesting_names.append({
                    "index": index,
                    "address": obj,
                    "name": name,
                    "class_name": class_name,
                    "outer_chain": outer_chain(r, obj),
                })
        except (OSError, ValueError, UnicodeError):
            continue
    return functions, structs, interesting_names


def find_progress_graphs(r: Reflection, limit=64):
    graphs = []
    for index, obj in r.objects_iter():
        try:
            cls = r.u64(obj + 16)
            if safe_name(r, cls) != "GbxProgressGraph":
                continue
            if safe_name(r, obj) == "Default__GbxProgressGraph":
                continue
            graphs.append({"index": index, "address": obj, "vtable": r.u64(obj)})
            if len(graphs) >= limit:
                break
        except (OSError, ValueError, UnicodeError):
            continue
    return graphs


def find_property(r: Reflection, instance: int, prop_name: str):
    cls = r.u64(instance + 16)
    for prop in r.props(cls):
        if prop["name"] == prop_name:
            return prop
    raise ValueError(f"Property {prop_name!r} not found")


def decode_experience_state(r: Reflection, player_state: int, struct_desc: dict):
    prop = find_property(r, player_state, "ExperienceState")
    header_addr = player_state + prop["offset"]
    raw_header = r.p.read(header_addr, 16)
    data_ptr, count, capacity = struct.unpack("<Qii", raw_header)
    size = struct_desc["size"]
    if not 0 < size <= 1024:
        raise ValueError(f"Unexpected PlayerExperienceState size: {size}")
    if not 0 <= count <= capacity <= 128:
        raise ValueError(f"Unexpected ExperienceState TArray bounds: {count}/{capacity}")

    rows = []
    props = {p["name"]: p for p in struct_desc["properties"]}
    for index in range(count):
        address = data_ptr + index * size
        raw = r.p.read(address, size)
        row = {"index": index, "address": address, "raw": raw.hex()}
        for name in ("ExperienceLevel", "ExperiencePoints", "ExperiencePointsRequiredForPreviousLevel", "ExperiencePointsRequiredForNextLevel", "bIsUnlocked"):
            p = props.get(name)
            if not p or not 0 <= p["offset"] < size:
                continue
            off = p["offset"]
            if p["kind"] == "IntProperty" and off + 4 <= size:
                row[name] = struct.unpack_from("<i", raw, off)[0]
            elif p["kind"] == "UInt64Property" and off + 8 <= size:
                row[name] = struct.unpack_from("<Q", raw, off)[0]
            elif p["kind"] == "BoolProperty" and off < size:
                row[name] = raw[off]
        rows.append(row)
    return {
        "property": prop,
        "header_address": header_addr,
        "data": data_ptr,
        "count": count,
        "capacity": capacity,
        "element_size": size,
        "elements": rows,
    }


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
        functions, structs, interesting_names = find_function_and_structs(r)
        local = locate_local_player(r)
        graphs = find_progress_graphs(r)

        for required in ("Server_AddCurrency", "Server_SpendProgressPoints", "AwardKillExperienceToParty"):
            if required not in functions:
                raise ValueError(f"Required function not found: {required}")
        if "PlayerExperienceState" not in structs:
            raise ValueError("PlayerExperienceState ScriptStruct not found")

        controller = local["controller"]

        # 1) Currency: Server_AddCurrency dispatches controller vtable+0xFA8 to a tiny
        # thunk which forwards through an embedded object at controller+0x9B0 and
        # that embedded object's vtable+0x28.
        controller_vtable = r.u64(controller)
        currency_thunk = r.u64(controller_vtable + 0xFA8)
        thunk_prefix = reader.read(currency_thunk, 0x15)
        expected_prefix = bytes.fromhex("488b81b00900004881c1b0090000488b402848ffe0")
        if thunk_prefix != expected_prefix:
            raise ValueError(f"Currency dispatch thunk changed: {thunk_prefix.hex()}")
        currency_embedded = controller + 0x9B0
        currency_embedded_vtable = r.u64(currency_embedded)
        currency_final = r.u64(currency_embedded_vtable + 0x28)

        # 2) Progress points: all observed GbxProgressGraph objects should share
        # vtable+0x2C8. The validated implementation then calls the authoritative
        # handler at +0x51 via rel32.
        progress_targets = sorted({r.u64(g["vtable"] + 0x2C8) for g in graphs})
        if len(progress_targets) != 1:
            raise ValueError(f"Expected one progress dispatch target; found {len(progress_targets)}")
        progress_dispatch = progress_targets[0]
        progress_final = follow_rel32(reader, progress_dispatch + 0x51, main, opcodes=(0xE8,), capture_size=CAPTURE_LARGE)

        # 3) XP: reflected AwardKillExperienceToParty has a rel32 call at +0x7D.
        # Its helper ends in a validated rel32 tail-jump at helper+0x60.
        award_native = functions["AwardKillExperienceToParty"]["native"]
        award_helper = follow_rel32(reader, award_native + 0x7D, main, opcodes=(0xE8,), capture_size=CAPTURE_SMALL)
        if "target" not in award_helper:
            raise ValueError("Could not resolve AwardKillExperienceToParty helper")
        helper_addr = award_helper["target"]["address"]
        xp_final = follow_rel32(reader, helper_addr + 0x60, main, opcodes=(0xE9,), capture_size=CAPTURE_LARGE)

        experience_state = decode_experience_state(r, local["player_state"], structs["PlayerExperienceState"])

        result = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "process": reader.identity(),
            "main_module": main,
            "final_targets": {
                "currency": {
                    "controller": controller,
                    "controller_vtable": controller_vtable,
                    "first_dispatch": capture(reader, currency_thunk, main, 256),
                    "embedded_object": currency_embedded,
                    "embedded_vtable": currency_embedded_vtable,
                    "vtable_slot": "0x28",
                    "final": capture(reader, currency_final, main, CAPTURE_LARGE),
                },
                "progress_points": {
                    "graph_count": len(graphs),
                    "dispatch": capture(reader, progress_dispatch, main, 512),
                    "call_instruction_offset": "0x51",
                    "final": progress_final,
                },
                "experience": {
                    "award_native": capture(reader, award_native, main, 256),
                    "helper": award_helper,
                    "tail_jump_offset": "0x60",
                    "final": xp_final,
                },
            },
            "player_state": local["player_state"],
            "experience_state": experience_state,
            "structs": structs,
            "interesting_names": interesting_names,
            "notes": [
                "No game memory was written.",
                "Currency final target is resolved through controller+0x9B0 embedded-object vtable+0x28.",
                "Progress final target is followed from the validated vtable+0x2C8 implementation call at +0x51.",
                "Experience final target is followed from the validated helper tail-jump at +0x60.",
                "ExperienceState is decoded from the reflected PlayerExperienceState struct size and property offsets.",
            ],
        }
        reader.require_alive()

    output = ROOT / "research" / "final_feature_probe.local.json"
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output)

    print(json.dumps({
        "output": str(output),
        "pid": pids[0],
        "read_only": True,
        "currency_final_rva": result["final_targets"]["currency"]["final"].get("rva"),
        "progress_final_rva": result["final_targets"]["progress_points"]["final"].get("target", {}).get("rva"),
        "xp_final_rva": result["final_targets"]["experience"]["final"].get("target", {}).get("rva"),
        "experience_state_count": result["experience_state"]["count"],
        "experience_states": [
            {
                "index": row["index"],
                "level": row.get("ExperienceLevel"),
                "xp": row.get("ExperiencePoints"),
                "prev": row.get("ExperiencePointsRequiredForPreviousLevel"),
                "next": row.get("ExperiencePointsRequiredForNextLevel"),
                "unlocked": row.get("bIsUnlocked"),
            }
            for row in result["experience_state"]["elements"]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
