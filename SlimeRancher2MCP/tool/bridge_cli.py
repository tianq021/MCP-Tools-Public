#!/usr/bin/env python3
"""不走 MCP 的桥接命令行（排障用）。

用法：
    python -B tool\\bridge_cli.py health
    python -B tool\\bridge_cli.py call sr2_gameinfo
    python -B tool\\bridge_cli.py call sr2_saves --arg limit=5
    python -B tool\\bridge_cli.py call sr2_echo --arg text=hello
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call, health  # noqa: E402


def parse_args(argv: list[str]) -> dict:
    out: dict = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--arg":
            if i + 1 >= len(argv) or "=" not in argv[i + 1]:
                raise SystemExit("--arg 需要 name=value 形式")
            name, value = argv[i + 1].split("=", 1)
            out[name] = coerce(value)
            i += 2
            continue
        raise SystemExit(f"未知参数: {tok}")
    return out


def coerce(value: str):
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    try:
        if cmd == "health":
            result = health()
        elif cmd == "call":
            if len(argv) < 2:
                raise SystemExit("call 需要工具名")
            result = call(argv[1], parse_args(argv[2:]))
        else:
            raise SystemExit(f"未知子命令: {cmd}")
    except BridgeError as exc:
        print(json.dumps({"ok": False, "error": {"code": "BRIDGE_UNAVAILABLE", "message": str(exc)}},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
