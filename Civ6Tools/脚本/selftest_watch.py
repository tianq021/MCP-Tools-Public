"""事件哨兵离线自测（不需要游戏、不需要桥）。

用法：
    E:\\Civ6\\civ6-mcp\\.venv\\Scripts\\python.exe 脚本\\selftest_watch.py

覆盖：
  * 探针 Lua 能被真实 Lua VM 编译（lupa）
  * 探针输出解析（各字段/边界）
  * EventBox 差分：基线抑制、外交 grace、贸易、宣战、阻塞、弹窗、新通知、去重
  * snapshot 级别过滤与 format_event
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import watch_core as wc  # noqa: E402

FAILS: list[str] = []
CHECKS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


def probe_text(turn=10, me=0, sessions=(), wars=(), blocks=(), notifs=(), popups=(),
               state="in_game", meta=None) -> str:
    lines = [f"STATE|{state}"]
    if state == "in_game":
        lines += [f"ME|{me}", f"TURN|{turn}"]
        for s in sessions:
            lines.append("DIPLO|{sid}|{player}|{civ}|{leader}|{war}|{deal}|{dialogue}".format(**s))
        for player, flag in wars:
            lines.append(f"WAR|{player}|{flag}")
        for b in blocks:
            lines.append("BLOCK|{type}|{notif_type}|{added_turn}|{message}".format(**b))
        for nid, ntype, added in notifs:
            lines.append(f"NOTIF|{nid}|{ntype}|{added}")
        count, maxid = meta if meta else (len(notifs), max([n[0] for n in notifs], default=0))
        lines.append(f"NOTIFMETA|{count}|{maxid}")
        for p in popups:
            lines.append(f"POPUP|{p}")
    lines.append(wc.SENTINEL)
    return "\n".join(lines)


def main() -> int:
    print("== 1) 探针 Lua 编译 ==")
    try:
        import lupa  # type: ignore

        src = wc.load_probe()
        lua = lupa.LuaRuntime()
        lua.compile(src)
        check("探针可被 Lua VM 编译", True)
        check("探针带 SENTINEL", wc.SENTINEL in src)
        check("探针覆盖通知/外交/弹窗", all(k in src for k in ("NotificationManager", "FindOpenSessionID", "POPUP")))
    except ImportError:
        check("lupa 可用（跳过编译检查）", True)
        print("  SKIP  lupa 未安装，跳过 Lua 编译检查")

    print("== 2) 解析 ==")
    txt = probe_text(
        turn=37,
        sessions=[dict(sid=11, player=3, civ="刚果", leader="姆本巴·恩津加", war=0, deal=0, dialogue="你好")],
        wars=[(2, 0), (5, 1)],
        blocks=[dict(type="ENDTURN_BLOCKING_PRODUCTION", notif_type="NOTIFICATION_CHOOSE_CITY_PRODUCTION",
                     added_turn=37, message="北京 需要选择生产")],
        notifs=[(101, "NOTIFICATION_DENOUNCED", 36), (102, "NOTIFICATION_CHOOSE_CITY_PRODUCTION", 37)],
        popups=["DiplomacyActionView", "BoostUnlockedPopup"],
    )
    st = wc.WatchState.parse(txt)
    check("online/in_game", st.online and st.in_game)
    check("turn/player", st.turn == 37 and st.player == 0)
    check("会话解析", len(st.sessions) == 1 and st.sessions[0].player == 3
          and st.sessions[0].dialogue == "你好", repr(st.sessions))
    check("战争集合", st.war_players == {5}, repr(st.war_players))
    check("阻塞解析", len(st.blockers) == 1 and st.blockers[0].type.endswith("PRODUCTION"))
    check("通知解析", len(st.notifications) == 2 and st.notif_max_id == 102)
    check("弹窗解析", st.popups == ["DiplomacyActionView", "BoostUnlockedPopup"])

    offline = wc.WatchState.parse("Cannot connect to Civ 6 at 127.0.0.1:4318. Is the game running with EnableTuner=1?")
    check("离线识别", offline.online is False)
    no_player = wc.WatchState.parse(probe_text(state="no_player"))
    check("菜单识别（在线但不在局内）", no_player.online and not no_player.in_game)

    print("== 3) 基线 ==")
    box = wc.EventBox(grace=1.2)
    base = probe_text(turn=10, notifs=[(1, "NOTIFICATION_OLD", 5), (2, "NOTIFICATION_DENOUNCED", 6)])
    ev = box.feed(base, now=100.0)
    check("基线不把旧通知刷成事件", ev == [], repr([e.type for e in ev]))
    check("基线状态可用", box.state is not None and box.state.turn == 10)

    print("== 4) 外交 grace ==")
    s_no_text = [dict(sid=21, player=4, civ="罗马", leader="图拉真", war=0, deal=0, dialogue="")]
    box.feed(probe_text(turn=10, notifs=[(1, "NOTIFICATION_OLD", 5)], sessions=s_no_text), now=101.0)
    check("首见无对话不立刻报", not box.blocking_events(min_severity="P0"))
    box.feed(probe_text(turn=10, notifs=[(1, "NOTIFICATION_OLD", 5)], sessions=s_no_text), now=101.5)
    check("宽限内仍不报", not box.blocking_events(min_severity="P0"))
    s_text = [dict(sid=21, player=4, civ="罗马", leader="图拉真", war=0, deal=0, dialogue="我们需要谈谈边界问题。")]
    ev = box.feed(probe_text(turn=10, notifs=[(1, "NOTIFICATION_OLD", 5)], sessions=s_text), now=102.5)
    check("对话填充后报 DIPLO_SESSION(P0)", len(ev) == 1 and ev[0].type == "DIPLO_SESSION"
          and ev[0].severity == "P0", repr([e.type for e in ev]))
    ev2 = box.feed(probe_text(turn=10, notifs=[(1, "NOTIFICATION_OLD", 5)], sessions=s_text), now=103.0)
    check("同一会话不重复报", box.blocking_events(min_severity="P0") == [ev[0]] and ev2 == [])

    print("== 5) 贸易报价升级 ==")
    s_deal = [dict(sid=21, player=4, civ="罗马", leader="图拉真", war=0, deal=2, dialogue="这笔交易对你有利。")]
    ev = box.feed(probe_text(turn=10, notifs=[(1, "NOTIFICATION_OLD", 5)], sessions=s_deal), now=104.0)
    check("报价单出现报 TRADE_OFFER(P0)", any(e.type == "TRADE_OFFER" and e.severity == "P0" for e in ev),
          repr([e.type for e in ev]))

    print("== 6) 宣战（会话被自动关掉也能报） ==")
    box2 = wc.EventBox(grace=1.2)
    box2.feed(probe_text(turn=20, wars=[(2, 0)]), now=200.0)
    ev = box2.feed(probe_text(turn=20, wars=[(2, 1)]), now=201.0)
    check("战争状态跳变报 WAR_DECLARED(P0)", len(ev) == 1 and ev[0].type == "WAR_DECLARED"
          and ev[0].severity == "P0", repr([e.type for e in ev]))
    ev = box2.feed(probe_text(turn=20, wars=[(2, 1)]), now=201.5)
    check("宣战不重复报", ev == [])

    print("== 7) 阻塞 ==")
    box3 = wc.EventBox(grace=1.2)
    box3.feed(probe_text(turn=30), now=300.0)
    b = dict(type="ENDTURN_BLOCKING_RESEARCH", notif_type="NOTIFICATION_CHOOSE_TECH", added_turn=30, message="选择科技")
    ev = box3.feed(probe_text(turn=30, blocks=[b]), now=301.0)
    check("阻塞报 BLOCKER(P1)", len(ev) == 1 and ev[0].type == "BLOCKER" and ev[0].severity == "P1")
    ev = box3.feed(probe_text(turn=30, blocks=[b]), now=301.5)
    check("同回合不重复", ev == [])
    ev = box3.feed(probe_text(turn=31, blocks=[b]), now=302.0)
    check("新回合重新提醒", len(ev) == 1 and ev[0].type == "TURN_START" or any(e.type == "BLOCKER" for e in ev),
          repr([e.type for e in ev]))

    print("== 8) 弹窗 / 新通知 / 离线 ==")
    box4 = wc.EventBox(grace=1.2)
    box4.feed(probe_text(turn=40, notifs=[(50, "NOTIFICATION_OLD", 39)]), now=400.0)
    ev = box4.feed(probe_text(turn=40, notifs=[(50, "NOTIFICATION_OLD", 39)], popups=["BoostUnlockedPopup"]), now=401.0)
    check("普通弹窗报 POPUP(P2)", len(ev) == 1 and ev[0].type == "POPUP" and ev[0].severity == "P2")
    ev = box4.feed(probe_text(turn=40, notifs=[(50, "NOTIFICATION_OLD", 39), (51, "NOTIFICATION_WONDER_BUILT", 40)],
                              popups=["BoostUnlockedPopup"]), now=402.0)
    check("新通知报 NOTIF(P2)", len(ev) == 1 and ev[0].type == "NOTIF" and ev[0].severity == "P2",
          repr([e.type for e in ev]))
    ev = box4.feed("Cannot connect to Civ 6 at 127.0.0.1:4318.", now=403.0)
    check("掉线报 GAME_OFFLINE(P1)", len(ev) == 1 and ev[0].type == "GAME_OFFLINE" and ev[0].severity == "P1")
    ev = box4.feed(probe_text(turn=40, notifs=[(50, "NOTIFICATION_OLD", 39)]), now=404.0)
    check("恢复报 GAME_ONLINE(P2)", any(e.type == "GAME_ONLINE" for e in ev))

    print("== 9) snapshot / 过滤 / 格式化 ==")
    snap = box4.snapshot(since=0, min_severity="P0")
    check("P0 过滤只留 P0", all(e["severity"] == "P0" for e in snap["events"]))
    snap_all = box4.snapshot(since=0, min_severity="P2")
    check("snapshot 带 state", snap_all["state"] is not None and "turn" in snap_all["state"])
    line = wc.format_event({"severity": "P0", "turn": 37, "type": "DIPLO_SESSION",
                            "message": "罗马 正在与你对话", "hint": "respond_to_diplomacy"})
    check("format_event 可读", line.startswith("[P0]") and "respond_to_diplomacy" in line)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}/{CHECKS}: {FAILS}")
        return 1
    print(f"ALL {CHECKS} CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
