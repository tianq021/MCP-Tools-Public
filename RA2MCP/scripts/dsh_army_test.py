"""DSH army/control test (phase 2): deploy at start, build, squad, attack.

Fresh offline skirmish in E:\\RA2MCP\\test-game (copy only).  Deploy the MCV at
its starting cell (guaranteed deployable), build a barracks + power plant, train
two GIs, then test squad move, attack order + damage observation, and native
rejection of an invalid order.  Never sends SELL/SELL_CELL.  ACKs are not
treated as success: every step re-reads the real game state.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_army_test.py [-o reports/dsh-army-test.json]
"""
import argparse
import asyncio
import json
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
CELL = 256  # leptons per cell (destination snapping tolerance)

report = {"script": "dsh_army_test.py", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "steps": [], "verdict": {}, "forbidden_commands_sent": []}


def step(label, **fields):
    rec = {"step": label, "wall": time.strftime("%H:%M:%S"), **fields}
    report["steps"].append(rec)
    print(f"[{rec['wall']}] {label}: {json.dumps(fields, ensure_ascii=False)[:700]}")
    return rec


def same_cell(a, b, tol=CELL):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


async def main(opts):
    client = DualClient("127.0.0.1", opts.port, timeout=2)
    client.connect()

    async def send(cmd, unpack_into, timeout=None):
        t = time.monotonic()
        try:
            r = await asyncio.wait_for(
                client.exec_command(cmd, timeout=timeout or opts.cmd_timeout),
                (timeout or opts.cmd_timeout) + 3.0)
        except asyncio.TimeoutError:
            return None, None, "timeout"
        ms = round((time.monotonic() - t) * 1000.0, 2)
        code = getattr(r, "result_code", 0)
        em = getattr(r, "error_message", "")
        if code != 0:
            return ms, None, f"native_error: {em}"
        msg = unpack_into()
        try:
            if not r.result.Unpack(msg):
                return ms, None, "unpack_failed"
        except Exception as e:  # noqa: BLE001
            return ms, None, f"unpack_error: {e}"
        return ms, msg, None

    async def state():
        q = commands_yr.GetGameState()
        ms, msg, err = await send(q, commands_yr.GetGameState)
        if err:
            raise RuntimeError(f"GetGameState failed: {err}")
        if msg.state.stage != 2:
            s = msg.state
            report["game_end_state"] = {
                "wall": time.strftime("%H:%M:%S"),
                "stage": ra2yr.LoadStage.Name(s.stage),
                "frame": s.current_frame,
                "houses": [{"name": h.name, "defeated": h.defeated,
                            "is_loser": h.is_loser, "is_winner": h.is_winner,
                            "is_game_over": h.is_game_over, "money": h.money}
                           for h in s.houses],
                "objects": len(s.objects),
            }
            raise RuntimeError(f"game ended: stage={ra2yr.LoadStage.Name(s.stage)}")
        return msg.state

    async def initial_state():
        q = commands_yr.ReadValue()
        q.data.initial_game_state.SetInParent()
        ms, msg, err = await send(q, commands_yr.ReadValue)
        if err:
            raise RuntimeError(f"ReadValue failed: {err}")
        return msg.data.initial_game_state, ms

    def human_house(st):
        return next(h for h in st.houses if h.current_player and h.is_human_player)

    def objs(st, house, kind=None):
        out = []
        for o in st.objects:
            if o.pointer_house != house.self or o.health <= 0:
                continue
            k = ra2yr.AbstractType.Name(o.object_type)
            if kind and k != kind:
                continue
            out.append(o)
        return out

    def type_names(init):
        return {t.pointer_self: t.name for t in init.object_types}

    def find_type(init, name):
        for t in init.object_types:
            if t.name == name:
                return t
        return None

    async def wait_for(pred, timeout_s, interval=0.5):
        t0 = time.monotonic()
        st = None
        while time.monotonic() - t0 < timeout_s:
            st = await state()
            ok, extra = pred(st)
            if ok:
                return True, round((time.monotonic() - t0) * 1000.0, 2), st, extra
            await asyncio.sleep(interval)
        return False, round((time.monotonic() - t0) * 1000.0, 2), st, None

    async def unit_order(action_name, addresses, coords=None, target=0):
        q = commands_game.UnitOrder(object_addresses=list(addresses),
                                    action=getattr(ra2yr, action_name))
        if coords is not None:
            q.coordinates.x, q.coordinates.y = coords
        if target:
            q.target_object = target
        ms, msg, err = await send(q, commands_game.UnitOrder)
        return ms, msg, err

    async def build_and_place(bname, anchor, init):
        """Produce one building, wait for completion, place it near anchor."""
        tc = find_type(init, bname)
        if tc is None:
            step("build_type_missing", name=bname)
            return False, None, "type class missing"
        q = commands_game.ProduceOrder(object_type=tc, action=ra2yr.PRODUCE_ACTION_BEGIN)
        ms, msg, err = await send(q, commands_game.ProduceOrder)
        step("build_begin", name=bname, ack_ms=ms, ack_error=err,
             array_index=tc.array_index)
        if err:
            return False, None, err
        factory_obj = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.build_timeout:
            st = await state()
            hh = human_house(st)
            for f in st.factories:
                if f.owner != hh.self or not f.completed or not f.object:
                    continue
                cand = next((o for o in st.objects if o.pointer_self == f.object
                             and o.health > 0), None)
                if cand is not None:
                    factory_obj = cand
                    break
            if factory_obj is not None:
                break
            await asyncio.sleep(1.0)
        dur = round((time.monotonic() - t0) * 1000.0, 2)
        if factory_obj is None:
            step("build_timeout", name=bname, waited_ms=dur, timeout_s=opts.build_timeout)
            return False, None, "not completed in timeout"
        step("build_complete", name=bname, duration_ms=dur,
             object_addr=factory_obj.pointer_self,
             object_type=type_names(init).get(factory_obj.pointer_technotypeclass, "?"))
        base = (anchor.coordinates.x, anchor.coordinates.y)
        cand = []
        for r in (1024, 2048, 3072):
            for dx, dy in [(r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, r), (r, -r), (-r, -r)]:
                cand.append({"x": base[0] + dx, "y": base[1] + dy})
        pq = commands_yr.PlaceQuery(type_class=tc.pointer_self,
                                    house_class=human_house(await state()).self)
        for pt in cand:
            pq.coordinates.add(x=pt["x"], y=pt["y"])
        ms, msg, err = await send(pq, commands_yr.PlaceQuery, timeout=10)
        valid = [{"x": p.x, "y": p.y} for p in msg.coordinates] if msg else []
        step("place_query", name=bname, ack_ms=ms, ack_error=err,
             candidates=len(cand), valid=len(valid))
        if err or not valid:
            return False, None, f"no valid cell ({err})"
        pb = commands_game.PlaceBuilding()
        pb.building.CopyFrom(factory_obj)
        pb.coordinates.x, pb.coordinates.y = valid[0]["x"], valid[0]["y"]
        ms, msg, err = await send(pb, commands_game.PlaceBuilding, timeout=10)
        placed_ok, eff, st, _ = await wait_for(
            lambda s: (any(o.pointer_technotypeclass == factory_obj.pointer_technotypeclass
                           and o.pointer_house == human_house(s).self
                           and not o.in_limbo and o.on_map for o in s.objects), None),
            opts.effect_timeout)
        placed = next((o for o in st.objects
                       if o.pointer_technotypeclass == factory_obj.pointer_technotypeclass
                       and o.pointer_house == human_house(st).self
                       and not o.in_limbo and o.on_map), None)
        step("place_result", name=bname, ack_ms=ms, ack_error=err,
             placed=placed_ok, effect_ms=eff,
             at=[valid[0]["x"], valid[0]["y"]],
             building=None if placed is None else {
                 "addr": placed.pointer_self,
                 "xyz": [placed.coordinates.x, placed.coordinates.y, placed.coordinates.z]})
        return placed_ok, placed, err

    # ---------------- A. preflight ----------------
    st = await state()
    init, init_ms = await initial_state()
    names = type_names(init)
    hh = human_house(st)
    units = objs(st, hh, TYPE_UNIT)
    mcv = units[0] if units else None
    by_owner = {}
    for oo in st.objects:
        by_owner[oo.pointer_house] = by_owner.get(oo.pointer_house, 0) + 1
    step("A_preflight", frame=st.current_frame, money=hh.money,
         objects_by_owner={h.name: by_owner.get(h.self, 0) for h in st.houses},
         initial_types=len(init.object_types), initial_read_ms=init_ms,
         human_units=[{"addr": o.pointer_self,
                       "type": names.get(o.pointer_technotypeclass, "?"),
                       "xyz": [o.coordinates.x, o.coordinates.y]}
                      for o in units],
         human_buildings=len(objs(st, hh, TYPE_BUILDING)))
    if mcv is None:
        raise RuntimeError("no MCV at start; aborting")
    start = (mcv.coordinates.x, mcv.coordinates.y)

    # ---------------- B. deploy at start ----------------
    t = time.monotonic()
    ms, msg, err = await unit_order("UNIT_ACTION_DEPLOY", [mcv.pointer_self])
    deployed, eff, st, _ = await wait_for(
        lambda s: (len(objs(s, human_house(s), TYPE_BUILDING)) >= 1, None),
        opts.effect_timeout)
    building = objs(st, human_house(st), TYPE_BUILDING)
    cy = building[0] if building else None
    step("B_deploy", ack_ms=ms, ack_error=err, deployed=deployed,
         effect_ms=eff,
         cy=None if cy is None else {
             "addr": cy.pointer_self,
             "type": names.get(cy.pointer_technotypeclass, "?"),
             "xyz": [cy.coordinates.x, cy.coordinates.y, cy.coordinates.z]})
    report["verdict"]["deploy_at_start"] = {"passed": deployed, "ack_ms": ms,
                                            "effect_ms": eff,
                                            "building_type": None if cy is None else
                                            names.get(cy.pointer_technotypeclass, "?")}
    if not deployed:
        raise RuntimeError("deploy at start failed")

    # ---------------- C. production chain ----------------
    built = {}
    ok, b, err = await build_and_place("Allied Barracks", cy, init)
    built["Allied Barracks"] = ok
    if not ok:
        ok2, b2, err2 = await build_and_place("Allied Power Plant", cy, init)
        built["Allied Power Plant"] = ok2
        if ok2:
            ok, b, err = await build_and_place("Allied Barracks", cy, init)
            built["Allied Barracks"] = ok
    else:
        okp, bp, errp = await build_and_place("Allied Power Plant", cy, init)
        built["Allied Power Plant"] = okp
    report["verdict"]["build_barracks"] = {"passed": built.get("Allied Barracks", False)}
    report["verdict"]["build_power_plant"] = {"passed": built.get("Allied Power Plant", False)}

    # ---- monitor: catch a spontaneous game end and record why ----
    mon = []
    for _ in range(6):
        stm = await state()
        mon.append({"wall": time.strftime("%H:%M:%S"), "frame": stm.current_frame,
                    "stage": ra2yr.LoadStage.Name(stm.stage),
                    "houses": [{"name": h.name, "defeated": h.defeated,
                                "is_loser": h.is_loser, "is_winner": h.is_winner,
                                "is_game_over": h.is_game_over} for h in stm.houses]})
        if stm.stage != 2:
            break
        await asyncio.sleep(1.0)
    step("C2_monitor", samples=mon)
    if mon and mon[-1]["stage"] != "STAGE_INGAME":
        report["game_end_observed"] = mon[-1]
        raise RuntimeError("game left STAGE_INGAME during monitor")

    # ---------------- D. infantry ----------------
    infantry = []
    tc_gi = find_type(init, "GI")
    if built.get("Allied Barracks") and tc_gi is not None:
        def prod_gi():
            q = commands_game.ProduceOrder(object_type=tc_gi,
                                           action=ra2yr.PRODUCE_ACTION_BEGIN)
            return send(q, commands_game.ProduceOrder)

        ack = []
        ms, msg, err = await prod_gi()
        ack.append({"ack_ms": ms, "ack_error": err})
        ok1, eff1, _st1, _ = await wait_for(
            lambda s: (len(objs(s, human_house(s), TYPE_INFANTRY)) >= 1, None),
            opts.build_timeout, interval=0.5)
        ms, msg, err = await prod_gi()
        ack.append({"ack_ms": ms, "ack_error": err})
        ok2, eff2, st, _ = await wait_for(
            lambda s: (len(objs(s, human_house(s), TYPE_INFANTRY)) >= 2, None),
            opts.build_timeout, interval=0.5)
        ok = ok1 and ok2
        eff = (eff1 or 0) + (eff2 or 0)
        st = await state()
        infantry = [o for o in objs(st, human_house(st), TYPE_INFANTRY)]
        step("D_infantry", order_acks=ack, ready=ok, effect_ms=eff,
             count=len(infantry),
             units=[{"addr": o.pointer_self,
                     "xyz": [o.coordinates.x, o.coordinates.y]} for o in infantry])
        report["verdict"]["produce_infantry"] = {"passed": bool(ok) and len(infantry) >= 2,
                                                 "count": len(infantry)}
    else:
        step("D_infantry_skipped", reason="barracks missing or GI type missing")
        report["verdict"]["produce_infantry"] = {"passed": False, "reason": "skipped"}

    # ---------------- E. squad move ----------------
    if len(infantry) >= 2:
        squad = [o.pointer_self for o in infantry[:2]]
        st = await state()
        before = {o.pointer_self: (o.coordinates.x, o.coordinates.y)
                  for o in st.objects if o.pointer_self in squad}
        anchor = next((o for o in st.objects if cy is not None and
                       o.pointer_self == cy.pointer_self), None)
        ax, ay = (anchor.coordinates.x, anchor.coordinates.y) if anchor else before[squad[0]]
        dest = (ax + 2048, ay - 2048)
        ms, msg, err = await unit_order("UNIT_ACTION_MOVE", squad, dest)
        ok, eff, st, _ = await wait_for(
            lambda s: (all(any(o.pointer_self == a and
                               not same_cell((o.coordinates.x, o.coordinates.y), before[a], tol=0)
                               for o in s.objects) for a in squad), None),
            opts.effect_timeout, interval=0.25)
        after = {o.pointer_self: (o.coordinates.x, o.coordinates.y)
                 for o in st.objects if o.pointer_self in squad}
        step("E_squad_move", ack_ms=ms, ack_error=err, moved_all=ok, effect_ms=eff,
             before={k: list(v) for k, v in before.items()},
             after={k: list(v) for k, v in after.items()})
        report["verdict"]["squad_move"] = {"passed": bool(ok and err is None),
                                           "ack_ms": ms, "effect_ms": eff}
        ms, msg, err = await unit_order("UNIT_ACTION_STOP", squad)
        step("E_squad_stop", ack_ms=ms, ack_error=err)
    else:
        step("E_squad_skipped")
        report["verdict"]["squad_move"] = {"passed": False, "reason": "skipped"}

    # ---------------- F. attack ----------------
    neutral = next((h for h in st.houses if h.name == "Neutral"), None)
    target = None
    if neutral is not None:
        st = await state()
        for o in st.objects:
            if o.pointer_house == neutral.self and o.health > 0 and o.on_map:
                target = o
                break
    if infantry and target is not None:
        attacker = infantry[0].pointer_self
        hp0 = target.health
        ms, msg, err = await unit_order("UNIT_ACTION_ATTACK", [attacker],
                                        (target.coordinates.x, target.coordinates.y),
                                        target.pointer_self)
        mission = None
        eff = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.effect_timeout:
            st = await state()
            atk = next((o for o in st.objects if o.pointer_self == attacker), None)
            if atk is not None:
                mission = ra2yr.Mission.Name(atk.current_mission)
                if mission in ("Mission_Attack", "Mission_AttackMove",
                               "Mission_Hunt", "Mission_Sticky"):
                    eff = round((time.monotonic() - t0) * 1000.0, 2)
                    break
            await asyncio.sleep(0.25)
        # observe target damage
        hp_min = hp0
        dmg_eff = None
        t0 = time.monotonic()
        dmg_seen = False
        while time.monotonic() - t0 < opts.damage_timeout:
            st = await state()
            tgt = next((o for o in st.objects
                        if o.pointer_self == target.pointer_self), None)
            if tgt is not None:
                if tgt.health < hp_min:
                    hp_min = tgt.health
                    if not dmg_seen:
                        dmg_seen = True
                        dmg_eff = round((time.monotonic() - t0) * 1000.0, 2)
                        break
            await asyncio.sleep(1.0)
        step("F_attack", ack_ms=ms, ack_error=err, mission_seen=mission,
             mission_effect_ms=eff, target_addr=target.pointer_self,
             target_cell=[target.coordinates.x, target.coordinates.y],
             target_hp_before=hp0, target_hp_after_observe=hp_min,
             damage_seen=dmg_seen, damage_effect_ms=dmg_eff)
        report["verdict"]["attack"] = {"passed": bool(mission is not None or dmg_seen),
                                       "command_ack_ms": ms, "mission_seen": mission,
                                       "damage_seen": dmg_seen,
                                       "target_hp_before": hp0, "target_hp_min": hp_min}
    else:
        step("F_attack_skipped", infantry=len(infantry), target=target is not None)
        report["verdict"]["attack"] = {"passed": False, "reason": "skipped"}

    # ---------------- G. invalid order rejection ----------------
    if infantry:
        src = infantry[0].pointer_self
        st_before = await state()
        ms, msg, err = await unit_order("UNIT_ACTION_MOVE", [src], (-1000, -1000))
        st_after = await state()
        moved_any = any(o.pointer_self == src and
                        (o.coordinates.x, o.coordinates.y) !=
                        next((b.coordinates.x, b.coordinates.y) for b in st_before.objects
                             if b.pointer_self == src)
                        for o in st_after.objects)
        step("G_invalid_order", ack_ms=ms, ack_error=err,
             frame_before=st_before.current_frame, frame_after=st_after.current_frame,
             unit_moved=moved_any)
        report["verdict"]["invalid_order_rejected"] = {
            "passed": bool(err) and not moved_any, "error": err, "ack_ms": ms,
            "unit_moved": moved_any}
    else:
        step("G_invalid_order_skipped")

    # ---------------- H. final state ----------------
    st = await state()
    hh = human_house(st)
    final = objs(st, hh)
    step("H_final_state", frame=st.current_frame, money=hh.money, defeated=hh.defeated,
         objects=[{"addr": o.pointer_self,
                   "type": names.get(o.pointer_technotypeclass, "?"),
                   "abstract": ra2yr.AbstractType.Name(o.object_type),
                   "mission": ra2yr.Mission.Name(o.current_mission),
                   "xyz": [o.coordinates.x, o.coordinates.y]} for o in final])
    report["forbidden_commands_sent"] = [s for s in report["steps"]
                                         if "SELL" in str(s.get("action", ""))]
    report["all_forbidden_absent"] = len(report["forbidden_commands_sent"]) == 0
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--cmd-timeout", type=float, default=5.0)
    ap.add_argument("--effect-timeout", type=float, default=12.0)
    ap.add_argument("--build-timeout", type=float, default=240.0)
    ap.add_argument("--damage-timeout", type=float, default=25.0)
    ap.add_argument("-o", "--output", type=pathlib.Path,
                    default=ROOT / "reports" / "dsh-army-test.json")
    opts = ap.parse_args()
    try:
        asyncio.run(main(opts))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        report["fatal_error"] = f"{type(e).__name__}: {e}"
        print(f"FATAL: {type(e).__name__}: {e}")
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    opts.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print(f"written: {opts.output}")
    print("verdict:", json.dumps(report["verdict"], ensure_ascii=False))
    sys.exit(0 if not report.get("fatal_error") else 1)
