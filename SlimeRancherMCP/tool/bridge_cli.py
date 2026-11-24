"""桥接 HTTP 的命令行客户端（不经过 MCP，方便调试）。

用法：
    python tool\\bridge_cli.py health
    python tool\\bridge_cli.py ping
    python tool\\bridge_cli.py state
    python tool\\bridge_cli.py call sr_echo --arg text=hello
    python tool\\bridge_cli.py raw-post /rpc '{"tool":"sr_ping"}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call, health, load_config  # noqa: E402


def parse_args(pairs: list[str]) -> dict:
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"参数格式应为 key=value: {pair}")
        key, value = pair.split("=", 1)
        if value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        else:
            try:
                out[key] = int(value)
            except ValueError:
                try:
                    out[key] = float(value)
                except ValueError:
                    out[key] = value
    return out


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Slime Rancher bridge CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    sub.add_parser("ping")
    sub.add_parser("state")
    sub.add_parser("config")

    p_call = sub.add_parser("call", help="调用任意工具")
    p_call.add_argument("tool")
    p_call.add_argument("--arg", action="append", default=[], help="key=value，可重复")
    p_call.add_argument("--request-id", help="执行写工具时使用，重试保留原值")

    p_raw = sub.add_parser("raw-post", help="直接 POST 一个 JSON（调试协议用）")
    p_raw.add_argument("path")
    p_raw.add_argument("body")

    args = parser.parse_args()
    try:
        if args.cmd == "config":
            dump(load_config())
        elif args.cmd == "health":
            dump(health())
        elif args.cmd == "ping":
            dump(call("sr_ping"))
        elif args.cmd == "state":
            dump(call("sr_state"))
        elif args.cmd == "call":
            dump(call(args.tool, parse_args(args.arg), args.request_id))
        elif args.cmd == "raw-post":
            import urllib.request

            cfg = load_config()
            url = f"http://{cfg['host']}:{cfg['port']}{args.path}"
            req = urllib.request.Request(
                url,
                data=args.body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-Bridge-Token": cfg["token"],
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(resp.read().decode("utf-8", "replace"))
    except BridgeError as exc:
        print(f"BRIDGE ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
