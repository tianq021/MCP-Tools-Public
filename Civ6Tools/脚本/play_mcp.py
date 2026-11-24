"""本地 HTTP 桥（127.0.0.1:8766）+ 事件哨兵后台轮询。

职责
====
1. 持有一条常驻 MCP stdio 会话（civ6-mcp），把工具调用暴露成 HTTP：
       POST /call  {"name": ..., "arguments": {...}}
       GET  /tools
2. **事件哨兵**：后台每 ~1.5s 用一条 `run_lua` 探针抓游戏状态
   （见 脚本\\civwatch_probe.lua + watch_core.py），差分出新事件：
       GET /inbox?since=<seq>&min=P0|P1|P2   拉未读事件（不阻塞）
       GET /wait?timeout=600&min=P0          守夜：事件一到立刻返回
       GET /status                           健康状态（游戏/轮询/seq）
3. 事件同时追加到 `logs\\watch_events.jsonl`。

启动（统一走 E:\\Civ6 根目录）：
    E:\\Civ6\\civ6-mcp\\.venv\\Scripts\\python.exe 脚本\\play_mcp.py

环境变量：
    CIV_WATCH_INTERVAL   轮询间隔秒，默认 1.5
    CIV_WATCH_DISABLED   设为 1 关闭哨兵（只留工具桥）
"""

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import uvicorn

ROOT = Path(__file__).resolve().parent.parent   # E:\Civ6（脚本/ 的上一级）
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

sys.path.insert(0, str(SCRIPT_DIR))
import watch_core  # noqa: E402  （脚本\watch_core.py）

WATCH_INTERVAL = float(os.environ.get("CIV_WATCH_INTERVAL", "1.5"))
WATCH_OFFLINE_INTERVAL = float(os.environ.get("CIV_WATCH_OFFLINE_INTERVAL", "8"))
WATCH_DISABLED = os.environ.get("CIV_WATCH_DISABLED", "").lower() in ("1", "true", "yes")
EVENTS_LOG = LOG_DIR / "watch_events.jsonl"


def _result_text(data: dict) -> tuple[str, bool]:
    """把 call_tool 的 model_dump 结果转成 (文本, 是否错误)。"""
    text = "\n".join(c.get("text", "") for c in data.get("content", []) if isinstance(c, dict))
    return text, bool(data.get("isError"))


async def _call_tool(app, name: str, arguments: dict) -> tuple[str, bool]:
    async with app.state.lock:
        result = await app.state.session.call_tool(name, arguments)
    data = result.model_dump(mode="json", exclude_none=True)
    return _result_text(data)


async def _watch_loop(app) -> None:
    """后台哨兵：定时跑探针，喂 EventBox，事件落盘。

    在线时按 WATCH_INTERVAL（~1.5s）轮询；离线时退避到 WATCH_OFFLINE_INTERVAL，
    避免游戏没开时刷爆 MCP 日志。
    """
    probe = watch_core.load_probe()
    box: watch_core.EventBox = app.state.box
    while True:
        try:
            text, is_error = await _call_tool(app, "run_lua", {"code": probe, "context": "ingame"})
            events = box.feed(text, is_error=is_error)
            if events:
                watch_core.events_to_jsonl(events, EVENTS_LOG)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 桥/连接层错误：按离线处理，不退出
            app.state.watch_error = f"{type(exc).__name__}: {exc}"
            box.feed("", is_error=True)
        online = bool(box.state and box.state.online)
        await asyncio.sleep(WATCH_INTERVAL if online else WATCH_OFFLINE_INTERVAL)


@asynccontextmanager
async def lifespan(app):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               CIV_MCP_WEB_PORT="0")
    params = StdioServerParameters(
        command=str(ROOT / "civ6-mcp/.venv/Scripts/civ-mcp.exe"),
        cwd=str(ROOT / "civ6-mcp"), env=env)
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "play_mcp_server.log").open("a", encoding="utf-8") as err:
        async with stdio_client(params, errlog=err) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                app.state.session = session
                app.state.lock = asyncio.Lock()
                app.state.box = watch_core.EventBox()
                app.state.watch_error = ""
                app.state.watch_task = None
                if not WATCH_DISABLED:
                    app.state.watch_task = asyncio.create_task(_watch_loop(app), name="civ-watch")
                try:
                    yield
                finally:
                    if app.state.watch_task:
                        app.state.watch_task.cancel()
                        try:
                            await app.state.watch_task
                        except (asyncio.CancelledError, Exception):
                            pass


app = FastAPI(lifespan=lifespan)


@app.get("/tools")
async def tools():
    result = await app.state.session.list_tools()
    return [t.model_dump(mode="json", exclude_none=True) for t in result.tools]


@app.post("/call")
async def call(request: dict):
    name = request.get("name")
    if not isinstance(name, str):
        raise HTTPException(400, "name is required")
    async with app.state.lock:
        result = await app.state.session.call_tool(name, request.get("arguments", {}))
        data = result.model_dump(mode="json", exclude_none=True)
        with (LOG_DIR / "play_mcp_calls.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"name": name, "arguments": request.get("arguments", {}),
                                  "result": data}, ensure_ascii=False) + "\n")
        return data


# ---------------------------------------------------------------------------
# 事件哨兵 API
# ---------------------------------------------------------------------------


@app.get("/status")
async def status():
    box: watch_core.EventBox = app.state.box
    data = box.status()
    data.update({
        "interval_s": WATCH_INTERVAL,
        "disabled": WATCH_DISABLED,
        "watch_error": getattr(app.state, "watch_error", ""),
        "events_log": str(EVENTS_LOG),
    })
    return {"game": box.state.summary() if box.state else None, "watcher": data}


@app.get("/inbox")
async def inbox(since: int = 0, min_severity: str = Query("P2", alias="min")):
    """未读事件（不阻塞）。since 用上次返回的 seq；min 为最低严重级别。"""
    return app.state.box.snapshot(since=since, min_severity=min_severity)


@app.get("/wait")
async def wait(timeout: float = 300, min_severity: str = Query("P0", alias="min"),
               since: int | None = None):
    """守夜：等到 seq>since 且级别<=min 的事件，或超时。

    since 省略时取「请求到达时」的 seq —— 即只等新事件，不会立刻吐出历史事件。
    """
    box: watch_core.EventBox = app.state.box
    if since is None:
        since = box.seq
    deadline = time.monotonic() + max(1.0, min(float(timeout), 1800.0))
    while True:
        events = box.blocking_events(since=since, min_severity=min_severity)
        if events:
            return {
                "seq": box.seq,
                "events": [e.to_dict() for e in events],
                "state": box.state.summary() if box.state else None,
                "watcher": box.status(),
                "timed_out": False,
            }
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.35)
    return {
        "seq": box.seq,
        "events": [],
        "state": box.state.summary() if box.state else None,
        "watcher": box.status(),
        "timed_out": True,
    }


@app.post("/inject")
async def inject(request: dict):
    """仅测试用：注入一个假事件，验证 /wait 与 DSH 唤醒链路（不经过游戏）。

    用法：POST /inject {"severity":"P0","type":"TEST_EVENT","message":"...","hint":"..."}
    """
    box: watch_core.EventBox = app.state.box
    event = box.inject(
        severity=request.get("severity", "P0"),
        etype=request.get("type", "TEST_EVENT"),
        message=request.get("message", "测试事件"),
        hint=request.get("hint", "（测试用，忽略）"),
    )
    watch_core.events_to_jsonl([event], EVENTS_LOG)
    return {"seq": box.seq, "event": event.to_dict()}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="warning")
