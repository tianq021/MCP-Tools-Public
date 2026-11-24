"""聊天监听主力进程：收到消息或发现积压就退出（退出即唤醒 DSH）。

合并了两种职责，只需重启一个进程：
- 正常监听：轮询 inbox，有新消息立刻打印并退出
- 兜底告警：如果消息长时间未被消费（自己没在监听、或消费失败），也要退出报警

用法：python tool/chat_listen.py [--timeout 3600]
退出码：0 = 有新消息/需要 DSH 处理；3 = 长时间无消息（超时）
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from chat_inbox import watch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=0, help="最长空闲秒数；默认 0 持续等待")
    ap.add_argument("--retries", type=int, default=40)
    args = ap.parse_args()

    if args.timeout < 0:
        ap.error("--timeout 必须大于或等于 0")
    return listen(args.timeout)


def listen(timeout: int = 0) -> int:
    deadline = time.monotonic() + timeout if timeout else None
    while deadline is None or time.monotonic() < deadline:
        wait = 120 if deadline is None else min(120, max(0, deadline - time.monotonic()))
        try:
            code = watch(wait)
        except BrokenPipeError:
            return 1
        except OSError as exc:
            print(json.dumps({"event": "watch_failed", "error": str(exc)}), file=sys.stderr, flush=True)
            time.sleep(5 if deadline is None else min(5, max(0, deadline - time.monotonic())))
            continue
        if code == 0:
            return 0
    print(json.dumps({"event": "listen_timeout"}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
