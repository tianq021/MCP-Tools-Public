"""Read-only local client check. Never creates a conversation or calls a model."""
import http.client
import json
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parent
state = json.loads((ROOT / "runtime/logs/server.json").read_text(encoding="utf-8-sig"))
log = Path(state["stdout"]).read_text(encoding="utf-8-sig")
token = re.findall(r"http://127\.0\.0\.1:3096/\?token=([A-Za-z0-9._~-]+)", log)[-1]

def request(method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", 3096, timeout=10)
    try:
        conn.request(method, path, headers=headers or {}, body=body)
        response = conn.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        conn.close()

status, headers, _ = request("GET", "/?token=" + token)
assert status in (200, 302, 303), status
cookie = "; ".join(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie")
assert cookie

def rpc(method, args):
    rpc_id = str(uuid.uuid4())
    status, _, raw = request("POST", "/api/" + method, {
        "Cookie": cookie, "Origin": "http://127.0.0.1:3096",
        "Content-Type": "application/json"
    }, json.dumps({"type": "client-request", "rpcId": rpc_id,
                  "method": method, "payload": {"args": args}}).encode())
    reply = json.loads(raw)
    assert status == 200 and reply["rpcId"] == rpc_id and reply["result"]["ok"], reply
    return reply["result"]["value"]

sessions = rpc("session/list", {"_request": {}})
catalog = rpc("session/modelCatalog", {})
presets = rpc("agentPresets/list", {})
assert catalog["default"]["provider"] == "ollama-local"
assert catalog["default"]["model"] == "gemma4:e4b"
assert catalog["default"]["reasoningEffort"] == "medium"
assert catalog["routableProviders"] == ["ollama-local"]
assert not catalog["failures"]
gemma = next(m for g in catalog["groups"] for m in g["models"] if m["id"] == "gemma4:e4b")
assert gemma.get("reasoning"), "Gemma4 thinking capability is missing from the live catalog"
assert any(p["id"] == "civ6-local" and p["isDefault"] for p in presets["presets"])
report = {"authenticated_api": "passed", "active_sessions": len(sessions["items"]),
          "model_catalog": catalog, "presets": presets,
          "inference_sent": False, "game_started": False}
(ROOT / "evidence/client-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
