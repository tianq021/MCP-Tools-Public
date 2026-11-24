from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path


class AgentError(Exception):
    """An actionable error safe to show after redaction."""


DEFAULT = {
    "rounds": 2,
    "max_calls": 6,
    "max_total_tokens": 120000,
    "timeout_seconds": 120,
    "max_prompt_bytes": 90000,
    "max_response_bytes": 1000000,
    "snapshot": {"max_files": 40, "max_file_bytes": 24000,
                 "max_total_bytes": 48000, "include": [], "exclude": []},
    "deepseek": {"model": "deepseek-flash", "max_output_tokens": 2500,
                 "max_usd": 0.25, "input_usd_per_million": 0.3,
                 "output_usd_per_million": 1.2},
    "codex": {"executable": "codex", "model": None, "max_calls": 3},
    # 外部 AI 调用 DSH 的入口参数；max_grant 是由人设定的权限上限。
    "dsh": {"root": "F:/deepseekA", "node": "node", "entry": None, "home": None,
            "workspace": None, "output": None,
            "default_grant": "workspace-write", "max_grant": "workspace-write",
            "timeout_seconds": 900, "max_answer_chars": 200000},
    # 独立 HTTP API：与本机 DSH 网页(3080)和官方 DeepSeek API 完全分开。
    "api": {"host": "127.0.0.1", "port": 3081, "tokens": [],
            "max_concurrency": 2, "max_body_bytes": 65536,
            "requests_per_minute": 60, "max_timeout_s": 3600,
            "allowed_workspace_roots": [],
            "provider_key_env": "DSH_ENTRY_DEEPSEEK_API_KEY",
            "provider_key_file": None, "audit": None},
}


def load_config(path: Path | None = None) -> dict:
    config = copy.deepcopy(DEFAULT)
    if path:
        try:
            incoming = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise AgentError("配置文件无法读取，或不是有效 JSON。") from None
        def merge(dst, src):
            if not isinstance(src, dict):
                raise AgentError("配置必须是 JSON 对象。")
            for key, val in src.items():
                if key not in dst:
                    raise AgentError("配置包含未知字段。请对照 config.example.json。")
                if isinstance(dst[key], dict):
                    merge(dst[key], val)
                else:
                    dst[key] = val
        merge(config, incoming)
    def number(obj, key, low, high, integer=True):
        value = obj[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AgentError(f"配置 {key} 必须是数字。")
        if not math.isfinite(value) or not low <= value <= high or (integer and not isinstance(value, int)):
            raise AgentError(f"配置 {key} 超出允许范围 {low}..{high}。")
    for key, lo, hi in [("rounds", 1, 5), ("max_calls", 2, 30),
                        ("max_total_tokens", 1, 2000000), ("timeout_seconds", 5, 600),
                        ("max_prompt_bytes", 1024, 300000), ("max_response_bytes", 1024, 2000000)]:
        number(config, key, lo, hi)
    for key in ("max_file_bytes", "max_total_bytes"):
        number(config["snapshot"], key, 1, 300000)
    number(config["snapshot"], "max_files", 1, 200)
    for key in ("include", "exclude"):
        value = config["snapshot"][key]
        if not isinstance(value, list) or not all(isinstance(s, str) and len(s) <= 200 for s in value):
            raise AgentError(f"snapshot.{key} 必须是 glob 字符串列表。")
    number(config["deepseek"], "max_output_tokens", 128, 16000)
    for key in ("max_usd", "input_usd_per_million", "output_usd_per_million"):
        number(config["deepseek"], key, 0.000001, 1000, False)
    number(config["codex"], "max_calls", 1, 10)
    for obj, key in [(config["deepseek"], "model"), (config["codex"], "executable")]:
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise AgentError(f"{key} 不得为空。")
    if config["codex"]["model"] is not None and not isinstance(config["codex"]["model"], str):
        raise AgentError("codex.model 必须是字符串或 null。")
    dsh = config["dsh"]
    for key in ("root", "node"):
        if not isinstance(dsh[key], str) or not dsh[key].strip():
            raise AgentError(f"dsh.{key} 不得为空。")
    for key in ("entry", "home", "workspace", "output"):
        if dsh[key] is not None and (not isinstance(dsh[key], str) or not dsh[key].strip()):
            raise AgentError(f"dsh.{key} 必须是非空字符串或 null。")
    grants = ("read-only", "workspace-write", "danger-full-access")
    for key in ("default_grant", "max_grant"):
        if dsh[key] not in grants:
            raise AgentError(f"dsh.{key} 必须是 {', '.join(grants)} 之一。")
    if grants.index(dsh["default_grant"]) > grants.index(dsh["max_grant"]):
        raise AgentError("dsh.default_grant 不能高于 dsh.max_grant。")
    number(dsh, "timeout_seconds", 10, 7200, False)
    number(dsh, "max_answer_chars", 1000, 1000000)
    api = config["api"]
    if api["host"] not in ("127.0.0.1", "localhost", "::1"):
        raise AgentError("api.host 只允许回环地址 127.0.0.1/localhost/::1；不要绑定 0.0.0.0。")
    number(api, "port", 1024, 65535)
    number(api, "max_concurrency", 1, 8)
    number(api, "max_body_bytes", 1024, 1048576)
    number(api, "requests_per_minute", 1, 6000)
    number(api, "max_timeout_s", 10, 7200)
    if not isinstance(api["tokens"], list) or len(api["tokens"]) > 20:
        raise AgentError("api.tokens 必须是数组，且最多 20 项。")
    allowed_fields = {"label", "sha256", "max_grant", "requests_per_minute"}
    labels, digests = set(), set()
    for entry in api["tokens"]:
        if not isinstance(entry, dict) or set(entry) - allowed_fields:
            raise AgentError("api.tokens 每项只能包含 label、sha256、max_grant、requests_per_minute。")
        if not isinstance(entry.get("label"), str) or not 1 <= len(entry["label"]) <= 40:
            raise AgentError("api token 的 label 必须是 1–40 个字符。")
        if entry["label"] in labels:
            raise AgentError("api token 的 label 不得重复。")
        labels.add(entry["label"])
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise AgentError("api token 的 sha256 必须是 64 位十六进制。")
        if digest.lower() in digests:
            raise AgentError("api token 的 sha256 不得重复。")
        digests.add(digest.lower())
        if entry.get("max_grant") not in grants:
            raise AgentError("api token 的 max_grant 必须是合法档位。")
        if "requests_per_minute" in entry:
            number(entry, "requests_per_minute", 1, 6000)
    roots = api["allowed_workspace_roots"]
    if not isinstance(roots, list) or len(roots) > 50 or not all(
            isinstance(r, str) and 0 < len(r) <= 260 for r in roots):
        raise AgentError("api.allowed_workspace_roots 必须是路径字符串数组（最多 50 项）。")
    for key in ("provider_key_env", "provider_key_file", "audit"):
        value = api[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise AgentError(f"api.{key} 必须是非空字符串或 null。")
    if api["provider_key_env"] is None:
        raise AgentError("api.provider_key_env 不得为空。")
    return config
