"""盯 Rush 结算：Steam 位图出现 time_limit_currency_c 就退出并唤醒 DSH。

同时打印游戏日/小时与当前现金，便于判断还剩多久。
用法：python -B tool\\watch_rush_finish.py [--timeout 5400]
退出码：0 = 成就已解锁；2 = Rush 存档已结束但成就没到位；3 = 超时
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call, health  # noqa: E402

SCHED = r"G:\Codex_mmx\SlimeRancher成就查询_20261117"
TARGET = "time_limit_currency_c"


def unlocked() -> set[str] | None:
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, r'{SCHED}')\n"
        "import build_sr as B\n"
        "s = B.load_schema('sr1')\n"
        "print(json.dumps(sorted(B.load_local_unlocked('433340', s))))\n"
    )
    try:
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return set(json.loads(out.stdout.strip().splitlines()[-1]))
    except Exception:
        return None


def clock() -> tuple[int | None, float | None, str]:
    try:
        res = call("sr_state", {})
        text = (res.get("data") or {}).get("text") or ""
        day = hour = None
        mode = "?"
        for line in text.splitlines():
            if "gameMode=" in line:
                for tok in line.split():
                    if tok.startswith("gameMode="):
                        mode = tok.split("=", 1)[1]
            if line.startswith("worldTime="):
                parts = dict(p.split("=", 1) for p in line.split() if "=" in p)
                day = int(float(parts.get("day", "0")))
                hour = float(parts.get("hour", "0"))
        return day, hour, mode
    except BridgeError:
        return None, None, "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=5400.0)
    ap.add_argument("--interval", type=float, default=30.0)
    args = ap.parse_args()

    last_note = 0.0
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        done = unlocked()
        if done is not None and TARGET in done:
            day, hour, mode = clock()
            print(json.dumps({"event": "achievement_unlocked", "achievement": TARGET,
                              "day": day, "hour": hour, "mode": mode,
                              "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                             ensure_ascii=False), flush=True)
            return 0
        now = time.time()
        if now - last_note >= 300:
            last_note = now
            day, hour, mode = clock()
            print(json.dumps({"event": "progress", "day": day, "hour": hour, "mode": mode,
                              "at": datetime.datetime.now().strftime("%H:%M:%S")}, ensure_ascii=False),
                  flush=True)
            if mode not in ("TIME_LIMIT_V2", "?"):
                print(json.dumps({"event": "rush_ended_without_achievement", "mode": mode},
                                 ensure_ascii=False), flush=True)
                return 2
        time.sleep(args.interval)
    print(json.dumps({"event": "timeout"}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
