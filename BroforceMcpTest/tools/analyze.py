"""Reproduce offline Broforce API/IL and saved-key analysis; never launch or inject."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME = Path(r"F:\SteamLibrary\steamapps\common\Broforce")
MANAGED = GAME / "Broforce_beta_Data" / "Managed"
COMPILER = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
PROBE = ROOT / "build" / "InspectAssembly.exe"

GROUPS = [
    ("candidate_types.txt", "list", r"Input|Control|KeyBind|KeyMap|Pause|GameMode|Menu|Options"),
    ("core_api.txt", "types", r"^(InputReader|PlayerOptions|Player|HeroController|TestVanDammeAnim|PolymorphicAI|PathAgent|PathAgentCapabilities|PauseController|GameMode|GameModeController|LevelSelectionController|Connect|KeyBindingMenu|Menu|ControllerNum|ControllerType)$"),
    ("world_api.txt", "types", r"^(InputKey|InputReader\+RewiredAction|PauseStatus|GameMode|KeyBindingMenu\+ControlToBind|ControllerType|Map|Unit|ActionState|CheckPoint|Projectile|PlayerUserData)$"),
    ("support_api.txt", "types", r"^(BotBrain|InputState|InputStateChange|BroforceObject|NetworkedUnit|PathAgent|DoodadCage|PlayerUserData|NewChatController|CutsceneController|PauseMenu|PlayerOptions|NetworkObject|PID)$"),
    ("input_reader.il.txt", "il", r"^InputReader::"),
    ("input_flow.il.txt", "il", r"^(Player::(GetInput|get_controllerNum|IsAlive|IsAliveAndSpawnedHero)|TestVanDammeAnim::(CheckInput|RunMovement)|HeroController::(GetPlayerNumForControllerID|GetPlayerNums|IsPlaying)|PolymorphicAI::GetInput|PauseController::[^:]+)$"),
    ("action_semantics.il.txt", "il", r"^(Player::(get_UsingBotBrain|set_UsingBotBrain)|BotBrain::GetInput|TestVanDammeAnim::(PressHighFiveMelee|CheckDashing|GetSprintValue|get_InputBits|set_InputBits|ClearAllInput|CheckTriggerActionInput|CheckWhetherShouldClearInput)|InputReader::GetKeyboardInput)$"),
]

def run(command):
    result = subprocess.run([str(v) for v in command], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8-sig").replace("\r\n", "\n")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "evidence").mkdir(exist_ok=True)
    compile_args = [COMPILER, "/nologo", "/noconfig", "/target:exe", "/out:" + str(PROBE),
                    "/reference:" + str(COMPILER.parent / "System.dll"), ROOT / "tools" / "InspectAssembly.cs"]
    run(compile_args)
    summary = []
    for filename, mode, pattern in GROUPS:
        output = run([PROBE, MANAGED, "Assembly-CSharp.dll", mode, pattern])
        (ROOT / "evidence" / filename).write_text(output, encoding="utf-8")
        matches = int(re.search(r"MATCHED (\d+)\s*$", output).group(1))
        summary.append({"file": filename, "mode": mode, "matched": matches, "sha256": sha(ROOT / "evidence" / filename)})
        print(f"{filename}: {matches} matches")

    keycode = run([PROBE, MANAGED, "UnityEngine.CoreModule.dll", "types", r"^UnityEngine.KeyCode$"])
    (ROOT / "evidence" / "keycode_enum.txt").write_text(keycode, encoding="utf-8")
    options = GAME / "Saves" / "Options.xml"
    tree = ET.parse(options).getroot()
    profiles = []
    for keyboard_id in range(4):
        prefix = f"keyPlayer{keyboard_id + 1}"
        bindings = {node.tag[len(prefix):]: node.text for node in tree if node.tag.startswith(prefix)}
        profiles.append({"keyboard_id": keyboard_id, "config_label": f"Player{keyboard_id+1}", "bindings": bindings})
    saved_keys = {"source": str(options), "source_sha256": sha(options), "evidence_level": "saved_options_not_live",
                  "note": "Keyboard profiles are not proof of the current player-slot/controller assignment.",
                  "keyboard_profiles": profiles, "gamepad_physical_bindings": "not_inspected_runtime_rewired_mapping_required"}
    (ROOT / "evidence" / "saved_keybindings.json").write_text(json.dumps(saved_keys, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = GAME.parents[1] / "appmanifest_274190.acf"
    manifest = manifest_path.read_text(encoding="utf-8")
    version = {
        "analysis_version": "0.1-analysis", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "app_id": 274190, "steam_build_id": re.search(r'"buildid"\s+"(\d+)"', manifest).group(1),
        "game_root": str(GAME), "backend": "Unity Mono managed IL", "runtime_validation": False,
        "files": {str(p.relative_to(GAME)): {"size": p.stat().st_size, "sha256": sha(p)} for p in [GAME / "Broforce_beta.exe", MANAGED / "Assembly-CSharp.dll", GAME / "UnityPlayer.dll"]},
        "assembly_mvid": re.search(r"MVID=([^\s]+)", (ROOT / "evidence" / "core_api.txt").read_text(encoding="utf-8")).group(1),
        "analysis_host": "64-bit .NET Framework 4.x, ReflectionOnly; signatures may display CLR-unified system type names",
        "extractions": summary,
    }
    (ROOT / "evidence" / "game_version.json").write_text(json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Build {version['steam_build_id']}; {len(profiles)} keyboard profiles; no game execution")

if __name__ == "__main__":
    main()
