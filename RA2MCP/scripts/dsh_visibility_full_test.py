"""DSH full visibility + damage test with the new native visibility fields.

One clean offline skirmish (test-game copy) run:
  A. preflight: verify new Object/Cell fields exist and are meaningful
  B. deploy MCV -> Construction Yard
  C. build + place a barracks
  D. produce 2 GIs
  E. baseline local-visibility table over all objects
  F. move GIs towards the nearest neutral tech building (reveal shroud)
  G. verify the target cell flips shrouded(true->false) / mapped(false->true)
  H. ATTACK the now-visible target and require real HP loss
  I. final state + report

Read-only except DEPLOY / BUILD / PLACE / MOVE / ATTACK orders; never SELL.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_visibility_full_test.py -o reports/dsh-visibility-test.json
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

CELL = 256
TYPE_BUILDING = "ABSTRACT_TYPE_BUILDING"
TYPE_UNIT = "ABSTRACT_TYPE_UNIT"
TYPE_INFANTRY = "ABSTRACT_TYPE_INFANTRY"

report = {"script": "dsh_visibility_full_test.py",
          "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "steps": [], "verdict": {}, "fatal": None}


def step(label, **fields):
    rec = {"step": label, "wall": time.strftime("%H:%M:%S"), **fields}
    report["steps"].append(rec)
    print(f"[{rec['wall']}] {label}: {json.dumps(fields, ensure_ascii=False, default=str)[:900]}")
    return rec


def cell_of(o):
    return (o.coordinates.x // CELL, o.coordinates.y // CELL)


def dist_xyz(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


async def main(opts):
    c = DualClient("127.0.0.1", opts.port, timeout=2)
    c.connect()

    async def send(cmd, into, timeout=5.0):
        t = time.monotonic()
        try:
            r = await asyncio.wait_for(c.exec_command(cmd, timeout=timeout), timeout + 4)
        except asyncio.TimeoutError:
            return None, None, "timeout"
        ms = round((time.monotonic() - t) * 1000.0, 2)
        if getattr(r, "result_code", 0) != 0:
            return ms, None, f"native_error: {getattr(r, 'error_message', '')}"
        msg = into()
        if not r.result.Unpack(msg):
            return ms, None, "unpack_failed"
        return ms, msg, None

    async def state(allow_end=False):
        ms, msg, err = await send(commands_yr.GetGameState(), commands_yr.GetGameState)
        if err:
            raise RuntimeError(f"GetGameState: {err}")
        st = msg.state
        if st.stage != 2 and not allow_end:
            report["game_end_state"] = {
                "wall": time.strftime("%H:%M:%S"),
                "stage": ra2yr.LoadStage.Name(st.stage),
                "frame": st.current_frame,
                "houses": [{"name": h.name, "defeated": h.defeated,
                            "is_loser": h.is_loser, "is_winner": h.is_winner,
                            "money": h.money} for h in st.houses]}
            raise RuntimeError(f"game ended: stage={ra2yr.LoadStage.Name(st.stage)}")
        return st

    async def map_soa():
        q = commands_yr.ReadValue()
        q.data.map_data_soa.SetInParent()
        ms, msg, err = await send(q, commands_yr.ReadValue, timeout=15)
        if err:
            raise RuntimeError(f"ReadValue map_data_soa: {err}")
        return msg.data.map_data_soa

    async def map_full():
        q = commands_yr.ReadValue()
        q.data.map_data.SetInParent()
        ms, msg, err = await send(q, commands_yr.ReadValue, timeout=15)
        if err:
            raise RuntimeError(f"ReadValue map_data: {err}")
        return msg.data.map_data

    async def initial_state():
        q = commands_yr.ReadValue()
        q.data.initial_game_state.SetInParent()
        ms, msg, err = await send(q, commands_yr.ReadValue)
        if err:
            raise RuntimeError(f"ReadValue initial: {err}")
        return msg.data.initial_game_state

    def human(st):
        return next(h for h in st.houses if h.current_player and h.is_human_player)

    def objs(st, house=None, kind=None):
        out = []
        for o in st.objects:
            if o.health <= 0 or o.in_limbo:
                continue
            if house is not None and o.pointer_house != house.self:
                continue
            k = ra2yr.AbstractType.Name(o.object_type)
            if kind and k != kind:
                continue
            out.append(o)
        return out

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

    async def order(action, addresses, coords=None, target=0, timeout=5.0):
        q = commands_game.UnitOrder(object_addresses=list(addresses),
                                    action=getattr(ra2yr, action))
        if coords is not None:
            q.coordinates.x, q.coordinates.y = coords
        if target:
            q.target_object = target
        return await send(q, commands_game.UnitOrder, timeout=timeout)

    async def build_and_place(bname, anchor, init, human_self):
        tc = next((t for t in init.object_types if t.name == bname), None)
        if tc is None:
            return False, None, f"type {bname} missing"
        q = commands_game.ProduceOrder(object_type=tc, action=ra2yr.PRODUCE_ACTION_BEGIN)
        ms, msg, err = await send(q, commands_game.ProduceOrder)
        if err:
            return False, None, err
        factory_obj = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.build_timeout:
            st = await state()
            for f in st.factories:
                if f.owner != human_self or not f.completed or not f.object:
                    continue
                cand = next((o for o in st.objects if o.pointer_self == f.object
                             and o.health > 0), None)
                if cand is not None:
                    factory_obj = cand
                    break
            if factory_obj is not None:
                break
            await asyncio.sleep(1.0)
        if factory_obj is None:
            return False, None, "build timeout"
        base = (anchor.coordinates.x, anchor.coordinates.y)
        cand_pts = []
        for r in (1024, 1536, 2048, 3072):
            for dx, dy in [(r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, r), (r, -r), (-r, -r)]:
                cand_pts.append({"x": base[0] + dx, "y": base[1] + dy})
        pq = commands_yr.PlaceQuery(type_class=tc.pointer_self, house_class=human_self)
        for pt in cand_pts:
            pq.coordinates.add(x=pt["x"], y=pt["y"])
        ms, msg, err = await send(pq, commands_yr.PlaceQuery, timeout=10)
        valid = [{"x": p.x, "y": p.y} for p in msg.coordinates] if msg else []
        if err or not valid:
            return False, None, f"no valid cell ({err})"
        pb = commands_game.PlaceBuilding()
        pb.building.CopyFrom(factory_obj)
        pb.coordinates.x, pb.coordinates.y = valid[0]["x"], valid[0]["y"]
        ms, msg, err = await send(pb, commands_game.PlaceBuilding, timeout=10)
        placed, eff, st, _ = await wait_for(
            lambda s: (len([o for o in s.objects
                            if o.pointer_technotypeclass == factory_obj.pointer_technotypeclass
                            and o.pointer_house == human_self and not o.in_limbo
                            and o.on_map]) > 0, None), opts.effect_timeout)
        return placed, factory_obj, err

    def visibility_table(st, md):
        # md may be a MapData (width/height/cells) or MapDataSoA (map_width/...)
        if hasattr(md, "map_width"):
            w = int(md.map_width)
            mh = int(md.map_height)
            mapped = list(md.mapped)
            shrouded = list(md.shrouded)
            shroud_state = list(md.shroud_state)
        else:
            w = int(md.width)
            mh = int(md.height)
            mapped = [bool(c.mapped) for c in md.cells]
            shrouded = [bool(c.shrouded) for c in md.cells]
            shroud_state = [int(c.shroud_state) for c in md.cells]
        houses = {h.self: h for h in st.houses}
        rows = []
        for o in st.objects:
            if o.health <= 0 or o.in_limbo:
                continue
            house = houses.get(o.pointer_house)
            cx, cy = cell_of(o)
            flat = cy * w + cx if 0 <= cx < w and 0 <= cy < mh else -1
            rows.append({
                "addr": o.pointer_self,
                "owner": house.name if house else "?",
                "is_human_owner": bool(house.is_human_player) if house else None,
                "type_ptr": o.pointer_technotypeclass,
                "abstract": ra2yr.AbstractType.Name(o.object_type),
                "health": o.health,
                "xyz": [o.coordinates.x, o.coordinates.y],
                "cell": [cx, cy],
                "cell_shrouded": o.cell_shrouded,
                "cell_mapped": o.cell_mapped,
                "discovered_by_current_player": o.discovered_by_current_player,
                "is_visible": o.is_visible,
                "cloak_state": o.cloak_state,
                "map_shrouded": shrouded[flat] if flat >= 0 else None,
                "map_mapped": mapped[flat] if flat >= 0 else None,
                "map_shroud_state": shroud_state[flat] if flat >= 0 else None,
            })
        return rows

    try:
        # ---------------- A. preflight ----------------
        st = await state()
        init = await initial_state()
        names = {t.pointer_self: t.name for t in init.object_types}
        hh = human(st)
        md = await map_full()
        mds = await map_soa()
        probe_obj = next((o for o in st.objects if o.health > 0), None)
        field_probe = None
        if probe_obj is not None:
            field_probe = {
                "has_cell_shrouded": hasattr(probe_obj, "cell_shrouded"),
                "has_discovered": hasattr(probe_obj, "discovered_by_current_player"),
                "has_cloak_state": hasattr(probe_obj, "cloak_state"),
                "cell_shrouded_value": bool(probe_obj.cell_shrouded),
                "discovered_value": bool(probe_obj.discovered_by_current_player),
            }
        cell_probe = None
        if len(md.cells) > 0:
            cc = md.cells[len(md.cells) // 2]
            cell_probe = {"has_mapped": hasattr(cc, "mapped"),
                          "has_alt_flags": hasattr(cc, "alt_flags"),
                          "has_shroud_counter": hasattr(cc, "shroud_counter"),
                          "has_shroud_state": hasattr(cc, "shroud_state"),
                          "mapped_value": bool(cc.mapped),
                          "shroud_state_value": int(cc.shroud_state)}
        step("A_preflight", frame=st.current_frame, money=hh.money,
             map={"width": int(md.width), "height": int(md.height),
                  "cells": len(md.cells), "soa_cells": len(mds.mapped)},
             fields=field_probe, cell_fields=cell_probe,
             soa_has_mapped=hasattr(mds, "mapped"),
             initial_types=len(init.object_types))
        report["verdict"]["native_visibility_fields"] = bool(
            field_probe and field_probe["has_cell_shrouded"] and
            field_probe["has_discovered"] and cell_probe and cell_probe["has_mapped"])
        if not report["verdict"]["native_visibility_fields"]:
            raise RuntimeError("native visibility fields missing (old DLL/pb2?)")

        # ---------------- B. deploy MCV (skip if already deployed) --------
        def has_human_building(state_obj, bname):
            h = human(state_obj)
            return any(names.get(o.pointer_technotypeclass) == bname
                       for o in objs(state_obj, h, TYPE_BUILDING))

        mcv = next(iter(objs(st, hh, TYPE_UNIT)), None)
        cy = next((o for o in objs(st, hh, TYPE_BUILDING)
                   if names.get(o.pointer_technotypeclass) == "Allied Construction Yard"),
                  None)
        if cy is None:
            if mcv is None:
                raise RuntimeError("no MCV and no Construction Yard")
            ms, msg, err = await order("UNIT_ACTION_DEPLOY", [mcv.pointer_self])
            b_ok, b_eff, st, _ = await wait_for(
                lambda s: (len(objs(s, human(s), TYPE_BUILDING)) >= 1, None),
                opts.effect_timeout)
            cy = next((o for o in objs(st, human(st), TYPE_BUILDING)
                       if names.get(o.pointer_technotypeclass) == "Allied Construction Yard"),
                      next(iter(objs(st, human(st), TYPE_BUILDING)), None))
            step("B_deploy", ack_ms=ms, ack_error=err, deployed=b_ok, effect_ms=b_eff,
                 cy=None if cy is None else {"addr": cy.pointer_self,
                                             "type": names.get(cy.pointer_technotypeclass, "?"),
                                             "cell": cell_of(cy)})
            report["verdict"]["deploy"] = {"passed": bool(cy is not None), "ack_error": err}
        else:
            step("B_deploy", skipped=True, reason="Construction Yard already present",
                 cy={"addr": cy.pointer_self, "cell": cell_of(cy)})
            report["verdict"]["deploy"] = {"passed": True, "preexisting": True}
        if cy is None:
            raise RuntimeError("deploy failed")

        # ---------------- C. build power plant + barracks (skip if present) --
        # RA2 tech order: the Allied Barracks requires an Allied Power Plant,
        # so build the power plant first and retry the barracks once.
        built = {}
        st = await state()
        if has_human_building(st, "Allied Power Plant"):
            built["Allied Power Plant"] = {"passed": True, "preexisting": True}
            step("C_build_power", skipped=True, reason="already present")
        else:
            ok, bobj, err = await build_and_place("Allied Power Plant", cy, init, human(st).self)
            built["Allied Power Plant"] = {"passed": ok, "error": err}
            step("C_build_power", built=ok, error=err,
                 building=None if bobj is None else {"addr": bobj.pointer_self,
                                                     "cell": cell_of(bobj)})
        st = await state()
        if has_human_building(st, "Allied Barracks"):
            built["Allied Barracks"] = {"passed": True, "preexisting": True}
            step("C_build_barracks", skipped=True, reason="already present")
        else:
            ok2, bobj2, err2 = await build_and_place("Allied Barracks", cy, init, human(st).self)
            built["Allied Barracks"] = {"passed": ok2, "error": err2}
            step("C_build_barracks", built=ok2, error=err2,
                 building=None if bobj2 is None else {"addr": bobj2.pointer_self,
                                                      "cell": cell_of(bobj2)})
        report["verdict"]["build_barracks"] = built
        if not built["Allied Barracks"].get("passed"):
            raise RuntimeError(f"barracks build failed: {built['Allied Barracks'].get('error')}")

        # ---------------- D. produce GIs (one at a time; duplicate ProduceOrder
        # events are rejected natively with "event already exists in DoList") ----
        gi = next((t for t in init.object_types if t.name == "GI"), None)
        if gi is None:
            raise RuntimeError("GI type missing")
        gi_acks = []
        st = await state()
        have = len(objs(st, human(st), TYPE_INFANTRY))
        for n in range(have, 2):
            for attempt in range(4):
                q = commands_game.ProduceOrder(object_type=gi,
                                               action=ra2yr.PRODUCE_ACTION_BEGIN)
                ms, msg, err = await send(q, commands_game.ProduceOrder)
                gi_acks.append({"want": n + 1, "attempt": attempt,
                                "ack_ms": ms, "error": err})
                if err is None:
                    break
                await asyncio.sleep(1.0)
            await wait_for(lambda s: (len(objs(s, human(s), TYPE_INFANTRY)) >= n + 1, None),
                           30.0)
        ok, eff, st, _ = await wait_for(
            lambda s: (len(objs(s, human(s), TYPE_INFANTRY)) >= 2, None), opts.build_timeout)
        gis = objs(st, human(st), TYPE_INFANTRY)
        step("D_produce_gi", produced=ok, effect_ms=eff, count=len(gis), acks=gi_acks,
             units=[{"addr": o.pointer_self, "xyz": [o.coordinates.x, o.coordinates.y]}
                    for o in gis])
        report["verdict"]["produce_gi"] = {"passed": len(gis) >= 2, "count": len(gis),
                                          "acks": gi_acks}
        if len(gis) < 2:
            raise RuntimeError("GI production failed")

        # ---------------- E. baseline visibility table ----------------
        st = await state()
        md = await map_full()
        base_rows = visibility_table(st, md)
        step("E_baseline_visibility", frame=st.current_frame,
             objects=len(base_rows),
             shrouded_objects=sum(1 for r in base_rows if r["cell_shrouded"]),
             discovered_objects=sum(1 for r in base_rows
                                    if r["discovered_by_current_player"]),
             table=[{k: r[k] for k in ("owner", "abstract", "cell", "cell_shrouded",
                                       "cell_mapped", "discovered_by_current_player",
                                       "is_visible", "cloak_state")}
                    for r in base_rows[:30]])
        report["baseline_visibility"] = base_rows
        enemy = [r for r in base_rows if not r["is_human_owner"]]
        report["verdict"]["enemy_objects_hidden_at_start"] = {
            "enemy_total": len(enemy),
            "enemy_cell_shrouded_true": sum(1 for r in enemy if r["cell_shrouded"]),
            "enemy_discovered_true": sum(1 for r in enemy
                                         if r["discovered_by_current_player"]),
            "enemy_cell_mapped_true": sum(1 for r in enemy if r["cell_mapped"]),
        }
        human_rows = [r for r in base_rows if r["is_human_owner"]]
        report["verdict"]["human_objects_visible"] = {
            "human_total": len(human_rows),
            "cell_shrouded_false": sum(1 for r in human_rows if not r["cell_shrouded"]),
        }

        # ---------------- F. move GIs to reveal + attack target ----------------
        st = await state()
        hh = human(st)
        gis = objs(st, hh, TYPE_INFANTRY)
        neutral = next((h for h in st.houses if h.name == "Neutral"), None)
        targets = [o for o in st.objects if neutral and o.pointer_house == neutral.self
                   and o.health > 0 and o.on_map and not o.in_limbo]
        gx, gy = gis[0].coordinates.x, gis[0].coordinates.y
        targets.sort(key=lambda o: dist_xyz((o.coordinates.x, o.coordinates.y), (gx, gy)))
        tgt = targets[0]
        tc_cell = cell_of(tgt)
        txyz = (tgt.coordinates.x, tgt.coordinates.y)
        md0 = await map_soa()
        w = int(md0.map_width)
        tflat = tc_cell[1] * w + tc_cell[0]
        step("F_target", target={"addr": tgt.pointer_self,
                                 "type": names.get(tgt.pointer_technotypeclass, "?"),
                                 "cell": tc_cell, "xyz": list(txyz),
                                 "health": tgt.health,
                                 "cell_shrouded": tgt.cell_shrouded,
                                 "cell_mapped": tgt.cell_mapped,
                                 "discovered": tgt.discovered_by_current_player},
             mapped_before=sum(md0.mapped), shrouded_before=sum(md0.shrouded),
             target_cell_map_shrouded=bool(md0.shrouded[tflat]),
             target_cell_map_mapped=bool(md0.mapped[tflat]))

        # aim for a cell 2 cells short of the target along the GI->target line
        vx, vy = txyz[0] - gx, txyz[1] - gy
        norm = math.hypot(vx, vy) or 1.0
        stand = (int(txyz[0] - vx / norm * 2.0 * CELL),
                 int(txyz[1] - vy / norm * 2.0 * CELL))
        ms, msg, err = await order("UNIT_ACTION_MOVE", [o.pointer_self for o in gis], stand)
        move_rec = {"ack_ms": ms, "ack_error": err, "dest": list(stand),
                    "samples": []}
        report["steps"].append({"step": "F_move", "wall": time.strftime("%H:%M:%S"),
                                **move_rec})

        close, eff, st, _ = await wait_for(
            lambda s: (all(dist_xyz((o.coordinates.x, o.coordinates.y), txyz) <= 3.5 * CELL
                           for o in objs(s, human(s), TYPE_INFANTRY)
                           if o.pointer_self in {g.pointer_self for g in gis}), None),
            opts.move_timeout, interval=1.0)
        md1 = await map_soa()
        st1 = st
        moved_gis = objs(st1, human(st1), TYPE_INFANTRY)
        step("F_move_result", arrived=close, effect_ms=eff,
             gis=[{"addr": o.pointer_self, "cell": cell_of(o)} for o in moved_gis],
             mapped_after=sum(md1.mapped), shrouded_after=sum(md1.shrouded),
             new_mapped_cells=sum(md1.mapped) - sum(md0.mapped),
             newly_revealed_cells=sum(md0.shrouded) - sum(md1.shrouded),
             target_cell_map_shrouded=bool(md1.shrouded[tflat]),
             target_cell_map_mapped=bool(md1.mapped[tflat]))
        report["verdict"]["reveal_via_move"] = {
            "passed": bool(close and (sum(md1.mapped) > sum(md0.mapped)
                                      or sum(md1.shrouded) < sum(md0.shrouded))),
            "new_mapped_cells": sum(md1.mapped) - sum(md0.mapped),
            "newly_unshrouded_cells": sum(md0.shrouded) - sum(md1.shrouded),
            "target_cell_map_shrouded_before": bool(md0.shrouded[tflat]),
            "target_cell_map_shrouded_after": bool(md1.shrouded[tflat]),
            "target_cell_map_mapped_after": bool(md1.mapped[tflat]),
            "gis_arrived": bool(close),
        }

        # watch whether the target object's own visibility flags flip
        tgt_now = next((o for o in st1.objects if o.pointer_self == tgt.pointer_self), None)
        report["verdict"]["target_visibility_flip"] = None if tgt_now is None else {
            "before": {"cell_shrouded": tgt.cell_shrouded, "cell_mapped": tgt.cell_mapped,
                       "discovered": tgt.discovered_by_current_player},
            "after": {"cell_shrouded": tgt_now.cell_shrouded,
                      "cell_mapped": tgt_now.cell_mapped,
                      "discovered": tgt_now.discovered_by_current_player,
                      "is_visible": tgt_now.is_visible},
        }

        # ---------------- G. attack + damage ----------------
        hp0 = tgt.health
        md = await map_full()
        rows = visibility_table(st1, md)
        trow = next((r for r in rows if r["addr"] == tgt.pointer_self), None)
        ms, msg, err = await order("UNIT_ACTION_ATTACK",
                                   [o.pointer_self for o in moved_gis],
                                   (tgt.coordinates.x, tgt.coordinates.y),
                                   tgt.pointer_self, timeout=8.0)
        hp_min = hp0
        hp_timeline = []
        dmg_eff = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.damage_timeout:
            st2 = await state()
            tg = next((o for o in st2.objects if o.pointer_self == tgt.pointer_self), None)
            if tg is None:
                hp_timeline.append({"wall": time.strftime("%H:%M:%S"), "hp": None,
                                    "note": "target gone"})
                break
            hp_timeline.append({"wall": time.strftime("%H:%M:%S"),
                                "frame": st2.current_frame, "hp": tg.health})
            if tg.health < hp_min:
                hp_min = tg.health
                if dmg_eff is None:
                    dmg_eff = round((time.monotonic() - t0) * 1000.0, 2)
                    # record attacker mission at first damage
                    attackers = [{"addr": o.pointer_self,
                                  "mission": ra2yr.Mission.Name(o.current_mission),
                                  "cell": cell_of(o)}
                                 for o in objs(st2, human(st2), TYPE_INFANTRY)]
                    step("G_first_damage", hp_before=hp0, hp_now=tg.health,
                         damage_effect_ms=dmg_eff, frame=st2.current_frame,
                         attackers=attackers, target_cell=cell_of(tg))
                    break
            await asyncio.sleep(0.5)
        step("G_attack", ack_ms=ms, ack_error=err,
             target_visible_row=trow,
             target_health_before=hp0, target_health_min=hp_min,
             damage_seen=dmg_eff is not None, damage_effect_ms=dmg_eff,
             hp_timeline=hp_timeline)
        report["verdict"]["attack_on_visible_target"] = {
            "passed": bool(dmg_eff is not None and hp_min < hp0),
            "ack_error": err, "hp_before": hp0, "hp_min": hp_min,
            "damage_effect_ms": dmg_eff,
        }

        # ---------------- H. cells_difference mapped agreement sample ----------------
        agree = disagree = 0
        examples = []
        for _ in range(5):
            st3 = await state()
            md3 = await map_soa()
            for d in st3.cells_difference:
                if 0 <= d.index < len(md3.mapped):
                    if bool(d.mapped) == bool(md3.mapped[d.index]):
                        agree += 1
                    else:
                        disagree += 1
                        if len(examples) < 10:
                            examples.append({"index": d.index,
                                             "diff_mapped": bool(d.mapped),
                                             "soa_mapped": bool(md3.mapped[d.index])})
            await asyncio.sleep(0.5)
        report["cells_difference_mapped"] = {"agree": agree, "disagree": disagree,
                                             "examples": examples}

        # ---------------- I. final ----------------
        st = await state(allow_end=True)
        he = human(st)
        step("I_final", frame=st.current_frame, stage=ra2yr.LoadStage.Name(st.stage),
             human_defeated=he.defeated, human_is_loser=he.is_loser,
             human_money=he.money,
             human_objects=len(objs(st, he)))
        report["final"] = {"frame": st.current_frame,
                           "stage": ra2yr.LoadStage.Name(st.stage),
                           "human_defeated": he.defeated}
        report["forbidden_actions"] = ["SELL"] if any(
            "SELL" in str(s) for s in report["steps"]) else []
        return report
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        report["fatal"] = f"{type(e).__name__}: {e}"
        return report
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--build-timeout", type=float, default=60.0)
    ap.add_argument("--effect-timeout", type=float, default=12.0)
    ap.add_argument("--move-timeout", type=float, default=60.0)
    ap.add_argument("--damage-timeout", type=float, default=45.0)
    ap.add_argument("-o", "--output", type=pathlib.Path,
                    default=ROOT / "reports" / "dsh-visibility-test.json")
    a = ap.parse_args()
    res = asyncio.run(main(a))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print("written:", a.output)
    print("verdict:", json.dumps(res.get("verdict", {}), ensure_ascii=False, default=str))
    print("fatal:", res.get("fatal"))
