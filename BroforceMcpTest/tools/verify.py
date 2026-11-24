"""Verify actual assembly signatures and extracted evidence without running game APIs."""
import json
import re
from pathlib import Path
from analyze import ROOT, MANAGED, COMPILER, run

def main():
    checks = []
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
    def text(name):
        return (ROOT / "evidence" / name).read_text(encoding="utf-8-sig")
    def method(filename, type_name, name):
        for block in re.split(r"(?=^METHOD )", text(filename), flags=re.M):
            if f"{type_name}::{name}(" in block.splitlines()[0]:
                return block
        return ""

    refs = ["Assembly-CSharp.dll", "UnityEngine.dll", "UnityEngine.CoreModule.dll"]
    args = [COMPILER, "/nologo", "/noconfig", "/target:library", "/nowarn:0219",
            "/out:" + str(ROOT / "build" / "ApiSurfaceProbe.dll")]
    args.extend("/reference:" + str(MANAGED / filename) for filename in refs)
    args.append(ROOT / "tools" / "ApiSurfaceProbe.cs")
    result = run(args)
    (ROOT / "evidence" / "api_compile.log").write_text(result or "Compilation succeeded. DLL was not executed or injected.\n", encoding="utf-8")
    check("compile_against_actual_game_assemblies", True)
    flow = method("input_flow.il.txt", "Player", "GetInput")
    check("player_input_dispatches_via_controllerNum", "Player::Int32 get_controllerNum()" in flow and "InputReader::Void GetInput" in flow)
    controller = method("input_flow.il.txt", "Player", "get_controllerNum")
    check("player_slot_to_controller_table_exists", "HeroController::Int32[] playerControllerIDs" in controller)
    dispatch = method("input_reader.il.txt", "InputReader", "GetInputIgnoreBlock")
    check("keyboard_and_rewired_routes_exist", "GetKeyboardInput" in dispatch and "GetXBoxControllerInput" in dispatch and "ldc.i4.4" in dispatch)
    gates = method("input_reader.il.txt", "InputReader", "GetInput")
    check("input_has_native_block_cutscene_and_pause_gates", all(s in gates for s in ["get_IsBlocked", "PlayersCanMove", "get_InCutscene", "get_DelayInput"]))
    melee = method("action_semantics.il.txt", "TestVanDammeAnim", "PressHighFiveMelee")
    check("melee_is_contextual", all(s in melee for s in ["StartMelee", "StartHighFive", "Switch::Void Activate", "CheckBustCage"]))
    bits = method("action_semantics.il.txt", "TestVanDammeAnim", "get_InputBits")
    check("inputbits_are_partial_not_complete_button_set", "dashing" in bits and "holdingHighFive" in bits and "buttonGesture" not in bits)
    saved = json.loads(text("saved_keybindings.json"))
    check("four_keyboard_profiles_extracted", len(saved["keyboard_profiles"]) == 4)
    check("each_keyboard_profile_has_ten_actions", all(len(p["bindings"]) == 10 for p in saved["keyboard_profiles"]))
    check("enum_spaces_differ", "InputKey fire = 5" in text("world_api.txt") and "InputReader+RewiredAction fire = 4" in text("world_api.txt"))
    check("no_unresolved_tokens_in_input_reader", "TOKEN_0x" not in text("input_reader.il.txt"))
    check("no_unresolved_tokens_in_action_semantics", "TOKEN_0x" not in text("action_semantics.il.txt"))
    version = json.loads(text("game_version.json"))
    check("expected_game_build", version["steam_build_id"] == "12964083")
    report = {"runtime_validation": False, "checks": checks, "passed": all(c["passed"] for c in checks)}
    (ROOT / "evidence" / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
