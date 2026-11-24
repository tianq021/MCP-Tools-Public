"""Run labeled observations through the existing game bridge. Uses the active game."""

import argparse
import json
import time
from pathlib import Path

import bridge_client as bridge
from map_authoring import MAPS, ROOT, command, read_project


def sample_blocks(cells):
    path = ROOT / "evidence" / "supermap_cells.txt"
    path.write_text("\n".join(f"{x},{y}" for x, y in cells), encoding="ascii")
    observed = command(dict(op="probe_blocks", cells=path))
    return {key: value for key, value in observed.items() if key != "ok"}


def move(x, y):
    try:
        command(dict(op="test_move_player", x=x, y=y))
    except bridge.BridgeError as error:
        if "no hero" not in str(error):
            raise
        ensure_player()
        command(dict(op="test_move_player", x=x, y=y))


def entities(names):
    response = command(dict(op="runtime_entities"))
    return [value for key, value in response.items()
            if key.startswith("entity.") and any(name in value for name in names)]


def ensure_player():
    command(dict(op="ensure_player"))
    time.sleep(1)


def trigger_state(x, y, settle=0.75):
    move(x, y)
    time.sleep(settle)
    return {"position": bridge.send_command(dict(op="player_pos")),
            "live": command(dict(op="runtime_triggers"))}


def probe():
    cells = [(21, 11), (21, 4), (40, 4), (42, 4), (44, 4), (45, 3), (103, 4)]
    ensure_player()
    out = {"start": command(dict(op="player_pos")), "before": sample_blocks(cells)}
    out["enemy_before"] = entities(("Undead", "Satan", "General"))
    move(14, 4)
    time.sleep(0.35)
    out["after_fall_trigger"] = sample_blocks(cells)
    move(32, 4)
    time.sleep(2.0)
    out["after_barrel_trigger"] = sample_blocks(cells)
    ensure_player()
    move(53, 4)
    out["resurrection_samples"] = []
    for _ in range(7):
        out["resurrection_samples"].append(entities(("Undead",)))
        time.sleep(0.45)
    out["enemy_after"] = entities(("Satan", "General"))
    return out


def campaign(check_finish):
    project = read_project("裂谷回声")
    command(dict(op="inspect_level", path=MAPS / "裂谷回声.bfc",
                 name=project["name"], description=project["description"]))
    command(dict(op="load"))
    time.sleep(2.2)
    ensure_player()
    out = {"start": command(dict(op="player_pos"))}
    out["boss_near_final_room"] = entities(("Satan", "General"))
    out["triggers"] = command(dict(op="dump_triggers"))
    out["before_approach_live"] = command(dict(op="runtime_triggers"))
    out["after_approach_live"] = trigger_state(401, 25)
    out["locked_exit_live"] = trigger_state(451, 25)
    out["extraction_with_living_boss"] = command(dict(op="state"))
    out["boss_after_locked_exit"] = entities(("Satan", "General"))
    waypoints = [(18, 6), (70, 10), (137, 10), (215, 10),
                 (298, 15), (330, 20), (368, 25)]
    out["waypoints"] = {}
    for x, y in waypoints:
        move(x, y)
        time.sleep(0.18)
        out["waypoints"][f"{x},{y}"] = command(dict(op="player_pos"))
    out["hazard_triggers"] = {}
    for x, y, cells in [(79, 9, [(88, 16)]), (98, 8, [(104, 15)]),
                        (159, 10, [(164, 10), (166, 10), (168, 10)])]:
        state = trigger_state(x, y, settle=2.0)
        state["blocks"] = sample_blocks(cells)
        out["hazard_triggers"][str(x)] = state
    move(451, 27)
    time.sleep(1.5)
    out["hazards_after"] = sample_blocks([(88, 16), (104, 15), (164, 10), (166, 10), (168, 10)])
    if check_finish:
        out["controlled_boss_defeat"] = test_boss_defeat()
    return out


def test_boss_defeat():
    # A temporary copy in the bridge's authoring memory. The delivered BFC is unchanged.
    project = read_project("裂谷回声")
    row = project["height"] - 1 - 25
    tiles = project["tiles"]
    tiles[row] = tiles[row][:417] + "B.B" + tiles[row][420:]
    path = ROOT / "evidence" / "supermap_battle_test_tiles.txt"
    path.write_text("\n".join(tiles), encoding="ascii")
    command(dict(op="set_tiles", tiles=path,
                 legend=",".join(f"{k}:{v}" for k, v in project["legend"].items())))
    command(dict(op="add_trigger", type=0, blx=407, bly=23, urx=414, ury=32,
                 action="Collapse", target_x=417, target_y=25, persist_on_reload=0))
    command(dict(op="load"))
    time.sleep(3)
    ensure_player()
    out = {"before": entities(("Satan", "General"))}
    out["battle_live"] = trigger_state(410, 25, settle=2.5)
    out["after"] = entities(("Satan", "General"))
    out["test_barrels"] = sample_blocks([(417, 25), (419, 25)])
    out["before_extraction"] = command(dict(op="state"))
    move(451, 25)
    for _ in range(15):
        time.sleep(0.6)
        status = command(dict(op="state"))
        if status.get("lastCompletedMap") == "裂谷回声":
            break
    out["extraction"] = status
    if status["isCampaignScene"] == "True":
        out["exit_live"] = command(dict(op="runtime_triggers"))
        out["exit_position"] = bridge.send_command(dict(op="player_pos"))
    return out


def install(map_id="裂谷回声"):
    source = MAPS / f"{map_id}.bfc"
    levels = Path(r"F:\SteamLibrary\steamapps\common\Broforce\Levels")
    target = levels / source.name
    data = source.read_bytes()
    levels.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as out:
            out.write(data)
    except FileExistsError:
        if target.read_bytes() != data:
            raise
    return {"path": str(target), "matches_export": target.read_bytes() == data}


def installed_campaign(map_id="裂谷回声"):
    receipt = install(map_id)
    bridge.wait_ready(timeout=5)
    command(dict(op="load_offline_test", file=map_id))
    time.sleep(3)
    ensure_player()
    return {**receipt, "state": command(dict(op="state")),
            "player": command(dict(op="player_pos")),
            "boss": entities(("Satan", "General")),
            "triggers": command(dict(op="runtime_triggers"))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["probe", "campaign", "install"])
    parser.add_argument("--finish", action="store_true")
    args = parser.parse_args()
    if args.mode == "install":
        result = installed_campaign()
    else:
        bridge.wait_ready(timeout=5)
        result = probe() if args.mode == "probe" else campaign(args.finish)
    path = ROOT / "evidence" / f"supermap_{args.mode}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evidence written to {path}")
