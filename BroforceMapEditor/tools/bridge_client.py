"""BroforceMapEditor 桥接客户端（M0）。

职责：注入 bridge/BroforceMapBridge.dll，通过文件队列下发命令并读回结果。
只使用本地文件与进程注入，不修改游戏安装目录。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import mono_injector  # noqa: E402

PROCESS_NAME = "Broforce_beta.exe"
BRIDGE_DIR = TOOLS_DIR.parent / "bridge"
DATA_DIR = Path(os.environ.get("BROFORCE_DATA_DIR", str(Path.home() / "AppData" / "Local" / "BroforceMapEditor")))
IPC_DIR = DATA_DIR / "ipc"
COMMAND_PATH = IPC_DIR / "command.txt"
RESULT_PATH = IPC_DIR / "result.txt"
HEARTBEAT_PATH = IPC_DIR / "heartbeat.txt"
STATUS_PATH = DATA_DIR / "bridge_status.txt"


class BridgeError(RuntimeError):
    pass


def parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def read_heartbeat() -> dict[str, str] | None:
    try:
        return parse_key_values(HEARTBEAT_PATH.read_text(encoding="utf-8-sig"))
    except OSError:
        return None


def heartbeat_age() -> float:
    try:
        return time.time() - HEARTBEAT_PATH.stat().st_mtime
    except OSError:
        return float("inf")


def bridge_dll() -> Path:
    """构建脚本输出带时间戳的 DLL（游戏加载后旧文件会被锁定），取 dist 里最新的一个。"""
    candidates = sorted((TOOLS_DIR.parent / "dist").glob("BroforceMapBridge*.dll"),
                        key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise BridgeError("桥接 DLL 不存在（先跑 bridge/build.ps1）")
    return candidates[-1]


def inject(pid: int | None = None) -> int:
    if pid is None:
        pid = mono_injector.find_process(PROCESS_NAME)
    mono_injector.inject_managed_bridge(pid, bridge_dll())
    return pid


def wait_ready(timeout: float = 20.0) -> dict[str, str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        beat = read_heartbeat()
        if beat is not None and heartbeat_age() < 2.0:
            return beat
        time.sleep(0.2)
    raise BridgeError("桥接未就绪：没有新的 heartbeat.txt（确认游戏在运行、DLL 已注入）")


def status_text() -> str:
    try:
        return STATUS_PATH.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "<no status>"


def send_command(payload: dict[str, object], timeout: float = 10.0) -> dict[str, str]:
    IPC_DIR.mkdir(parents=True, exist_ok=True)
    if RESULT_PATH.exists():
        RESULT_PATH.unlink()
    lines = [f"{key}={value}" for key, value in payload.items()]
    tmp = IPC_DIR / "command.tmp"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if COMMAND_PATH.exists():
        COMMAND_PATH.unlink()
    tmp.replace(COMMAND_PATH)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if RESULT_PATH.exists():
            return parse_key_values(RESULT_PATH.read_text(encoding="utf-8-sig"))
        time.sleep(0.05)
    raise BridgeError(f"命令超时：{payload}")


def ensure_ready(pid: int | None = None, timeout: float = 20.0) -> dict[str, str]:
    if heartbeat_age() >= 2.0:
        # A minimized game may stop updating. Never install a second polling bridge.
        beat = read_heartbeat()
        if beat and beat.get("pid") == str(mono_injector.find_process(PROCESS_NAME)):
            return wait_ready(timeout)
        inject(pid)
    return wait_ready(timeout)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Broforce 地图桥接客户端")
    parser.add_argument("command", choices=["ping", "state", "inject", "make-test-map", "save-bfg", "load", "list-levels"])
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=32)
    parser.add_argument("--theme", type=int, default=0)
    parser.add_argument("--name", default="")
    parser.add_argument("--author", default="BroforceMapBridge")
    parser.add_argument("--note", default="")
    parser.add_argument("--path", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    try:
        if args.command == "inject":
            pid = inject()
            print(json.dumps({"ok": 1, "pid": pid}, ensure_ascii=False))
            return 0
        if args.command == "ping":
            ensure_ready(timeout=args.timeout)
            print(json.dumps(send_command({"op": "ping"}), ensure_ascii=False, indent=2))
            return 0
        if args.command == "state":
            ensure_ready(timeout=args.timeout)
            print(json.dumps(send_command({"op": "state"}), ensure_ascii=False, indent=2))
            return 0
        if args.command == "make-test-map":
            ensure_ready(timeout=args.timeout)
            result = send_command({"op": "make_test_map", "width": args.width,
                                   "height": args.height, "theme": args.theme,
                                   "name": args.name, "author": args.author,
                                   "description": args.note})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") == "1" else 1
        if args.command == "save-bfg":
            ensure_ready(timeout=args.timeout)
            payload: dict[str, object] = {"op": "save_bfg"}
            if args.path:
                payload["path"] = args.path
            result = send_command(payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") == "1" else 1
        if args.command == "load":
            ensure_ready(timeout=args.timeout)
            result = send_command({"op": "load"})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") == "1" else 1
        if args.command == "list-levels":
            ensure_ready(timeout=args.timeout)
            print(json.dumps(send_command({"op": "list_levels"}), ensure_ascii=False, indent=2))
            return 0
    except (BridgeError, mono_injector.InjectorError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print(f"bridge_status: {status_text()}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
