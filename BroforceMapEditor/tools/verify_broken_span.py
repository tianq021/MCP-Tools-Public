"""Verify the bridge break, the gravity drop, and the extraction of Broken Span."""

import json
import time

import bridge_client as bridge
from make_broken_span import MAP_ID
from map_authoring import MAPS, ROOT, command, read_project
from verify_supermap import entities, ensure_player, installed_campaign, move, sample_blocks


def inspect_and_load(project):
    command(dict(op="inspect_level", path=MAPS / f"{MAP_ID}.bfc",
                 name=project["name"], description=project["description"]))
    command(dict(op="load"))
    time.sleep(3)
    ensure_player()


def test_extraction(project):
    move(180, 63)
    time.sleep(0.55)
    move(207, 64)
    time.sleep(0.8)
    blocked = command(dict(op="runtime_triggers"))

    # Use a temporary in-memory test blast to exercise the actual boss-death trigger.
    tiles = project["tiles"][:]
    row = project["height"] - 1 - 64
    tiles[row] = tiles[row][:196] + "B.B" + tiles[row][199:]
    test_tiles = ROOT / "evidence" / "broken_span_boss_test_tiles.txt"
    test_tiles.write_text("\n".join(tiles), encoding="ascii")
    command(dict(op="set_tiles", tiles=test_tiles,
                 legend=",".join(f"{key}:{value}" for key, value in project["legend"].items())))
    command(dict(op="add_trigger", type=0, blx=191, bly=60, urx=193, ury=66,
                 action="Collapse", target_x=196, target_y=64, persist_on_reload=0))
    command(dict(op="load"))
    time.sleep(3)
    ensure_player()
    move(192, 63)
    time.sleep(2.4)
    defeated = command(dict(op="runtime_triggers"))
    move(207, 64)
    for _ in range(12):
        time.sleep(0.55)
        result = command(dict(op="state"))
        if result.get("lastCompletedMap") == project["name"]:
            break
    return dict(blocked=blocked, defeated=defeated, extraction=result)


def run():
    bridge.wait_ready(timeout=5)
    project = read_project(MAP_ID)
    inspect_and_load(project)
    start = command(dict(op="player_pos"))
    cells = [(x, 61) for x in range(72, 83)]
    before = sample_blocks(cells)
    move(69, 62)
    time.sleep(1.7)
    after = sample_blocks(cells)
    move(75, 62)
    time.sleep(0.85)
    landing = command(dict(op="player_pos"))
    platforms = {}
    for x, y in [(83, 46), (90, 38), (98, 30), (111, 22), (112, 11),
                 (127, 23), (152, 17), (168, 36), (164, 44), (160, 52), (173, 63)]:
        move(x, y)
        time.sleep(0.22)
        platforms[f"{x},{y}"] = bridge.send_command(dict(op="player_pos"))
    proof = dict(start=start, bridge_before=before, bridge_after=after, landing=landing,
                 platforms=platforms, boss=entities(("Satan", "General")))
    proof["finish"] = test_extraction(project)
    evidence = ROOT / "evidence" / "broken_span_test.json"
    evidence.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(value.endswith(",1,0,0,0") for value in before.values()):
        raise RuntimeError("Expected all bridge segments present before the cut.")
    if not all(value.endswith(",0,0,0,1") for value in after.values()):
        raise RuntimeError("Expected all selected bridge segments to break.")
    if int(landing["cellY"]) != 54 or landing["solidBelow"] != "True":
        raise RuntimeError("The actual gravity drop did not land on the first terrace.")
    if proof["finish"]["extraction"].get("lastCompletedMap") != project["name"]:
        raise RuntimeError("Boss-gated extraction did not finish.")
    print(f"Runtime checks passed: {evidence}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    options = parser.parse_args()
    run()
    if options.install:
        result = installed_campaign(MAP_ID)
        path = ROOT / "evidence" / "broken_span_install.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Installation checked: {path}")
