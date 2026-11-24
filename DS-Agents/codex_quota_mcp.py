"""Codex 额度查询 MCP 工具。

设计原则：进程内部可以读到完整响应，但**只有“剩余额度”会返回给模型**。
账户 ID、credit ID、计划类型、原始 JSON 等一律不进入模型上下文。
"""

import json
import os
import subprocess
from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("codex-quota")

# primary/secondary 是槽位，不代表固定时长。
WINDOW_LABELS = {300: "5 小时", 10080: "每周"}


def _window_label(key, window, window_count):
    minutes = window.get("windowDurationMins")
    try:
        duration = float(minutes)
    except (TypeError, ValueError):
        duration = 0
    if duration > 0:
        return WINDOW_LABELS.get(duration, f"{minutes} 分钟")
    # 用户约定：只返回一个数值且缺少时长时，按周额度处理。
    if window_count == 1:
        return "每周"
    return {"primary": "5 小时", "secondary": "每周"}[key]


def _read_codex_limits():
    """运行 codex app-server 并读取额度。仅供内部使用，不直接返回给模型。"""
    exe = os.environ.get("CODEX_EXE", "codex")
    proc = subprocess.Popen(
        [exe, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def receive(request_id):
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("codex app-server exited")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("codex app-server error")
                return message.get("result", {})

    try:
        send({
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "codex-quota-mcp",
                    "title": "Codex Quota",
                    "version": "1.0.0",
                }
            },
        })
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"method": "account/rateLimits/read", "id": 2})
        return receive(2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _iter_windows(result):
    buckets = result.get("rateLimitsByLimitId") or {}
    if not buckets and result.get("rateLimits"):
        limit = result["rateLimits"]
        buckets = {limit.get("limitId", "codex"): limit}

    for bucket in buckets.values():
        windows = [
            (key, bucket[key])
            for key in ("primary", "secondary")
            if bucket.get(key) and bucket[key].get("usedPercent") is not None
        ]
        for key, window in windows:
            yield _window_label(key, window, len(windows)), window


def _percent(remaining):
    if remaining < 10:
        return f"{remaining:.1f}".rstrip("0").rstrip(".")
    return f"{remaining:.0f}"


def _reset_text(window):
    value = window.get("resetsAt")
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return None


def _remaining_summary(result):
    lines = []
    for label, window in _iter_windows(result):
        remaining = max(0.0, 100 - float(window["usedPercent"]))
        text = f"{label}窗口剩余 {_percent(remaining)}%"
        reset = _reset_text(window)
        if reset:
            text += f"（{reset} 重置）"
        lines.append(text)
    return lines


def _quota_line() -> str:
    """对外唯一出口：只给出剩余比例与重置时间。"""
    try:
        lines = _remaining_summary(_read_codex_limits())
    except Exception:
        return "Codex 剩余额度：查询失败，稍后重试。"

    if not lines:
        return "Codex 剩余额度：暂未获取到窗口信息。"

    return "Codex 剩余额度：" + "；".join(lines)


@mcp.tool()
def get_codex_quota() -> str:
    """查询当前 Codex 账户的剩余额度。

    返回各额度窗口的剩余百分比和重置时间，不返回账户、凭证或原始响应。
    调用成本低，可在需要判断剩余额度时随时使用。
    """
    return _quota_line()


if __name__ == "__main__":
    import sys

    if "--brief" in sys.argv[1:]:
        print(_quota_line())
    else:
        mcp.run()
