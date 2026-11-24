"""Attack damage + defeat-cause watch.

Fresh offline skirmish in E:\\RA2MCP\\test-game: deploy MCV at start, build
power plant + barracks, train one GI, order ATTACK on the nearest Neutral
building, then watch (no further commands) up to N seconds recording:
  * target health over time (real damage evidence),
  * every AI/human object count, so a defeat can be explained (destroyed vs not),
  * house flags (defeated/is_loser/is_winner) and stage.
Never sends SELL/SELL_CELL.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_attack_watch.py -o reports/dsh-attack-watch.json
"""
import argparse
import asyncio
import json
import math
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))

from pyra2yr.network import DualClient  # noqa: E402
from ra2yrproto import commands_game, commands_yr, ra2yr  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402

TYPE_BUILDING = "ABSTRACT_TYPE_BUILDING"
TYPE_UNIT = "ABSTRACT_TYPE_UNIT"
TYPE_INFANTRY = "ABSTRACT_TYPE_INFANTRY"

report = {"script": "dsh_attack_watch.py", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "steps": [], "verdict": {}}


def step(label, **fields):
    rec = {"step": label, "wall": time.strftime("%H:%M:%S"), **fields}
    report["steps"].append(rec)
    print(f"[{rec['wall']}] {label}: {json.dumps(fields, ensure_ascii=False)[:600]}")
    return rec


