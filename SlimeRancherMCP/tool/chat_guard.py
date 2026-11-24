"""聊天消息守护：检测「游戏内有消息但长时间没人取」，超时就退出以唤醒管家。

背景：chat_inbox.py --watch 是一次性任务（读到一条消息就退出），管家处理完后
需要手动重启；漏重启会让游戏内消息一直排队（界面显示「消息排队中」）。

本守护任务每 --interval 秒检查一次本地 inbox 文件是否有未读消息（相对 read_offset），
连续 --streak 次都发现未读就打印告警并退出（DSH 会收到完成通知 → 唤醒管家重新接管）。

不消费消息、不修改任何状态，只读。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_DIR = os.path.join(ROOT, "out", "chat")
INBOX = os.path.join(CHAT_DIR, "inbox.jsonl")
STATE = os.path.join(CHAT_DIR, "read_offset.txt")


def read_state() -> dict:
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def unread_count() -> tuple[int, str]:
    state = read_state()
    offset = int(state.get("offset") or 0)
    try:
        with open(INBOX, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return 0, ""
    if offset > len(data):
        offset = 0
    chunk = data[offset:]
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    last = ""
    if lines:
        try:
            last = json.loads(lines[-1].decode("utf-8")).get("text", "")
        except Exception:
            last = ""
    return len(lines), last


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard for stalled in-game chat delivery")
    parser.add_argument("--interval", type=float, default=15.0, help="检查间隔秒")
    parser.add_argument("--streak", type=int, default=2, help="连续多少次未读才告警")
    parser.add_argument("--timeout", type=float, default=7200.0, help="最长运行秒数")
    args = parser.parse_args()

    started = time.time()
    streak = 0
    last_count = 0
    while time.time() - started < args.timeout:
        count, last = unread_count()
        if count > 0:
            streak += 1
            last_count = count
            if streak >= args.streak:
                print(json.dumps({
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "alert": "CHAT_DELIVERY_STALLED",
                    "unread": count,
                    "last_text": last,
                }, ensure_ascii=False))
                return 0
        else:
            streak = 0
        time.sleep(args.interval)

    print("IDLE_TIMEOUT: no stalled chat delivery", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
