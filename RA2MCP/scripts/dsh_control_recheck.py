"""Follow-up: clean squad order, clean invalid-order rejection, attack damage.

Runs against the still-running offline skirmish in E:\\RA2MCP\\test-game.
Only read commands plus MOVE/STOP/ATTACK orders; never SELL.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_control_recheck.py -o reports/dsh-control-recheck.json
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

TYPE_INFANTRY = "ABSTRACT_TYPE_INFANTRY"
report = {"script": "dsh_control_recheck.py",
          "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
            r = await asyncio.wait_for(client.exec_command(cmd, timeout or opts.cmd_timeout),
                                       (timeout or opts.cmd_timeout) + 3)
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
        if msg.state.stage != 2:
            report["game_end_state"] = {
                "wall": time.strftime("%H:%M:%S"),
                "stage": ra2yr.LoadStage.Name(msg.state.stage),
                "houses": [{"name": h.name, "defeated": h.defeated,
                            "is_loser": h.is_loser, "is_winner": h.is_winner}
                           for h in msg.state.houses]}
            raise RuntimeError("game ended")
        return msg.state

    def human(st):
        return next(h for h in st.houses if h.current_player and h.is_human_player)

    def infantry(st, house):
        return [o for o in st.objects
                if o.pointer_house == house.self and o.health > 0
                and ra2yr.AbstractType.Name(o.object_type) == TYPE_INFANTRY]

    async def order(action, addresses, coords=None, target=0):
        q = commands_game.UnitOrder(object_addresses=list(addresses),
                                    action=getattr(ra2yr, action))
        if coords is not None:
            q.coordinates.x, q.coordinates.y = coords
        if target:
            q.target_object = target
        ms, msg, err = await send(q, commands_game.UnitOrder)
        return ms, msg, err

    st = await state()
    gis = infantry(st, human(st))
    step("A_state", frame=st.current_frame, infantry=len(gis),
         units=[{"addr": o.pointer_self, "mission": ra2yr.Mission.Name(o.current_mission),
                 "xyz": [o.coordinates.x, o.coordinates.y]} for o in gis])
    if len(gis) < 2:
        raise RuntimeError("need 2 infantry for this recheck")

    # settle both infantry first
    await order("UNIT_ACTION_STOP", [o.pointer_self for o in gis])
    await asyncio.sleep(2.0)
    st = await state()
    gis = infantry(st, human(st))
    before = {o.pointer_self: (o.coordinates.x, o.coordinates.y) for o in gis}
    # ---------------- squad move (both valid addresses, valid destination) ----------------
    cell = next((o for o in st.objects
                 if o.pointer_house == human(st).self and
                 ra2yr.AbstractType.Name(o.object_type) == "ABSTRACT_TYPE_BUILDING"), gis[0])
    dest = (cell.coordinates.x + 2048, cell.coordinates.y + 1024)
    ack_ms, msg, err = await order("UNIT_ACTION_MOVE", list(before.keys()), dest)
    moved = {}
    eff = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < opts.effect_timeout:
        st = await state()
        cur = {o.pointer_self: (o.coordinates.x, o.coordinates.y)
               for o in st.objects if o.pointer_self in before}
        moved = {k: (k in cur and cur[k] != before[k]) for k in before}
        if all(moved.values()):
            eff = round((time.monotonic() - t0) * 1000.0, 2)
            break
        await asyncio.sleep(0.25)
    after = {k: list(cur.get(k)) if k in cur else None for k in before}
    step("B_squad_move", ack_ms=ack_ms, ack_error=err, dest=list(dest),
         before={k: list(v) for k, v in before.items()}, after=after,
         moved=moved, effect_ms=eff,
         native_echo=MessageToDict(msg) if msg else None)
    report["verdict"]["squad_move_clean"] = {
        "passed": bool(err is None and all(moved.values()) and eff is not None),
        "ack_ms": ack_ms, "effect_ms": eff, "moved": moved}
    await order("UNIT_ACTION_STOP", list(before.keys()))
    await asyncio.sleep(2.0)

    # ---------------- invalid order on a stationary unit ----------------
    st = await state()
    gis = infantry(st, human(st))
    target_unit = gis[0]
    base = (target_unit.coordinates.x, target_unit.coordinates.y)
    ack_ms, msg, err = await order("UNIT_ACTION_MOVE", [target_unit.pointer_self],
                                   (-1000, -1000))
    await asyncio.sleep(2.0)
    st2 = await state()
    now = next((o for o in st2.objects if o.pointer_self == target_unit.pointer_self), None)
    pos_after = (now.coordinates.x, now.coordinates.y) if now else None
    step("C_invalid_order", ack_ms=ack_ms, ack_error=err,
         pos_before=list(base), pos_after=list(pos_after) if pos_after else None,
         frame_before=st.current_frame, frame_after=st2.current_frame)
    report["verdict"]["invalid_order_rejected"] = {
        "passed": bool(err is not None) and pos_after == base,
        "ack_ms": ack_ms, "error": err, "moved": pos_after != base}

    # ---------------- attack + damage ----------------
    st = await state()
    hh = human(st)
    gis = infantry(st, hh)
    neutral = next((h for h in st.houses if h.name == "Neutral"), None)
    attacker = gis[0]
    axy = (attacker.coordinates.x, attacker.coordinates.y)
    targets = [o for o in st.objects if neutral and o.pointer_house == neutral.self
               and o.health > 0 and o.on_map]
    targets.sort(key=lambda o: math.hypot(o.coordinates.x - axy[0], o.coordinates.y - axy[1]))
    tgt = targets[0] if targets else None
    if tgt is None:
        step("D_attack_skipped", reason="no neutral target")
        report["verdict"]["attack_damage"] = {"passed": False, "reason": "skipped"}
    else:
        hp0 = tgt.health
        dist0 = round(math.hypot(tgt.coordinates.x - axy[0], tgt.coordinates.y - axy[1]), 1)
        ack_ms, msg, err = await order("UNIT_ACTION_ATTACK", [attacker.pointer_self],
                                       (tgt.coordinates.x, tgt.coordinates.y),
                                       tgt.pointer_self)
        hp_min = hp0
        dmg_eff = None
        att_mission = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < opts.damage_timeout:
            st = await state()
            tg = next((o for o in st.objects if o.pointer_self == tgt.pointer_self), None)
            if tg is not None and tg.health < hp_min:
                hp_min = tg.health
                if dmg_eff is None:
                    dmg_eff = round((time.monotonic() - t0) * 1000.0, 2)
                break
            atk = next((o for o in st.objects if o.pointer_self == attacker.pointer_self), None)
            if atk is not None:
                att_mission = ra2yr.Mission.Name(atk.current_mission)
            await asyncio.sleep(1.0)
        step("D_attack", ack_ms=ack_ms, ack_error=err,
             attacker=attacker.pointer_self, attacker_mission=att_mission,
             target_addr=tgt.pointer_self, target_cell=[tgt.coordinates.x, tgt.coordinates.y],
             distance=dist0, hp_before=hp0, hp_min=hp_min,
             damage_seen=dmg_eff is not None, damage_effect_ms=dmg_eff,
             observe_window_s=opts.damage_timeout)
        report["verdict"]["attack_damage"] = {
            "passed": bool(err is None and dmg_eff is not None),
            "ack_ms": ack_ms, "damage_seen": dmg_eff is not None,
            "hp_before": hp0, "hp_min": hp_min, "distance": dist0,
            "mission": att_mission}

    st = await state()
    hh = human(st)
    step("E_final", frame=st.current_frame, money=hh.money, defeated=hh.defeated,
         is_game_over=hh.is_game_over,
         objects=[{"addr": o.pointer_self,
                   "mission": ra2yr.Mission.Name(o.current_mission),
                   "abstract": ra2yr.AbstractType.Name(o.object_type),
                   "xyz": [o.coordinates.x, o.coordinates.y]}
                  for o in st.objects if o.pointer_house == hh.self])
    report["forbidden_sent"] = [s for s in report["steps"] if "SELL" in str(s.get("action", ""))]
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--cmd-timeout", type=float, default=5.0)
    ap.add_argument("--effect-timeout", type=float, default=12.0)
    ap.add_argument("--damage-timeout", type=float, default=120.0)
    ap.add_argument("-o", "--output", type=pathlib.Path,
                    default=ROOT / "reports" / "dsh-control-recheck.json")
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