async def main(opts):
    client = DualClient("127.0.0.1", opts.port, timeout=2)
    client.connect()

    async def send(cmd, into, timeout=None):
        t = time.monotonic()
        try:
            r = await asyncio.wait_for(client.exec_command(cmd, timeout or 5.0),
                                       (timeout or 5.0) + 3)
        except asyncio.TimeoutError:
            return None, None, "timeout"
        ms = round((time.monotonic() - t) * 1000.0, 2)
        if getattr(r, "result_code", 0) != 0:
            return ms, None, f"native_error: {getattr(r, 'error_message', '')}"
        msg = into()
        if not r.result.Unpack(msg):
            return ms, None, "unpack_failed"
        return ms, msg, None

    async def state():
        q = commands_yr.GetGameState()
        ms, msg, err = await send(q, commands_yr.GetGameState)
        if err:
            raise RuntimeError(f"GetGameState: {err}")
        return msg.state

    def human(st):
        h = next((h for h in st.houses if h.current_player and h.is_human_player), None)
        if h is None:
            raise RuntimeError("no human house")
        return h

    def owned(st, house, kind=None):
        out = []
        for o in st.objects:
            if o.pointer_house != house.self or o.health <= 0:
                continue
            if kind and ra2yr.AbstractType.Name(o.object_type) != kind:
                continue
            out.append(o)
        return out

    async def unit_order(action, addresses, coords=None, target=0):
        q = commands_game.UnitOrder(object_addresses=list(addresses),
                                    action=getattr(ra2yr, action))
        if coords is not None:
            q.coordinates.x, q.coordinates.y = coords
        if target:
            q.target_object = target
        ms, msg, err = await send(q, commands_game.UnitOrder)
        return ms, msg, err

    st = await state()
    init, _ = await (lambda: (None, None))() if False else (None, None)
    q = commands_yr.ReadValue()
    q.data.initial_game_state.SetInParent()
    _, init_msg, err = await send(q, commands_yr.ReadValue, timeout=8)
    if err:
        raise RuntimeError(f"ReadValue: {err}")
    init = init_msg.data.initial_game_state
    names = {t.pointer_self: t.name for t in init.object_types}

    def find_type(name):
        return next((t for t in init.object_types if t.name == name), None)

    hh = human(st)
    units = owned(st, hh, TYPE_UNIT)
    if not units:
        raise RuntimeError("no MCV")
    mcv = units[0]
    ms, msg, err = await unit_order("UNIT_ACTION_DEPLOY", [mcv.pointer_self])
    t0 = time.monotonic()
    cy = None
    while time.monotonic() - t0 < 10:
        st = await state()
        b = owned(st, human(st), TYPE_BUILDING)
        if b:
            cy = b[0]
            break
        await asyncio.sleep(0.5)
    step("A_deploy", ack_ms=ms, ack_error=err, cy=None if cy is None else
         {"type": names.get(cy.pointer_technotypeclass, "?"),
          "xyz": [cy.coordinates.x, cy.coordinates.y]})
    if cy is None:
        raise RuntimeError("deploy failed")
    report["verdict"]["deploy"] = {"passed": True, "ack_ms": ms}

    async def build(name):
        tc = find_type(name)
        if tc is None:
            return False, f"type {name} missing"
        q = commands_game.ProduceOrder(object_type=tc, action=ra2yr.PRODUCE_ACTION_BEGIN)
        ms, msg, err = await send(q, commands_game.ProduceOrder)
        if err:
            return False, err
        obj = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.build_timeout:
            st = await state()
            hh2 = human(st)
            for f in st.factories:
                if f.owner == hh2.self and f.completed and f.object:
                    cand = next((o for o in st.objects if o.pointer_self == f.object
                                 and o.health > 0), None)
                    if cand is not None:
                        obj = cand
                        break
            if obj is not None:
                break
            await asyncio.sleep(1.0)
        if obj is None:
            return False, "build timeout"
        st = await state()
        hh2 = human(st)
        base = (cy.coordinates.x, cy.coordinates.y)
        cands = []
        for r in (1024, 2048, 3072):
            for dx, dy in [(r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, r), (r, -r), (-r, -r)]:
                cands.append({"x": base[0] + dx, "y": base[1] + dy})
        pq = commands_yr.PlaceQuery(type_class=tc.pointer_self, house_class=hh2.self)
        for pt in cands:
            pq.coordinates.add(x=pt["x"], y=pt["y"])
        ms, msg, err = await send(pq, commands_yr.PlaceQuery, timeout=10)
        if err or not msg.coordinates:
            return False, f"no place cell {err}"
        pb = commands_game.PlaceBuilding()
        pb.building.CopyFrom(obj)
        pb.coordinates.x = msg.coordinates[0].x
        pb.coordinates.y = msg.coordinates[0].y
        ms, msg2, err2 = await send(pb, commands_game.PlaceBuilding, timeout=10)
        step("build", name=name, build_ms=None, place_ack_ms=ms, place_error=err2,
             at=[pb.coordinates.x, pb.coordinates.y])
        return err2 is None, err2

    ok_pp, e_pp = await build("Allied Power Plant")
    ok_bar, e_bar = await build("Allied Barracks")
    step("B_build", power_plant=ok_pp, barracks=ok_bar, pp_err=e_pp, bar_err=e_bar)
    report["verdict"]["build"] = {"passed": bool(ok_pp and ok_bar)}

    gi_tc = find_type("GI")
    if not ok_bar or gi_tc is None:
        raise RuntimeError("cannot train GI")
    q = commands_game.ProduceOrder(object_type=gi_tc, action=ra2yr.PRODUCE_ACTION_BEGIN)
    ms, msg, err = await send(q, commands_game.ProduceOrder)
    gi = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < opts.build_timeout:
        st = await state()
        g = owned(st, human(st), TYPE_INFANTRY)
        if g:
            gi = g[0]
            break
        await asyncio.sleep(0.5)
    step("C_gi", ack_ms=ms, ack_error=err, trained=gi is not None,
         effect_ms=round((time.monotonic() - t0) * 1000.0, 2))
    if gi is None:
        raise RuntimeError("GI training failed")
    report["verdict"]["train_gi"] = {"passed": True}

    # nearest Neutral building as attack target
    st = await state()
    neutral = next((h for h in st.houses if h.name == "Neutral"), None)
    gi = owned(st, human(st), TYPE_INFANTRY)[0]
    gxy = (gi.coordinates.x, gi.coordinates.y)
    targets = [o for o in st.objects if neutral and o.pointer_house == neutral.self
               and o.health > 0 and o.on_map]
    targets.sort(key=lambda o: math.hypot(o.coordinates.x - gxy[0], o.coordinates.y - gxy[1]))
    tgt = targets[0] if targets else None
    if tgt is None:
        raise RuntimeError("no neutral target")
    hp0 = tgt.health
    dist0 = round(math.hypot(tgt.coordinates.x - gxy[0], tgt.coordinates.y - gxy[1]), 1)
    ms, msg, err = await unit_order("UNIT_ACTION_ATTACK", [gi.pointer_self],
                                    (tgt.coordinates.x, tgt.coordinates.y),
                                    tgt.pointer_self)
    step("D_attack_order", ack_ms=ms, ack_error=err,
         attacker=gi.pointer_self, target=tgt.pointer_self,
         target_cell=[tgt.coordinates.x, tgt.coordinates.y],
         target_hp=hp0, distance_leptons=dist0,
         native_echo=MessageToDict(msg) if msg else None)

    # watch: damage + defeat cause
    names_by_self = {h.self: h.name for h in st.houses}
    hp_min = hp0
    dmg_wall = None
    series = []
    end = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < opts.watch_timeout:
        st = await state()
        hh2 = human(st)
        by_owner = {}
        for o in st.objects:
            by_owner[o.pointer_house] = by_owner.get(o.pointer_house, 0) + 1
        tg = next((o for o in st.objects if o.pointer_self == tgt.pointer_self), None)
        atk = next((o for o in st.objects if o.pointer_self == gi.pointer_self), None)
        if tg is not None and tg.health < hp_min:
            hp_min = tg.health
            if dmg_wall is None:
                dmg_wall = time.strftime("%H:%M:%S")
        rec = {"wall": time.strftime("%H:%M:%S"), "frame": st.current_frame,
               "stage": ra2yr.LoadStage.Name(st.stage),
               "human_defeated": hh2.defeated, "human_is_loser": hh2.is_loser,
               "objects_by_owner": {names_by_self.get(k, str(k)): v
                                    for k, v in by_owner.items()},
               "target_hp": None if tg is None else tg.health,
               "attacker_mission": None if atk is None else
               ra2yr.Mission.Name(atk.current_mission),
               "attacker_xyz": None if atk is None else
               [atk.coordinates.x, atk.coordinates.y]}
        series.append(rec)
        if hh2.defeated or hh2.is_loser or hh2.is_game_over or st.stage != 2:
            end = rec
            break
        if dmg_wall is not None and (time.monotonic() - t0) > 3:
            # keep a little series after first damage, then finish
            if len([s for s in series if s["target_hp"] is not None
                    and s["target_hp"] < hp0]) >= 3:
                end = rec
                break
        await asyncio.sleep(1.0)
    step("E_watch_result", hp_before=hp0, hp_min=hp_min,
         damage_seen=dmg_wall is not None, damage_wall=dmg_wall,
         series_len=len(series), end=end)
    report["damage_series"] = series
    report["verdict"]["attack_damage"] = {
        "passed": bool(err is None and dmg_wall is not None),
        "ack_ms": ms, "damage_seen": dmg_wall is not None,
        "hp_before": hp0, "hp_min": hp_min, "distance_leptons": dist0,
        "damage_wall": dmg_wall}
    if end is not None and (end.get("human_defeated") or end.get("stage") != "STAGE_INGAME"):
        report["defeat_cause"] = {
            "wall": end["wall"], "stage": end["stage"],
            "human_defeated": end["human_defeated"],
            "human_is_loser": end.get("human_is_loser"),
            "objects_by_owner_at_end": end["objects_by_owner"],
            "last_series": series[-4:],
        }
        report["verdict"]["defeat_observed"] = {"observed": True,
                                                "human_objects_left":
                                                end["objects_by_owner"]}
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--build-timeout", type=float, default=180.0)
    ap.add_argument("--watch-timeout", type=float, default=150.0)
    ap.add_argument("-o", "--output", type=pathlib.Path,
                    default=ROOT / "reports" / "dsh-attack-watch.json")
    opts = ap.parse_args()
    try:
        asyncio.run(main(opts))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        report["fatal_error"] = f"{type(e).__name__}: {e}"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    opts.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print("verdict:", json.dumps(report["verdict"], ensure_ascii=False))
    print("written:", opts.output)
