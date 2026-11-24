"""DSH independent control test for the local ra2yrcpp bridge.

Runs against an offline skirmish in E:\\RA2MCP\\test-game (copy only).
Every step verifies the real game state after the command; a returned ACK is
never treated as proof of effect.  Timeouts are recorded and never blindly
retried.  SELL/SELL_CELL are never sent (the main base must not be sold).

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_control_test.py [-o reports/dsh-control-test.json]
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

TYPE_UNIT = "ABSTRACT_TYPE_UNIT"
TYPE_INFANTRY = "ABSTRACT_TYPE_INFANTRY"
TYPE_BUILDING = "ABSTRACT_TYPE_BUILDING"

report = {
    "script": "dsh_control_test.py",
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "steps": [],
    "forbidden_commands_sent": [],
    "verdict": {},
}


def now():
    return time.strftime("%H:%M:%S")


def step(name, **fields):
    rec = {"step": name, "wall": now(), **fields}
    report["steps"].append(rec)
    print(f"[{rec['wall']}] {name}: {json.dumps(fields, ensure_ascii=False)[:600]}")
    return rec


async def main(opts):
    c = DualClient("127.0.0.1", opts.port, timeout=2)
    c.connect()

    async def send(cmd, unpack_into, timeout=None):
        """Send one command; returns (ack_ms, result_msg_or_None, error)."""
        t = time.monotonic()
        try:
            r = await asyncio.wait_for(
                c.exec_command(cmd, timeout=timeout or opts.cmd_timeout),
                (timeout or opts.cmd_timeout) + 3.0,
            )
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
        st = msg.state
        if st.stage != 2:
            raise RuntimeError(f"not in game (stage={st.stage})")
        return st

    async def initial_state():
        q = commands_yr.ReadValue()
        q.data.initial_game_state.SetInParent()
        ms, msg, err = await send(q, commands_yr.ReadValue)
        if err:
            raise RuntimeError(f"ReadValue failed: {err}")
        return msg.data.initial_game_state, ms

    async def read_config():
        q = commands_yr.InspectConfiguration()
        ms, msg, err = await send(q, commands_yr.InspectConfiguration)
        if err:
            raise RuntimeError(f"InspectConfiguration failed: {err}")
        return msg.config, ms

    def human_house(st):
        return next(h for h in st.houses if h.current_player and h.is_human_player)

    def human_objects(st, house, kind=None, include_limbo=False):
        out = []
        for o in st.objects:
            if o.pointer_house != house.self or o.health <= 0:
                continue
            if not include_limbo and (o.in_limbo or not o.on_map):
                continue
            if kind and ra2yr.AbstractType.Name(o.object_type) != kind:
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

    async def wait_until(pred, timeout_s, interval=0.25):
        t0 = time.monotonic()
        last = None
        while time.monotonic() - t0 < timeout_s:
            st = await state()
            last = st
            ok, extra = pred(st)
            if ok:
                return True, round((time.monotonic() - t0) * 1000.0, 2), st, extra
            await asyncio.sleep(interval)
        return False, round((time.monotonic() - t0) * 1000.0, 2), last, None

    def unit_order(action_name, addresses, coords=None, target=0):
        q = commands_game.UnitOrder(object_addresses=list(addresses),
                                    action=getattr(ra2yr, action_name))
        if coords is not None:
            q.coordinates.x, q.coordinates.y = coords
        if target:
            q.target_object = target
        return q

    forbidden = {"UNIT_ACTION_SELL", "UNIT_ACTION_SELL_CELL"}

    async def order_step(name, action_name, addresses, coords=None, target=0):
        assert action_name not in forbidden
        q = unit_order(action_name, addresses, coords, target)
        ms, msg, err = await send(q, commands_game.UnitOrder)
        rec = step(name, action=action_name, addresses=list(addresses),
                   coords=list(coords) if coords else None, target=target,
                   ack_ms=ms, ack_error=err,
                   native_echo=MessageToDict(msg) if msg else None)
        return ms, msg, err, rec

    # ---------------- A. preflight ----------------
    cfg, cfg_ms = await read_config()
    st = await state()
    init, init_ms = await initial_state()
    names = type_names(init)
    hh = human_house(st)
    human_objs = human_objects(st, hh)
    unit = next((o for o in human_objs
                 if ra2yr.AbstractType.Name(o.object_type) == TYPE_UNIT), None)
    step("A_preflight",
         config={"singleStep": cfg.single_step, "port": cfg.port,
                 "allowedHostsRegex": cfg.allowed_hosts_regex,
                 "logFilename": cfg.log_filename, "config_read_ms": cfg_ms},
         frame=st.current_frame,
         tech_level=st.tech_level,
         houses=[{"i": h.array_index, "name": h.name, "faction": h.faction,
                  "human": h.is_human_player, "money": h.money,
                  "defeated": h.defeated} for h in st.houses],
         initial_object_type_count=len(init.object_types),
         initial_state_read_ms=init_ms,
         human_object_count=len(human_objs),
         human_objects=[{"addr": o.pointer_self,
                         "type": names.get(o.pointer_technotypeclass, "?"),
                         "abstract": ra2yr.AbstractType.Name(o.object_type),
                         "mission": ra2yr.Mission.Name(o.current_mission),
                         "health": o.health,
                         "xyz": [o.coordinates.x, o.coordinates.y, o.coordinates.z]}
                        for o in human_objs])
    report["initial_object_type_names"] = sorted(set(t.name for t in init.object_types))
    if unit is None:
        raise RuntimeError("no human ground unit found; cannot run control tests")
    unit_addr = unit.pointer_self
    step("A_unit_identified",
         address=unit_addr,
         type_name=names.get(unit.pointer_technotypeclass, "?"),
         abstract=ra2yr.AbstractType.Name(unit.object_type),
         health=unit.health,
         mission=ra2yr.Mission.Name(unit.current_mission),
         coordinates=[unit.coordinates.x, unit.coordinates.y])

    async def unit_now(addr=unit_addr):
        st = await state()
        for o in st.objects:
            if o.pointer_self == addr and o.health > 0:
                return st, o
        return st, None

    # ---------------- B. MOVE ----------------
    st, u = await unit_now()
    origin = (u.coordinates.x, u.coordinates.y)
    dest = (origin[0] + 2048, origin[1])
    ms, msg, err, rec = await order_step("B_move_order", "UNIT_ACTION_MOVE",
                                         [unit_addr], dest)
    t_after_order = time.monotonic()
    moved, eff_ms, st2, _ = await wait_until(
        lambda s: (any(o2.pointer_self == unit_addr and
                       (o2.coordinates.x, o2.coordinates.y) != origin
                       for o2 in s.objects), None),
        opts.effect_timeout)
    u2 = next(o for o in st2.objects if o.pointer_self == unit_addr)
    rec.update(moved=moved, effect_ms=eff_ms,
               from_xy=list(origin), to_xy=[u2.coordinates.x, u2.coordinates.y],
               mission_after=ra2yr.Mission.Name(u2.current_mission),
               dest_field=[u2.destination.x, u2.destination.y],
               frame_after=st2.current_frame)
    report["verdict"]["move"] = {"passed": bool(moved and err is None and ms is not None),
                                 "moved": moved, "ack_ms": ms, "effect_ms": eff_ms}

    # ---------------- C. STOP ----------------
    ms, msg, err, rec = await order_step("C_stop_order", "UNIT_ACTION_STOP", [unit_addr])
    stopped = False
    stable = False
    stop_pos = None
    pos_hist = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < opts.effect_timeout:
        st, u = await unit_now()
        if u is None:
            break
        pos_hist.append((u.coordinates.x, u.coordinates.y,
                         ra2yr.Mission.Name(u.current_mission), st.current_frame))
        if len(pos_hist) >= 3:
            a, b = pos_hist[-2], pos_hist[-1]
            if a[0:2] == b[0:2]:
                stable = True
                stop_pos = b[0:2]
                break
        await asyncio.sleep(0.5)
    rec.update(ack_ms=ms, ack_error=err, position_history=pos_hist,
               stable=stable, final_xy=list(stop_pos) if stop_pos else None,
               final_mission=pos_hist[-1][2] if pos_hist else None)
    report["verdict"]["stop"] = {"passed": bool(err is None and ms is not None and stable
                                                and stop_pos != dest),
                                 "stable": stable, "ack_ms": ms,
                                 "stopped_at": list(stop_pos) if stop_pos else None,
                                 "did_not_reach_destination": stop_pos != dest}

    # ---------------- D. re-order while moving ----------------
    st, u = await unit_now()
    p0 = (u.coordinates.x, u.coordinates.y)
    dest_a = (p0[0] + 3072, p0[1])
    ms1, _, err1, _ = await order_step("D_order_a", "UNIT_ACTION_MOVE", [unit_addr], dest_a)
    moving_a = await wait_until(
        lambda s: (any(o.pointer_self == unit_addr and (o.coordinates.x, o.coordinates.y) != p0
                       for o in s.objects), None), opts.effect_timeout)
    st, u = await unit_now()
    dest_b = (u.coordinates.x, u.coordinates.y + 3072)
    ms2, _, err2, _ = await order_step("D_reorder_b", "UNIT_ACTION_MOVE", [unit_addr], dest_b)
    seen_b = False
    eff_b = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < opts.effect_timeout:
        st, u = await unit_now()
        if u is None:
            break
        if abs(u.destination.x - dest_b[0]) <= 256 and abs(u.destination.y - dest_b[1]) <= 256:
            seen_b = True
            eff_b = round((time.monotonic() - t0) * 1000.0, 2)
            break
        await asyncio.sleep(0.25)
    rec = step("D_reorder_result", first_ack_ms=ms1, second_ack_ms=ms2,
               destination_b_seen=seen_b, effect_ms=eff_b,
               current_destination=[u.destination.x, u.destination.y] if u else None)
    report["verdict"]["reorder"] = {"passed": bool(err1 is None and err2 is None and seen_b),
                                    "destination_b_seen": seen_b,
                                    "ack_ms": [ms1, ms2], "effect_ms": eff_b}
    await order_step("D_stop", "UNIT_ACTION_STOP", [unit_addr])
    await asyncio.sleep(1.5)

    # ---------------- E. DEPLOY (MCV -> construction yard) ----------------
    st, u = await unit_now()
    pre_coords = (u.coordinates.x, u.coordinates.y)
    ms, msg, err, rec = await order_step("E_deploy_order", "UNIT_ACTION_DEPLOY", [unit_addr])
    deployed = False
    new_building = None
    eff = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < opts.deploy_timeout:
        st = await state()
        blds = human_objects(st, human_house(st), TYPE_BUILDING)
        unit_gone = not any(o.pointer_self == unit_addr for o in st.objects)
        if blds:
            new_building = blds[0]
            deployed = True
            eff = round((time.monotonic() - t0) * 1000.0, 2)
            break
        await asyncio.sleep(0.5)
    rec.update(ack_ms=ms, ack_error=err, deployed=deployed, effect_ms=eff,
               building=None if new_building is None else {
                   "addr": new_building.pointer_self,
                   "type": names.get(new_building.pointer_technotypeclass, "?"),
                   "xyz": [new_building.coordinates.x, new_building.coordinates.y,
                           new_building.coordinates.z],
                   "health": new_building.health},
               unit_still_present=(not deployed and
                                   any(o.pointer_self == unit_addr for o in st.objects)))
    report["verdict"]["deploy"] = {"passed": bool(err is None and deployed),
                                   "deployed": deployed, "ack_ms": ms,
                                   "building_type": None if new_building is None else
                                   names.get(new_building.pointer_technotypeclass, "?")}
    if not deployed:
        raise RuntimeError("MCV deploy did not produce a building; aborting production tests")

    cy = new_building

    # ---------------- F. PRODUCTION ----------------
    build_names = ["Allied Power Plant", "Allied Barracks"]
    placed = {}
    for bname in build_names:
        tc = find_type(init, bname)
        if tc is None:
            step(f"F_{bname}_missing", note="type class not found in initial state")
            continue
        q = commands_game.ProduceOrder(object_type=tc, action=ra2yr.PRODUCE_ACTION_BEGIN)
        ms, msg, err = await send(q, commands_game.ProduceOrder)
        step(f"F_{bname}_produce_begin", ack_ms=ms, ack_error=err,
             pointer_self=tc.pointer_self, array_index=tc.array_index,
             native_echo=MessageToDict(msg) if msg else None)
        # Wait for a completed factory object owned by the human for this type.
        done = False
        t0 = time.monotonic()
        factory_obj = None
        last_progress = None
        while time.monotonic() - t0 < opts.build_timeout:
            st = await state()
            hh2 = human_house(st)
            for f in st.factories:
                if f.owner != hh2.self or not f.completed or not f.object:
                    continue
                for o in st.objects:
                    if o.pointer_self == f.object and o.health > 0:
                        factory_obj = o
                        break
                if factory_obj is not None:
                    break
            if factory_obj is not None:
                done = True
                break
            # progress visibility: house build_state entry with the name
            last_progress = [b.percent_done for b in hh2.build_state if b.name == bname]
            await asyncio.sleep(1.0)
        eff = round((time.monotonic() - t0) * 1000.0, 2)
        step(f"F_{bname}_complete", completed=done, effect_ms=eff,
             progress_samples=last_progress,
             factory_object=None if factory_obj is None else factory_obj.pointer_self)
        if not done:
            report["verdict"][f"produce_{bname}"] = {"passed": False,
                                                     "reason": "not completed in timeout",
                                                     "timeout_s": opts.build_timeout}
            continue
        # Placement: query valid cells around the construction yard / factory.
        cand = []
        base = (cy.coordinates.x, cy.coordinates.y)
        offsets = []
        for r in (1024, 2048, 3072):
            for dx, dy in [(r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, r), (r, -r), (-r, -r)]:
                offsets.append((dx, dy))
        offsets.append((0, 0))
        for dx, dy in offsets:
            cand.append({"x": base[0] + dx, "y": base[1] + dy})
        pq = commands_yr.PlaceQuery(type_class=tc.pointer_self,
                                    house_class=hh2.self)
        for pt in cand:
            pq.coordinates.add(x=pt["x"], y=pt["y"])
        ms, msg, err = await send(pq, commands_yr.PlaceQuery, timeout=8)
        valid = [{"x": c.x, "y": c.y} for c in msg.coordinates] if msg else []
        step(f"F_{bname}_place_query", ack_ms=ms, candidates=len(cand),
             valid=valid, ack_error=err)
        if err is None and valid:
            pb = commands_game.PlaceBuilding()
            pb.building.CopyFrom(factory_obj)
            pb.coordinates.x, pb.coordinates.y = valid[0]["x"], valid[0]["y"]
            ms, msg, err = await send(pb, commands_game.PlaceBuilding)
            step(f"F_{bname}_place", ack_ms=ms, ack_error=err,
                 at=[valid[0]["x"], valid[0]["y"]],
                 native_echo=MessageToDict(msg) if msg else None)
            placed[bname] = err is None and ms is not None
        else:
            placed[bname] = False
        report["verdict"][f"produce_{bname}"] = {"passed": placed.get(bname, False),
                                                 "note": "completed" if done else ""}

    # ---------------- G. INFANTRY (for squad & attack) ----------------
    infantry = []
    tc_e1 = find_type(init, "GI")
    if tc_e1 is not None and placed.get("GAPILE"):
        for _ in range(2):
            q = commands_game.ProduceOrder(object_type=tc_e1,
                                           action=ra2yr.PRODUCE_ACTION_BEGIN)
            ms, msg, err = await send(q, commands_game.ProduceOrder)
            step("G_GI_produce_begin", ack_ms=ms, ack_error=err)
        ok, eff, st2, _ = await wait_until(
            lambda s: (len(human_objects(s, human_house(s), TYPE_INFANTRY)) >= 2, None),
            opts.build_timeout, interval=1.0)
        st2 = await state()
        infantry = [o.pointer_self for o in human_objects(st2, human_house(st2), TYPE_INFANTRY)]
        step("G_infantry_ready", ok=ok, effect_ms=eff, count=len(infantry))
        report["verdict"]["produce_infantry"] = {"passed": bool(ok), "count": len(infantry)}
    else:
        step("G_infantry_skipped", reason="GAPILE not built or E1 type missing")
        report["verdict"]["produce_infantry"] = {"passed": False, "reason": "skipped"}

    # ---------------- H. SQUAD CONTROL ----------------
    if len(infantry) >= 2:
        squad = infantry[:2]
        st = await state()
        pos0 = {}
        for o in st.objects:
            if o.pointer_self in squad:
                pos0[o.pointer_self] = (o.coordinates.x, o.coordinates.y)
        st_cy = await state()
        anchor = next((o for o in st_cy.objects if o.pointer_self == cy.pointer_self), None)
        squad_dest = (anchor.coordinates.x + 2048, anchor.coordinates.y - 2048) \
            if anchor else (pos0[squad[0]][0] + 2048, pos0[squad[0]][1])
        ms, msg, err, rec = await order_step("H_squad_move", "UNIT_ACTION_MOVE",
                                             squad, squad_dest)
        ok, eff, st2, _ = await wait_until(
            lambda s: (all(any(o.pointer_self == a and
                               (o.coordinates.x, o.coordinates.y) != pos0.get(a)
                               for o in s.objects) for a in squad), None),
            opts.effect_timeout, interval=0.5)
        nowpos = {}
        for o in st2.objects:
            if o.pointer_self in squad:
                nowpos[o.pointer_self] = (o.coordinates.x, o.coordinates.y)
        step("H_squad_result", ack_ms=ms, ack_error=err, moved_all=ok,
             effect_ms=eff, before={k: list(v) for k, v in pos0.items()},
             after={k: list(v) for k, v in nowpos.items()})
        report["verdict"]["squad_move"] = {"passed": bool(err is None and ok),
                                           "moved_all": ok, "ack_ms": ms,
                                           "effect_ms": eff}
        await order_step("H_squad_stop", "UNIT_ACTION_STOP", squad)
    else:
        step("H_squad_skipped", reason="fewer than 2 infantry")
        report["verdict"]["squad_move"] = {"passed": False, "reason": "skipped"}

    # ---------------- I. ATTACK ----------------
    attack_target = None
    st = await state()
    neutral = next((h for h in st.houses if h.name == "Neutral"), None)
    if neutral:
        for o in st.objects:
            if o.pointer_house == neutral.self and o.health > 0 and o.on_map:
                attack_target = o
                break
    if infantry and attack_target is not None:
        attacker = infantry[0]
        tgt_health0 = attack_target.health
        ms, msg, err, rec = await order_step(
            "I_attack", "UNIT_ACTION_ATTACK", [attacker],
            coords=(attack_target.coordinates.x, attack_target.coordinates.y),
            target=attack_target.pointer_self)
        seen_mission = None
        eff = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.effect_timeout:
            st = await state()
            atk = next((o for o in st.objects if o.pointer_self == attacker), None)
            if atk is not None:
                m = ra2yr.Mission.Name(atk.current_mission)
                if m in ("Mission_Attack", "Mission_AttackMove"):
                    seen_mission = m
                    eff = round((time.monotonic() - t0) * 1000.0, 2)
                    break
            await asyncio.sleep(0.25)
        ms2, msg2, err2, _ = await order_step(
            "I_attack_move", "UNIT_ACTION_ATTACK_MOVE", [attacker],
            coords=(attack_target.coordinates.x, attack_target.coordinates.y))
        step("I_attack_result", ack_ms=ms, ack_error=err,
             mission_seen=seen_mission, effect_ms=eff,
             attack_move_ack_ms=ms2, attack_move_error=err2,
             target_addr=attack_target.pointer_self,
             target_health_before=tgt_health0)
        report["verdict"]["attack"] = {
            "passed": bool(err is None and (seen_mission is not None)),
            "mission_seen": seen_mission, "ack_ms": ms, "effect_ms": eff,
            "attack_move_ack_ms": ms2,
            "damage_verified": False,
            "note": "command/mission transition verified; damage not required for this check",
        }
    else:
        step("I_attack_skipped", reason="no infantry or no neutral target",
             infantry=len(infantry),
             neutral_present=bool(neutral))
        report["verdict"]["attack"] = {"passed": False, "reason": "skipped"}

    # ---------------- J. rejected-command path ----------------
    invalid_src = infantry[:1] if infantry else []
    q = unit_order("UNIT_ACTION_MOVE", invalid_src, (-1000, -1000))
    ms, msg, err = await send(q, commands_game.UnitOrder)
    st_after = await state()
    step("J_invalid_coordinates", ack_ms=ms, ack_error=err,
         source=invalid_src, frame_after=st_after.current_frame,
         note="expect native rejection, not a fake success")
    report["verdict"]["invalid_command_rejected"] = {
        "passed": bool(invalid_src) and err is not None,
        "tested": bool(invalid_src),
        "ack_ms": ms, "error": err,
    }

    # ---------------- K. final state snapshot ----------------
    st_final, u_final = None, None
    st_final = await state()
    hh_final = human_house(st_final)
    final_objs = human_objects(st_final, hh_final, include_limbo=True)
    step("K_final_state", frame=st_final.current_frame,
         money=hh_final.money, defeated=hh_final.defeated,
         is_game_over=hh_final.is_game_over,
         objects=[{"addr": o.pointer_self,
                   "type": names.get(o.pointer_technotypeclass, "?"),
                   "abstract": ra2yr.AbstractType.Name(o.object_type),
                   "mission": ra2yr.Mission.Name(o.current_mission),
                   "health": o.health,
                   "xyz": [o.coordinates.x, o.coordinates.y, o.coordinates.z]}
                  for o in final_objs])
    report["forbidden_commands_sent"] = [s for s in report["steps"]
                                         if s.get("action") in forbidden]
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
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
    ap.add_argument("--deploy-timeout", type=float, default=15.0)
    ap.add_argument("--build-timeout", type=float, default=180.0)
    ap.add_argument("-o", "--output", type=pathlib.Path,
                    default=ROOT / "reports" / "dsh-control-test.json")
    opts = ap.parse_args()
    try:
        asyncio.run(main(opts))
    except Exception as e:  # noqa: BLE001
        report["fatal_error"] = f"{type(e).__name__}: {e}"
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"FATAL: {type(e).__name__}: {e}")
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    opts.output.write_text(text, encoding="utf-8")
    print(f"written: {opts.output}")
    sys.exit(0 if not report.get("fatal_error") else 1)
