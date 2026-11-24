"""聊天监听守护：自动重试 + 收到消息就退出（退出即通知 DSH）。

设计目的：`chat_inbox.py --watch` 是一次性的，遇到桥接抖动/报错就退出，
容易导致「消息进来了但没人取」（2026-11-18 两次通信断线事故）。

本守护循环执行「一次性监听」：
- 收到消息 -> 打印消息并 exit 0（让 DSH 收到完成通知，然后重新拉起本守护）
- 监听超时/异常 -> 自动重启内层监听，保持在线
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

PY = r"C:\Program Files\Python310\python.exe"
WATCH = r"E:\SlimeRancherMCP\tool\chat_inbox.py"


def run_once() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-B", WATCH, "--watch", "--timeout", "300"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "").strip()


def main() -> int:
    deadline = time.time() + 7200  # 守护最长 2 小时
    while time.time() < deadline:
        try:
            code, out = run_once()
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"at": time.strftime("%H:%M:%S"), "event": "spawn_error", "error": str(exc)},
                             ensure_ascii=False), flush=True)
            time.sleep(5)
            continue
        if code == 0 and out:
            print(out, flush=True)
            return 0                      # 有消息 -> 退出，唤醒 DSH
        if code == 3:                     # 内层超时（无消息）-> 继续守
            time.sleep(1)
            continue
        print(json.dumps({"at": time.strftime("%H:%M:%S"), "event": "watch_failed", "code": code,
                          "stderr_tail": out[-200:]}, ensure_ascii=False), flush=True)
        time.sleep(5)
    print(json.dumps({"event": "guard_timeout"}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
