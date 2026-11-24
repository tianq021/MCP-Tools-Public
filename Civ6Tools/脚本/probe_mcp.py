r"""Probe the civ6-mcp MCP server over stdio.

Does NOT need Civilization VI to be running: it only checks that the
server process starts, speaks JSON-RPC, and advertises its tools.

Run:  C:\Python312\python.exe E:\Civ6\脚本\probe_mcp.py
"""

import json
import os
import queue
import subprocess
import sys
import threading

REPO = r"E:\Civ6\civ6-mcp"
EXE = os.path.join(REPO, r".venv\Scripts\civ-mcp.exe")

env = dict(os.environ)
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    [EXE],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    errors="replace",
    bufsize=1,
    env=env,
    cwd=REPO,
)

lines: "queue.Queue[str | None]" = queue.Queue()
err_chunks: list[str] = []


def _pump_out() -> None:
    assert proc.stdout is not None
    for raw in proc.stdout:
        lines.put(raw)
    lines.put(None)


def _pump_err() -> None:
    assert proc.stderr is not None
    for raw in proc.stderr:
        err_chunks.append(raw)


threading.Thread(target=_pump_out, daemon=True).start()
threading.Thread(target=_pump_err, daemon=True).start()


def send(obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def recv(timeout: float = 40.0):
    try:
        raw = lines.get(timeout=timeout)
    except queue.Empty:
        return None
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return recv(timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_unparsed": raw}


summary: dict = {}

# --- 1. initialize -------------------------------------------------
send(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "dsh-probe", "version": "0.1"},
        },
    }
)
init = recv(60)
if init and "result" in init:
    info = init["result"].get("serverInfo", {})
    summary["handshake"] = "OK"
    summary["server"] = f"{info.get('name')} v{info.get('version')}"
    summary["protocol"] = init["result"].get("protocolVersion")
else:
    summary["handshake"] = "FAILED"
    summary["initialize_response"] = init

# --- 2. initialized notification -----------------------------------
if summary.get("handshake") == "OK":
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # --- 3. tools/list ---------------------------------------------
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    resp = recv(60)
    if resp and "result" in resp:
        tools = resp["result"].get("tools", [])
        summary["tool_count"] = len(tools)
        names = sorted(t.get("name", "?") for t in tools)
        summary["tools_sample"] = names[:12]
        summary["has_end_turn"] = "end_turn" in names
        summary["has_unit_action"] = "unit_action" in names
        summary["has_run_lua"] = "run_lua" in names
        summary["has_set_city_production"] = "set_city_production" in names
        summary["has_civ_abilities"] = "get_civ_abilities" in names
    else:
        summary["tool_count"] = "FAILED"
        summary["tools_response"] = resp

# --- cleanup -------------------------------------------------------
try:
    proc.stdin.close()  # type: ignore[union-attr]
except Exception:
    pass
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()

err = "".join(err_chunks).strip()
if err:
    summary["stderr_tail"] = err.splitlines()[-12:]

print(json.dumps(summary, indent=2, ensure_ascii=False))
sys.exit(0 if summary.get("handshake") == "OK" else 1)
