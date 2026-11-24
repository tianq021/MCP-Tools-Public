"""事件哨兵核心：探针解析 / 状态差分 / 事件分级。

用途
====
《文明6》的外交会话、贸易报价、EndTurn 阻塞、弹窗都是「持久状态」，但 MCP
是调用-返回，没有推送通道；agent 不主动查就会漏。本模块把「一次性拉取」变成
「事件流」：

  civwatch_probe.lua   一条 InGame Lua 往返，输出压缩状态（~50-150ms）
  WatchState.parse()   解析成结构化状态
  EventBox.feed()      和上一次状态做差分，产出带严重级别的事件（去重/宽限）
  格式化/提示           format_event()

被这些调用方复用：
  脚本\\play_mcp.py    （8766 桥）后台每 ~1.2s 喂一次探针，维护 EventBox，
                       暴露 /inbox /wait /status
  脚本\\civwatch.py    CLI 客户端：--inbox / --wait / --status
  脚本\\selftest_watch.py 离线单测（不需要游戏）

严重级别（决定谁会打断 agent）
  P0  外交会话 / 贸易报价 / 宣战 / （世界大会阻塞）→ 立即唤醒处理
  P1  EndTurn 阻塞（生产/科技/市政/总督/信条/升职/使者/奉献）、游戏离线
  P2  新通知（谴责/同盟/奇观/时代分…）、普通弹窗、回合开始、探针错误
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

SENTINEL = "---END---"
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}

_HERE = Path(__file__).resolve().parent
PROBE_PATH = _HERE / "civwatch_probe.lua"

# 探针读不到的典型文案（run_lua 在游戏没开/在菜单时会返回这些）
_OFFLINE_MARKERS = (
    "Cannot connect to Civ 6",
    "Transport error",
    "TRANSPORT_ERROR",
    "Connection refused",
    "timed out",
)

# EndTurn 阻塞 → 该用什么工具处理
BLOCKER_HINTS: dict[str, str] = {
    "ENDTURN_BLOCKING_PRODUCTION": "set_city_production(city_id, item_type, item_name)",
    "ENDTURN_BLOCKING_RESEARCH": "set_research(tech_or_civic, category='tech')",
    "ENDTURN_BLOCKING_CIVIC": "set_research(tech_or_civic, category='civic')",
    "ENDTURN_BLOCKING_UNITS": "move/skip_remaining_units()",
    "ENDTURN_BLOCKING_STACKED_UNITS": "move 叠在一起的单位",
    "ENDTURN_BLOCKING_UNIT_PROMOTION": "get_unit_promotions → promote_unit",
    "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT": "get_governors → appoint_governor",
    "ENDTURN_BLOCKING_GOVERNOR_IDLE": "assign_governor",
    "ENDTURN_BLOCKING_FILL_CIVIC_SLOT": "get_policies → set_policies",
    "ENDTURN_BLOCKING_PANTHEON": "get_pantheon_beliefs → choose_pantheon",
    "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE": "get_dedications → choose_dedication",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION": "get_world_congress → queue_wc_votes（投票后再 end_turn）",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION": "get_world_congress → queue_wc_votes",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK": "get_world_congress 复核结果",
    "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE": "change_government",
    "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY": "city_action(city_id, 'keep'/'raze')",
    "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY": "city_action(city_id, 'keep')",
    "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN": "get_city_states → send_envoy",
    "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE": "自动处理（可能已 resolve）",
}

TYPE_HINTS: dict[str, str] = {
    "DIPLO_SESSION": "get_pending_diplomacy() → respond_to_diplomacy(...) → end_turn",
    "TRADE_OFFER": "get_pending_trades() → respond_to_trade(player_id, accept=True/False)",
    "WAR_DECLARED": "get_diplomacy() 看战况；检查城防/军事；end_turn 会自动关掉宣战会话（无需回应）",
    "WAR_ENDED": "get_diplomacy() 复核和平状态",
    "BLOCKER": "按事件里的 hint 处理；处理完再 end_turn",
    "POPUP": "dismiss_popup() 关掉",
    "NOTIF": "get_notifications() 看全文（信息类，不必立刻处理）",
    "TURN_START": "get_game_overview() 开始新回合",
    "GAME_OFFLINE": "检查游戏进程与 8766 桥（play_mcp.py）是否存活",
    "GAME_ONLINE": "游戏已恢复连接，可继续",
    "PROBE_ERROR": "用 run_lua 手工排查探针错误",
}


# ---------------------------------------------------------------------------
# 探针
# ---------------------------------------------------------------------------


def load_probe() -> str:
    """读取探针 Lua 源码（每次读盘，方便改了立即生效）。"""
    return PROBE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 状态模型
# ---------------------------------------------------------------------------


@dataclass
class DiploSession:
    sid: int
    player: int
    civ: str
    leader: str
    at_war: bool
    deal_count: int
    dialogue: str

    @property
    def label(self) -> str:
        name = f"{self.civ}({self.leader})" if self.leader and self.leader != "?" else self.civ
        return name

    def to_dict(self) -> dict:
        return {
            "sid": self.sid,
            "player": self.player,
            "civ": self.civ,
            "leader": self.leader,
            "at_war": self.at_war,
            "deal_count": self.deal_count,
            "dialogue": self.dialogue,
        }


@dataclass
class Blocker:
    type: str
    notif_type: str
    added_turn: int
    message: str

    @property
    def hint(self) -> str:
        return BLOCKER_HINTS.get(self.type, f"按阻塞类型 {self.type} 处理（end_turn 会说明）")

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "notif_type": self.notif_type,
            "added_turn": self.added_turn,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass
class WatchState:
    online: bool = False
    in_game: bool = False
    player: int = -1
    turn: int = -1
    sessions: list[DiploSession] = field(default_factory=list)
    war_players: set[int] = field(default_factory=set)
    blockers: list[Blocker] = field(default_factory=list)
    notifications: list[tuple[int, str, int]] = field(default_factory=list)
    notif_count: int = 0
    notif_max_id: int = 0
    popups: list[str] = field(default_factory=list)
    probe_error: str = ""
    ts: float = 0.0

    # --- 解析 -----------------------------------------------------------
    @classmethod
    def parse(cls, text: str, is_error: bool = False, now: float | None = None) -> "WatchState":
        st = cls(ts=now if now is not None else time.time())
        if text is None:
            text = ""
        low = text.lower()
        if is_error or any(m.lower() in low for m in _OFFLINE_MARKERS):
            st.online = False
            return st
        if "STATE|" not in text:
            # 既不是离线也不是有效探针输出（可能被截断/别的错误）
            st.online = False
            st.probe_error = (text.strip().splitlines() or ["<empty>"])[0][:200]
            return st

        st.online = True
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line == SENTINEL or "|" not in line:
                continue
            parts = line.split("|")
            kind = parts[0]

            if kind == "STATE":
                st.in_game = len(parts) > 1 and parts[1] == "in_game"
            elif kind == "ME":
                st.player = _int(parts, 1, -1)
            elif kind == "TURN":
                st.turn = _int(parts, 1, -1)
            elif kind == "DIPLO":
                st.sessions.append(
                    DiploSession(
                        sid=_int(parts, 1, -1),
                        player=_int(parts, 2, -1),
                        civ=parts[3] if len(parts) > 3 else "?",
                        leader=parts[4] if len(parts) > 4 else "?",
                        at_war=(len(parts) > 5 and parts[5] == "1"),
                        deal_count=_int(parts, 6, 0),
                        dialogue="|".join(parts[7:]) if len(parts) > 7 else "",
                    )
                )
            elif kind == "WAR":
                if len(parts) > 2 and parts[2] == "1":
                    st.war_players.add(_int(parts, 1, -1))
            elif kind == "BLOCK":
                st.blockers.append(
                    Blocker(
                        type=parts[1] if len(parts) > 1 else "UNKNOWN",
                        notif_type=parts[2] if len(parts) > 2 else "UNKNOWN",
                        added_turn=_int(parts, 3, -1),
                        message=parts[4] if len(parts) > 4 else "",
                    )
                )
            elif kind == "NOTIF":
                st.notifications.append(
                    (_int(parts, 1, 0), parts[2] if len(parts) > 2 else "UNKNOWN", _int(parts, 3, -1))
                )
            elif kind == "NOTIFMETA":
                st.notif_count = _int(parts, 1, 0)
                st.notif_max_id = _int(parts, 2, 0)
            elif kind == "POPUP":
                st.popups.append(parts[1] if len(parts) > 1 else "UNKNOWN")
            elif kind == "PROBE_ERROR":
                st.probe_error = "|".join(parts[1:])[:300]

        if st.probe_error and not st.in_game:
            st.online = False
        return st

    def summary(self) -> dict:
        return {
            "online": self.online,
            "in_game": self.in_game,
            "player": self.player,
            "turn": self.turn,
            "sessions": [s.to_dict() for s in self.sessions],
            "war_players": sorted(self.war_players),
            "blockers": [b.to_dict() for b in self.blockers],
            "popups": list(self.popups),
            "notification_count": self.notif_count,
            "notification_max_id": self.notif_max_id,
            "probe_error": self.probe_error,
            "ts": round(self.ts, 3),
        }


def _int(parts: list[str], idx: int, default: int) -> int:
    try:
        return int(parts[idx])
    except (IndexError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


@dataclass
class Event:
    seq: int
    ts: float
    turn: int
    severity: str
    type: str
    key: str
    message: str
    hint: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": round(self.ts, 3),
            "turn": self.turn,
            "severity": self.severity,
            "type": self.type,
            "key": self.key,
            "message": self.message,
            "hint": self.hint,
            "data": self.data,
        }


class EventBox:
    """把连续探针快照差分成事件流（带去重、宽限、基线抑制）。

    - `feed()` 每次探针调用一次，返回本次新产生的事件列表；
    - 首次 feed 视为基线：只报告 P0（外交/战争）与 P1（阻塞），
      不把历史通知刷成 P2 事件；
    - 外交会话有 `grace` 秒宽限：等对话正文填充后再报，避免报到空文本；
    - `seen` 单调增长（上限裁剪），同 key 事件不重复；
    - 事件环形缓冲 `max_events`，供 /inbox /wait 增量拉取。
    """

    def __init__(self, grace: float = 1.2, max_events: int = 500, seen_limit: int = 800):
        self.grace = grace
        self.seen_limit = seen_limit
        self._prev: WatchState | None = None
        self._baseline_done = False
        self._seen: dict[str, float] = {}
        self._first_seen: dict[str, float] = {}
        self._events: deque[Event] = deque(maxlen=max_events)
        self._seq = 0
        self._feeds = 0
        self._last_feed_ts = 0.0
        self._last_probe_error = ""

    # --- 主入口 ---------------------------------------------------------
    def feed(self, text: str, is_error: bool = False, now: float | None = None) -> list[Event]:
        now = time.time() if now is None else now
        self._feeds += 1
        self._last_feed_ts = now
        cur = WatchState.parse(text, is_error=is_error, now=now)
        events: list[Event] = []

        # 离线：只在「在线→离线」跳变时报一次
        if not cur.online:
            if self._prev is not None and self._prev.online:
                events.append(
                    self._emit(now, cur.turn, "P1", "GAME_OFFLINE", "offline",
                               "游戏连接断开（run_lua 失败或不在局内）", TYPE_HINTS["GAME_OFFLINE"])
                )
            self._prev = cur
            return self._push(events)

        if self._prev is not None and not self._prev.online:
            events.append(
                self._emit(now, cur.turn, "P2", "GAME_ONLINE", "online",
                           "游戏连接恢复", TYPE_HINTS["GAME_ONLINE"])
            )

        first = not self._baseline_done

        # 探针内部报错：同一条错误只报一次
        if cur.probe_error and cur.probe_error != self._last_probe_error:
            self._last_probe_error = cur.probe_error
            events.append(
                self._emit(now, cur.turn, "P2", "PROBE_ERROR", f"probe:{cur.probe_error[:60]}",
                           f"探针内部错误：{cur.probe_error[:200]}", TYPE_HINTS["PROBE_ERROR"])
            )

        prev = self._prev

        # 回合推进
        if prev is not None and prev.turn != cur.turn and cur.turn >= 0:
            events.append(
                self._emit(now, cur.turn, "P2", "TURN_START", f"turn:{cur.turn}",
                           f"进入新回合 T{cur.turn}", TYPE_HINTS["TURN_START"])
            )

        # 战争状态跳变（会话可能被 end_turn 自动关掉，战争状态不会）
        prev_war = prev.war_players if prev is not None else set()
        for player in sorted(cur.war_players - prev_war):
            label = _player_label(cur, player)
            events.append(
                self._emit(now, cur.turn, "P0", "WAR_DECLARED", f"war:{player}",
                           f"{label} 向你宣战！", TYPE_HINTS["WAR_DECLARED"],
                           {"player": player})
            )
        for player in sorted(prev_war - cur.war_players):
            events.append(
                self._emit(now, cur.turn, "P2", "WAR_ENDED", f"war_end:{player}",
                           f"与 {_player_label(cur, player)} 的战争状态结束", TYPE_HINTS["WAR_ENDED"],
                           {"player": player})
            )

        # 外交会话（带 grace，等对话正文）。宣战会话交给上面的战争状态跳变处理，
        # 因为 end_turn 会把宣战会话自动关掉，会话本身可能一瞬即逝。
        live_diplo_keys: set[str] = set()
        for s in cur.sessions:
            if s.at_war:
                continue
            phase = "deal" if s.deal_count > 0 else "talk"
            key = f"diplo:{s.player}:{s.sid}:{phase}"
            live_diplo_keys.add(key)
            if key in self._seen:
                continue
            self._first_seen.setdefault(key, now)
            ready = bool(s.dialogue) or (now - self._first_seen[key]) >= self.grace
            if not ready:
                continue
            if s.deal_count > 0:
                etype, sev = "TRADE_OFFER", "P0"
                msg = f"{s.label} 向你报价（{s.deal_count} 项）：{_short(s.dialogue)}"
                hint = TYPE_HINTS["TRADE_OFFER"]
            else:
                etype, sev = "DIPLO_SESSION", "P0"
                msg = f"{s.label} 正在与你对话：{_short(s.dialogue) or '(对话文本还在加载)'}"
                hint = TYPE_HINTS["DIPLO_SESSION"]
            events.append(
                self._emit(now, cur.turn, sev, etype, key, msg, hint,
                           {"player": s.player, "sid": s.sid, "phase": phase,
                            "deal_count": s.deal_count, "dialogue": s.dialogue})
            )
            self._mark(key, now)
            self._first_seen.pop(key, None)
        for key in list(self._first_seen):
            if key.startswith("diplo:") and key not in live_diplo_keys:
                self._first_seen.pop(key, None)

        # EndTurn 阻塞（每个回合每种阻塞报一次）
        for b in cur.blockers:
            key = f"block:{cur.turn}:{b.type}:{b.notif_type}"
            if key in self._seen:
                continue
            events.append(
                self._emit(now, cur.turn, "P1", "BLOCKER", key,
                           f"{b.type}：{b.message or b.notif_type}",
                           b.hint, b.to_dict())
            )
            self._mark(key, now)

        # 普通弹窗（只在「新出现」时报；外交弹窗已由会话覆盖）
        prev_popups = set(prev.popups) if prev is not None else set()
        for name in cur.popups:
            if name in prev_popups:
                continue
            key = f"popup:{name}"
            if key in self._seen:
                continue
            if name in ("DiplomacyActionView", "DiplomacyDealView") and cur.sessions:
                continue
            events.append(
                self._emit(now, cur.turn, "P2", "POPUP", key, f"弹窗出现：{name}",
                           TYPE_HINTS["POPUP"], {"popup": name})
            )
            self._mark(key, now)

        # 新通知（基线时抑制已有通知；阻塞类通知已由 BLOCKER 覆盖）
        if not first:
            prev_ids = {n[0] for n in prev.notifications} if prev is not None else set()
            blocking_types = {b.notif_type for b in cur.blockers}
            for nid, ntype, added in cur.notifications:
                if nid in prev_ids:
                    continue
                if ntype in blocking_types:
                    continue
                key = f"notif:{nid}"
                if key in self._seen:
                    continue
                events.append(
                    self._emit(now, cur.turn, "P2", "NOTIF", key,
                               f"新通知：{ntype}（T{added}）", TYPE_HINTS["NOTIF"],
                               {"notif_id": nid, "notif_type": ntype, "added_turn": added})
                )
                self._mark(key, now)

        self._baseline_done = True
        self._prev = cur
        self._prune_seen()
        return self._push(events)

    # --- 查询 -----------------------------------------------------------
    @property
    def state(self) -> WatchState | None:
        return self._prev

    @property
    def seq(self) -> int:
        return self._seq

    def snapshot(self, since: int = 0, min_severity: str = "P2") -> dict:
        rank = SEVERITY_ORDER.get(min_severity, 2)
        events = [e.to_dict() for e in self._events
                  if e.seq > since and SEVERITY_ORDER.get(e.severity, 9) <= rank]
        return {
            "seq": self._seq,
            "events": events,
            "state": self._prev.summary() if self._prev else None,
            "watcher": self.status(),
        }

    def blocking_events(self, since: int = 0, min_severity: str = "P0") -> list[Event]:
        rank = SEVERITY_ORDER.get(min_severity, 0)
        return [e for e in self._events if e.seq > since and SEVERITY_ORDER.get(e.severity, 9) <= rank]

    def inject(self, severity: str = "P0", etype: str = "TEST_EVENT",
               message: str = "测试事件", hint: str = "（测试用，忽略）") -> Event:
        """仅测试用：直接注入一个事件，验证 /wait 与唤醒链路（不经过游戏）。"""
        now = time.time()
        event = self._emit(now, self._prev.turn if self._prev else -1,
                           severity, etype, f"inject:{self._seq + 1}", message, hint,
                           {"injected": True})
        self._push([event])
        return event

    def status(self) -> dict:
        st = self._prev
        return {
            "feeds": self._feeds,
            "last_feed_ts": round(self._last_feed_ts, 3),
            "last_feed_age_s": round(time.time() - self._last_feed_ts, 2) if self._last_feed_ts else None,
            "events": len(self._events),
            "seq": self._seq,
            "online": bool(st and st.online),
            "in_game": bool(st and st.in_game),
            "turn": st.turn if st else -1,
            "player": st.player if st else -1,
            "sessions": len(st.sessions) if st else 0,
            "blockers": len(st.blockers) if st else 0,
            "popups": len(st.popups) if st else 0,
            "probe_error": st.probe_error if st else "",
        }

    # --- 内部 -----------------------------------------------------------
    def _emit(self, now: float, turn: int, severity: str, etype: str, key: str,
              message: str, hint: str, data: dict | None = None) -> Event:
        self._seq += 1
        return Event(self._seq, now, turn, severity, etype, key, message, hint, data or {})

    def _push(self, events: list[Event]) -> list[Event]:
        for e in events:
            self._events.append(e)
        return events

    def _mark(self, key: str, now: float) -> None:
        self._seen[key] = now

    def _prune_seen(self) -> None:
        if len(self._seen) <= self.seen_limit:
            return
        keep = sorted(self._seen.items(), key=lambda kv: kv[1])[-self.seen_limit:]
        self._seen = dict(keep)


def _player_label(state: WatchState, player: int) -> str:
    for s in state.sessions:
        if s.player == player:
            return s.label
    return f"玩家 {player}"


def _short(text: str, limit: int = 80) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_event(event: dict | Event) -> str:
    """一行人类可读格式（给 DSH 唤醒通知 / CLI 输出）。"""
    e = event.to_dict() if isinstance(event, Event) else event
    return f"[{e['severity']}] T{e.get('turn', '?')} {e['type']}: {e['message']} → {e['hint']}"


def events_to_jsonl(events, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for e in events:
            e = e.to_dict() if isinstance(e, Event) else e
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
