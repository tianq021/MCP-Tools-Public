"""Slime Rancher bridge HTTP 客户端（MCP server 与 CLI 共用）。

配置与游戏内 mod 共用同一个文件：
    E:\\SlimeRancherMCP\\config\\bridge.json
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

CONFIG_PATH = os.environ.get(
    "SR_BRIDGE_CONFIG", r"E:\SlimeRancherMCP\config\bridge.json"
)


class BridgeError(RuntimeError):
    """通道级错误（进程/端口/鉴权），与游戏内业务错误区分。"""


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise BridgeError(
            f"找不到桥接配置 {CONFIG_PATH}；先运行 plugin\\SlimeRancherBridge\\build.ps1 或启动一次游戏"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _request(method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
    cfg = load_config()
    url = f"http://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 18760)}{path}"
    data = None
    headers = {"X-Bridge-Token": cfg.get("token", "")}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    to = timeout if timeout is not None else (cfg.get("request_timeout_ms", 9000) / 1000.0 + 2.0)
    try:
        with urllib.request.urlopen(req, timeout=to) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:  # 4xx/5xx 也带 JSON 体
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            raise BridgeError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise BridgeError(
            f"连不上桥接 {url}（{e.reason}）。检查：游戏是否在运行、mod 是否加载、端口是否被占用"
        ) from e
    except TimeoutError as e:
        raise BridgeError(f"请求超时 {url}") from e


def health() -> dict:
    return _request("GET", "/health")


def call(tool: str, args: dict | None = None, request_id: str | None = None) -> dict:
    body = {
        "tool": tool,
        "args": args or {},
    }
    if request_id is not None:
        body["request_id"] = request_id
    return _request("POST", "/rpc", body)
