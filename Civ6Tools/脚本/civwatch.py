"""事件哨兵 CLI：跟 8766 桥要「游戏里发生的新事件」。

用法
====
    python 脚本\\civwatch.py --status               # 桥/游戏/哨兵健康状态
    python 脚本\\civwatch.py --inbox                # 拉一次未读事件（P2 起）
    python 脚本\\civwatch.py --inbox --since 42     # 只拉 seq>42
    python 脚本\\civwatch.py --wait 600 --min P0    # 挂守夜：P0 事件一到立刻返回
    python 脚本\\civwatch.py --wait 600 --json      # 机器可读（给自动化/子进程用）

退出码（DSH 后台 job 就靠这个判断）
    0    拿到事件（或在 --inbox/--status 模式下正常返回）
    75   守夜超时，期间没有达到 --min 级别的事件
    2    桥连不上 / 游戏不在局内 / 探针不可用

典型工作流（见 文档\\回合清单.md）
    每回合 end_turn 之后，用后台 job 起：
        python 脚本\\civwatch.py --wait 600 --min P0
    job 一结束 DSH 会把 agent 唤醒，事件正文就在 job 输出里。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # 保证中文在任何控制台/管道下不乱码
    except (AttributeError, ValueError):
        pass

DEFAULT_BRIDGE = os.environ.get("CIV_BRIDGE_URL", "http://127.0.0.1:8766")

EXIT_EVENT = 0
EXIT_TIMEOUT = 75
EXIT_ERROR = 2


def _get(bridge: str, path: str, timeout: float) -> dict:
    url = bridge.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _print_events(data: dict, as_json: bool) -> None:
    events = data.get("events") or []
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for e in events:
        print(f"[{e['severity']}] T{e.get('turn', '?')} {e['type']}: {e['message']}")
        if e.get("hint"):
            print(f"      → {e['hint']}")
    if not events:
        print("（无新事件）")


def _print_state(data: dict) -> None:
    state = data.get("state") or {}
    watcher = data.get("watcher") or {}
    if not state:
        print("（哨兵还没有拿到任何状态）")
        return
    if not state.get("online"):
        print("游戏：离线（run_lua 连不上 FireTuner 或不在局内）")
    elif not state.get("in_game"):
        print("游戏：在线但不在局内（菜单/载入中）")
    else:
        print(f"游戏：在线 T{state.get('turn')} | 玩家 {state.get('player')}")
    if state.get("sessions"):
        for s in state["sessions"]:
            print(f"  外交会话：{s['civ']}({s['leader']}) sid={s['sid']} deal={s['deal_count']} "
                  f"war={s['at_war']} 「{s['dialogue'][:60]}」")
    if state.get("war_players"):
        print(f"  交战玩家：{state['war_players']}")
    for b in state.get("blockers") or []:
        print(f"  阻塞：{b['type']} — {b['message']}")
    if state.get("popups"):
        print(f"  弹窗：{state['popups']}")
    print(f"  通知：{state.get('notification_count')} 条 | 探针 seq={watcher.get('seq')} "
          f"feed={watcher.get('feeds')} 间隔={watcher.get('last_feed_age_s')}s")


def cmd_status(args) -> int:
    try:
        data = _get(args.bridge, "/status", args.http_timeout)
    except urllib.error.URLError as exc:
        print(f"桥不可达（{args.bridge}）：{exc.reason if hasattr(exc, 'reason') else exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_state({"state": data.get("game"), "watcher": data.get("watcher")})
    return 0


def cmd_inbox(args) -> int:
    since = 0 if args.since is None else args.since
    path = f"/inbox?since={since}&min={args.min}"
    try:
        data = _get(args.bridge, path, args.http_timeout)
    except urllib.error.URLError as exc:
        print(f"桥不可达（{args.bridge}）：{exc.reason if hasattr(exc, 'reason') else exc}", file=sys.stderr)
        return EXIT_ERROR
    _print_events(data, args.json)
    if not args.json:
        _print_state(data)
    return 0


def cmd_wait(args) -> int:
    path = f"/wait?timeout={args.wait}&min={args.min}"
    if args.since is not None:
        path += f"&since={args.since}"
    try:
        data = _get(args.bridge, path, args.wait + 20)
    except urllib.error.URLError as exc:
        print(f"桥不可达（{args.bridge}）：{exc.reason if hasattr(exc, 'reason') else exc}", file=sys.stderr)
        return EXIT_ERROR
    except TimeoutError:
        print("哨兵等待超时（HTTP 层）", file=sys.stderr)
        return EXIT_TIMEOUT

    events = data.get("events") or []
    watcher = data.get("watcher") or {}
    state = data.get("state") or {}

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_events(data, as_json=False)

    if events:
        return EXIT_EVENT

    # 没事件：区分「超时」与「根本没连上游戏」
    if state and not state.get("online"):
        print("游戏未连接：哨兵拿不到状态，不进入守夜", file=sys.stderr)
        return EXIT_ERROR
    if not watcher.get("feeds"):
        print("哨兵还没开始轮询（桥刚启动？）", file=sys.stderr)
        return EXIT_ERROR
    print(f"守夜超时：{args.wait}s 内没有 {args.min} 级事件", file=sys.stderr)
    return EXIT_TIMEOUT


def _post_json(bridge: str, path: str, payload: dict, timeout: float) -> dict:
    url = bridge.rstrip("/") + path
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_inject(args) -> int:
    """仅测试用：往桥里注入一条假事件，验证 /wait 与唤醒链路。"""
    try:
        data = _post_json(args.bridge, "/inject", {
            "severity": args.min,
            "type": "TEST_EVENT",
            "message": args.inject,
            "hint": "（测试事件，无需处理）",
        }, args.http_timeout)
    except urllib.error.URLError as exc:
        print(f"桥不可达（{args.bridge}）：{exc.reason if hasattr(exc, 'reason') else exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"已注入事件 seq={data.get('seq')} severity={args.min}: {args.inject}")
    print("若此时有 --wait 守夜在跑，它应该立刻返回（退出码 0）。")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="文明6事件哨兵客户端（8766 桥）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="打印桥/游戏/哨兵状态")
    g.add_argument("--inbox", action="store_true", help="拉取一次未读事件")
    g.add_argument("--wait", type=int, metavar="SECONDS", help="守夜：等到事件或超时")
    g.add_argument("--inject", metavar="MESSAGE", help="仅测试：注入一条假事件，验证唤醒链路")
    p.add_argument("--since", type=int, default=None,
                   help="只关心 seq 大于该值的事件（--wait 省略=只等新事件）")
    p.add_argument("--min", default="P0", choices=["P0", "P1", "P2"], help="最低严重级别（默认 P0）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--bridge", default=DEFAULT_BRIDGE, help=f"桥地址（默认 {DEFAULT_BRIDGE}）")
    p.add_argument("--http-timeout", type=float, default=30.0, help="--inbox/--status 的 HTTP 超时")
    args = p.parse_args(argv)

    if args.status:
        return cmd_status(args)
    if args.inbox:
        return cmd_inbox(args)
    if args.inject:
        return cmd_inject(args)
    return cmd_wait(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
