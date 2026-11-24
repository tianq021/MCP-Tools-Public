"""直接通过 stdio 测试 MCP 服务（UTF-8 正确编码，不经过 PowerShell 管道）。"""
from __future__ import annotations

import json
import subprocess
import sys
import time

PY = r"E:\feiyu\.venv\Scripts\python.exe"
SERVER = r"E:\feiyu\server\mcp_server.py"


def main() -> int:
    proc = subprocess.Popen(
        [PY, "-u", SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def recv(timeout: float = 900.0) -> dict:
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read().decode("utf-8", "replace")[-2000:]
            raise RuntimeError(f"MCP 无响应；stderr:\n{err}")
        return json.loads(line.decode("utf-8"))

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
    print("[init]", recv(30))
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = recv(30)
    print("[tools]", [t["name"] for t in tools["result"]["tools"]])

    t0 = time.perf_counter()
    send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "health", "arguments": {}}})
    print(f"[health] {recv(300)['result']['content'][0]['text']}  ({time.perf_counter() - t0:.1f}s)")

    t0 = time.perf_counter()
    send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "主人，语音工具已经接好了，我现在可以直接跟你说话。", "voice": "cute", "wait": True}}})
    resp = recv(900)
    print(f"[speak] ({time.perf_counter() - t0:.1f}s) {resp['result']}")
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
