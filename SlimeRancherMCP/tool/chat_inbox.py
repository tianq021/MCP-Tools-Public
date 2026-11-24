"""读取游戏内聊天落盘记录（out/chat/*.jsonl）。

游戏内 mod 在玩家发送消息时把每条消息**追加**到 `out/chat/inbox.jsonl`；
即使 DSH 没在监听、游戏重启、或开了新对话，消息也不会丢。

容量策略：
  - mod 侧：`inbox.jsonl` 超过 1MB 时改名为 `inbox.<时间戳>.jsonl` 归档并继续写新文件；
    **归档全部保留不删除**；
  - 读取侧：按「归档（时间顺序）→ 当前文件」消费；读取状态记录
    **文件的 inode + 偏移**，因此文件轮转、追加、跨对话重启都不会读错位置；
    每条消息只消费一次。

用法：
    python -B tool\\chat_inbox.py --watch             # 阻塞等新消息，收到就逐行打印并退出（唤醒管家）
    python -B tool\\chat_inbox.py --watch --timeout 3600
    python -B tool\\chat_inbox.py --list 20           # 打印最近 20 条（不动读取状态）
    python -B tool\\chat_inbox.py --stats             # 看各文件大小/条数与读取状态
    python -B tool\\chat_inbox.py --replay            # 从头重放全部并更新状态
    python -B tool\\chat_inbox.py --reset             # 读取位置重置到当前文件末尾（不删文件）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_DIR = os.path.join(ROOT, "out", "chat")
INBOX = os.path.join(CHAT_DIR, "inbox.jsonl")
STATE = os.path.join(CHAT_DIR, "read_offset.txt")


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def file_ino(path: str) -> int:
    try:
        return int(os.stat(path).st_ino)
    except OSError:
        return 0


def ordered_files() -> list[str]:
    """归档（按时间戳名升序）在前，当前 inbox.jsonl 在最后。"""
    archives = sorted(
        p for p in glob.glob(os.path.join(CHAT_DIR, "inbox.*.jsonl"))
        if os.path.basename(p) != "inbox.jsonl"
    )
    files = list(archives)
    if os.path.exists(INBOX):
        files.append(INBOX)
    return files


def load_state() -> dict:
    """返回 {"file": 文件名, "offset": int, "ino": 文件 inode}；兼容旧格式。"""
    empty = {"file": os.path.basename(INBOX), "offset": 0, "ino": 0}
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return empty
    if not raw:
        return empty
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            return {"file": os.path.basename(INBOX), "offset": int(raw), "ino": 0}
        except ValueError:
            return empty
    if isinstance(data, int):
        return {"file": os.path.basename(INBOX), "offset": data, "ino": 0}
    return {
        "file": str(data.get("file") or os.path.basename(INBOX)),
        "offset": int(data.get("offset", 0)),
        "ino": int(data.get("ino", 0) or 0),
    }


def save_state(state: dict) -> None:
    os.makedirs(CHAT_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CHAT_DIR,
                                     prefix=".read_offset-", delete=False) as f:
        pending = f.name
    try:
        with open(pending, "w", encoding="utf-8") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(pending, STATE)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def read_complete_lines(path: str, offset: int) -> tuple[list[dict], int]:
    """从 offset 读**完整行**；最后不完整的一行留到下次。返回 (消息列表, 新 offset)。"""
    size = file_size(path)
    if offset > size:
        offset = 0
    if size == 0:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read()
    if not raw:
        return [], offset
    end = raw.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = raw[: end + 1]
    new_offset = offset + end + 1
    messages: list[dict] = []
    for line in chunk.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line.decode("utf-8", "replace")))
        except json.JSONDecodeError:
            messages.append({"raw": line.decode("utf-8", "replace")})
    return messages, new_offset


def parse_all(path: str) -> list[dict]:
    return read_complete_lines(path, 0)[0]


def resolve_start(files: list[str], state: dict) -> tuple[int, int]:
    """找出应该从哪个文件、哪个偏移继续读。

    优先按 inode 匹配（文件被改名归档后 inode 不变，脚本能顺着归档继续读）；
    旧状态没有 inode 时退回按文件名匹配。
    """
    offset = int(state.get("offset") or 0)
    ino = int(state.get("ino") or 0)
    if ino:
        for i, path in enumerate(files):
            if file_ino(path) == ino:
                return i, offset
    name = state.get("file") or ""
    if name:
        target = os.path.join(CHAT_DIR, name)
        if target in files:
            return files.index(target), offset
    return (0, 0) if files else (0, 0)


def consume(state: dict) -> tuple[list[dict], dict]:
    """按顺序消费所有文件里尚未读过的消息。"""
    files = ordered_files()
    if not files:
        return [], state
    idx, offset = resolve_start(files, state)
    messages: list[dict] = []
    last_path = files[idx]
    for i in range(idx, len(files)):
        path = files[i]
        current_offset = offset if i == idx else 0
        batch, new_offset = read_complete_lines(path, current_offset)
        messages.extend(batch)
        last_path = path
        offset = new_offset
        if new_offset < file_size(path):
            # 还有不完整的尾行，先停在这个文件
            break
    new_state = {
        "file": os.path.basename(last_path),
        "offset": offset,
        "ino": file_ino(last_path),
    }
    return messages, new_state


def count_lines(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def heartbeat() -> None:
    """告诉游戏「管家还在」——只读状态工具，不取消息、不写存档。"""
    try:
        bridge_dir = os.path.join(ROOT, "mcp")
        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)
        from bridge_client import call  # noqa: PLC0415

        call("sr_chat_status", {})
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Read/watch in-game chat inbox")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--watch", action="store_true", help="阻塞等待新消息")
    group.add_argument("--list", type=int, metavar="N", help="打印最近 N 条（不动读取状态）")
    group.add_argument("--stats", action="store_true", help="显示各文件大小/条数与读取状态")
    group.add_argument("--replay", action="store_true", help="从头重放并更新状态")
    group.add_argument("--reset", action="store_true", help="读取位置重置到当前文件末尾")
    parser.add_argument("--timeout", type=float, default=3600.0, help="--watch 最长等待秒数")
    parser.add_argument("--interval", type=float, default=1.0, help="--watch 轮询间隔秒")
    args = parser.parse_args()

    os.makedirs(CHAT_DIR, exist_ok=True)

    if args.stats:
        files = ordered_files()
        rows = [{
            "file": os.path.basename(p),
            "bytes": file_size(p),
            "messages": count_lines(p),
            "ino": file_ino(p),
        } for p in files]
        print(json.dumps({
            "dir": CHAT_DIR,
            "max_bytes_per_file": 1024 * 1024,
            "files": rows,
            "total_files": len(rows),
            "state": load_state(),
            "heartbeat": "on",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.list:
        all_messages: list[dict] = []
        for path in ordered_files():
            all_messages.extend(parse_all(path))
        for msg in all_messages[-args.list:]:
            print(json.dumps(msg, ensure_ascii=False))
        return 0

    if args.reset:
        files = ordered_files()
        active = INBOX if INBOX in files else (files[-1] if files else INBOX)
        state = {"file": os.path.basename(active), "offset": file_size(active), "ino": file_ino(active)}
        save_state(state)
        print(json.dumps(state, ensure_ascii=False))
        return 0

    if args.replay:
        files = ordered_files()
        state = {"file": os.path.basename(files[0]) if files else "inbox.jsonl", "offset": 0, "ino": 0}
        while True:
            messages, new_state = consume(state)
            for msg in messages:
                print(json.dumps(msg, ensure_ascii=False), flush=True)
            if new_state == state:
                break
            state = new_state
        save_state(state)
        return 0

    code = watch(args.timeout, args.interval)
    if code == 3:
        print("IDLE_TIMEOUT: no new in-game chat message", file=sys.stderr)
    return code


def watch(timeout: float, interval: float = 1.0) -> int:
    state = load_state()
    started = time.monotonic()
    last_heartbeat = 0.0
    while time.monotonic() - started < timeout:
        messages, new_state = consume(state)
        for msg in messages:
            print(json.dumps(msg, ensure_ascii=True), flush=True)
        if new_state != state:
            save_state(new_state)
        state = new_state
        if messages:
            return 0
        now = time.monotonic()
        if now - last_heartbeat >= 5.0:
            last_heartbeat = now
            heartbeat()
        time.sleep(min(interval, max(0, started + timeout - time.monotonic())))

    return 3


if __name__ == "__main__":
    sys.exit(main())
