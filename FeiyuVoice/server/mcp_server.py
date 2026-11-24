"""大肥鱼语音 MCP 服务（stdio）：让 DSH / Codex / Claude Desktop 等任何 MCP 客户端直接调用。

启动（客户端配置里写）：
  E:\\feiyu\\.venv\\Scripts\\python.exe E:\\feiyu\\server\\mcp_server.py

Windows 上的 MCP 客户端通过 http://127.0.0.1:9880 访问网关。speak 默认按需启动
feiyu-voice-svc 容器。可用环境变量 FEIYU_URL / FEIYU_API_KEY 配置地址与鉴权。

工具：
  speak(text, voice="cute", speed=1.0, wait=true)  生成语音，wait=true 直接在本机扬声器播放并返回本地 wav 路径
  list_voices()                                    列出可用声线
  job_status(job_id)                               查询异步任务
  cancel_job(job_id)                               取消异步任务
  health()                                         服务健康状态

协议：MCP stdio（换行分隔 JSON-RPC 2.0），只实现 tools 能力。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# MCP 走 stdio，强制 UTF-8，避免 Windows 控制台代码页把中文写坏
for _stream in (sys.stdin, sys.stdout):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

BASE_URL = os.environ.get("FEIYU_URL", "http://127.0.0.1:9880").rstrip("/")
API_KEY = os.environ.get("FEIYU_API_KEY", "").strip()
OUT_DIR = os.environ.get("FEIYU_OUT_DIR", r"E:\feiyu\outputs\mcp")
# speak 生成完是否直接在本机扬声器播放。默认开；设 FEIYU_AUTOPLAY=0 关掉（只要文件）。
AUTOPLAY = os.environ.get("FEIYU_AUTOPLAY", "1").strip().lower() not in ("0", "false", "no", "off")
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "speak",
        "description": "用大肥鱼的声音说一段话。两条声线："
        "voice=cute（可爱，默认；适合日常聊天、活泼回应、游戏陪玩，检查点 F6+N01）／"
        "voice=gentle（温柔；适合轻声陪伴、安慰、睡前，原版 LLM+N10）。"
        "wait=true 返回所选 WAV 或 MP3 的本地路径，wait=false 返回 job_id 供 job_status 轮询。"
        "优先复用当前网关。冷启动默认使用 feiyu-voice-svc 容器内的 PyTorch（container-torch，vLLM 关闭）。"
        "docker start 失败时尝试 Windows 原生网关（windows-native）。容器已启动但就绪超时会返回错误。"
        "返回值里的 runtime 字段标明本次语音由哪套引擎生成；想先确认可调用 health。"
        "引擎不同速度差别明显（容器 RTF≈0.66，Windows≈1.49），容器不可用时请向用户说明。"
        "游戏时建议只发 10-20 字短句（模型常驻约占 3.4GB 显存）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要说的话（中文自然语句；游戏时建议 10-20 字）"},
                "voice": {
                    "type": "string",
                    "enum": ["cute", "gentle"],
                    "default": "cute",
                    "description": "cute=可爱（默认，日常/陪玩）；gentle=温柔（安慰/睡前）",
                },
                "speed": {"type": "number", "default": 1.0, "description": "语速倍率；0.9-0.95 更自然，1.0 原速"},
                "wait": {"type": "boolean", "default": True, "description": "true 等生成完成返回路径；false 返回 job_id"},
                "format": {"type": "string", "enum": ["wav", "mp3"], "default": "wav"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_voices",
        "description": "列出两条声线的元信息：id、检查点、参考音频、适用场景、推荐语速。不加载模型。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "job_status",
        "description": "查询异步语音任务状态（queued/running/success/failed/cancelled）。",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    },
    {
        "name": "cancel_job",
        "description": "按 job_id 请求取消异步任务。正在合成的任务会在音频分块之间检查取消标记。",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    },
    {
        "name": "health",
        "description": "检查语音服务是否就绪，并返回当前实际在服务的引擎："
        "runtime=container-torch（常驻容器，默认）/ windows-native（回退的 Windows 原生）/ stopped（未运行）。"
        "说话前可用它确认加载的是哪套引擎；不加载模型。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _req(method: str, path: str, body: dict | None = None, timeout: int = 600) -> tuple[int, bytes, str]:
    url = BASE_URL + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if API_KEY:
        req.add_header("Authorization", f"Bearer {API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")
    except (urllib.error.URLError, ConnectionError, OSError):
        return 0, b"", ""  # 网关未启动


def _gateway_alive() -> bool:
    code, _, _ = _req("GET", "/health", timeout=5)
    return code == 200


# 语音引擎部署方式：
#   docker —— 常驻容器 feiyu-voice-svc（推荐，速度快；容器停了用 docker start 拉起）
#   process —— Windows 原生进程（默认 py 环境，慢一些但内存归还干净）
AUTOSTART = os.environ.get("FEIYU_AUTOSTART", "docker")
CONTAINER = os.environ.get("FEIYU_CONTAINER", "feiyu-voice-svc")


def _docker_start_container() -> bool:
    try:
        r = subprocess.run(
            ["docker", "start", CONTAINER],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def current_runtime() -> str:
    """返回当前实际在服务的 runtime（container-vllm / container-torch / windows-native / stopped）。"""
    code, body, _ = _req("GET", "/health", timeout=5)
    if code != 200:
        return "stopped"
    try:
        return json.loads(body.decode("utf-8", "replace")).get("runtime", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


SPAWN_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway.spawn.lock")


def _try_claim_spawn() -> bool:
    """用独占锁保证同一时刻只有一个 MCP 客户端去拉起网关，避免重复加载 6GB 模型。"""
    now = time.time()
    try:
        if os.path.exists(SPAWN_LOCK) and now - os.path.getmtime(SPAWN_LOCK) > 300:
            os.remove(SPAWN_LOCK)  # 过期锁（上次启动失败/崩溃）
        fd = os.open(SPAWN_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _release_spawn_lock() -> None:
    try:
        os.remove(SPAWN_LOCK)
    except OSError:
        pass


def _wait_ready(deadline: float) -> bool:
    while time.time() < deadline:
        time.sleep(2)
        code, body, _ = _req("GET", "/health", timeout=5)
        if code == 200 and b'"ready":true' in body.replace(b" ", b""):
            return True
    return False


def _ensure_gateway(wait_s: int = 300) -> bool:
    """网关没起就自动拉起：优先 docker start 常驻容器，失败再回退 Windows 原生进程。"""
    if _gateway_alive():
        return True
    deadline = time.time() + wait_s
    if not _try_claim_spawn():
        # 别的客户端正在拉起，等它就绪即可
        return _wait_ready(min(deadline, time.time() + wait_s))
    try:
        if AUTOSTART != "process":
            if _docker_start_container():
                if _wait_ready(deadline):
                    return True
                # 容器启动失败或没就绪：不再回退到进程，避免同一张卡加载两份模型
                return False
        py = os.environ.get("FEIYU_PYTHON", r"E:\feiyu\.venv\Scripts\python.exe")
        gateway = os.environ.get("FEIYU_GATEWAY", r"E:\feiyu\server\gateway.py")
        port = str(urllib.parse.urlparse(BASE_URL).port or 9880)
        flags = 0
        if sys.platform == "win32":
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_gateway_autostart.log")
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [py, "-u", gateway, "--port", port, "--preload"],
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                cwd=os.path.dirname(gateway),
                creationflags=flags,
            )
        return _wait_ready(deadline)
    finally:
        _release_spawn_lock()


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": False}


def _err(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": True}


def _play_wav(path: str) -> str:
    """在本机默认音频设备同步播放 wav（仅 Windows）。返回 played / disabled / 原因。"""
    if not AUTOPLAY:
        return "disabled(FEIYU_AUTOPLAY)"
    if os.name != "nt":
        return "skipped(non-windows)"
    try:
        import winsound
    except Exception as exc:  # noqa: BLE001
        return f"skipped(no-winsound: {exc})"
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME)  # 同步，播完才返回
        return "played"
    except Exception as exc:  # noqa: BLE001
        return f"play_failed({type(exc).__name__}: {exc})"


def call_tool(name: str, args: dict) -> dict:
    # 只有真正要合成语音时才自动拉起网关（模型加载会占用约 6GB 内存 + 3.4GB 显存）。
    # 查询类工具只探测状态，不触发加载。
    if name == "health":
        code, body, _ = _req("GET", "/health", timeout=10)
        if code == 0:
            return _text(json.dumps({"status": "stopped", "runtime": "stopped", "ready": False, "hint": "网关不可达；调用 speak 会尝试拉起"}, ensure_ascii=False))
        if code != 200:
            return _err(f"健康检查返回 HTTP {code}")
        return _text(body.decode("utf-8", "replace"))

    if name == "list_voices":
        code, body, _ = _req("GET", "/health", timeout=10)
        if code == 200 and json.loads(body).get("ready"):
            code, body, _ = _req("GET", "/v1/voices", timeout=10)
            if code == 200:
                return _text(body.decode("utf-8", "replace"))
        return _text(json.dumps({"data": [
            {"id": "feiyu-cute", "style": "cute", "name": "可爱", "candidate": "F6",
             "checkpoint": "models/feiyu_cute_F6_0040.pt", "reference_audio": "references/cute_N01.wav",
             "sample_rate": 24000, "recommended_speed": 1.0,
             "best_for": "日常聊天、活泼回应、游戏陪玩默认",
             "description": "F6 微调检查点（累计 40 次更新）+ N01 参考音频"},
            {"id": "feiyu-gentle", "style": "gentle", "name": "温柔", "candidate": "C3",
             "checkpoint": "models/Fun-CosyVoice3-0.5B-2512/llm.pt", "reference_audio": "references/gentle_N10.wav",
             "sample_rate": 24000, "recommended_speed": 1.0,
             "best_for": "轻声陪伴、安慰、睡前语气",
             "description": "原版预训练 LLM + N10 参考音频（零样本音色控制）"},
        ]}, ensure_ascii=False))

    if name in ("job_status", "cancel_job"):
        jid = str(args.get("job_id", "")).strip()
        path = f"/v1/jobs/{jid}" if name == "job_status" else f"/v1/jobs/{jid}/cancel"
        code, body, _ = _req("POST" if name == "cancel_job" else "GET", path, {} if name == "cancel_job" else None)
        return _text(body.decode("utf-8", "replace") if code == 200 else f"HTTP {code}")

    if name == "speak":
        text = str(args.get("text", "")).strip()
        if not text:
            return _err("text 不能为空")
        if not _ensure_gateway():
            return _err(
                "语音网关启动失败。排查：`docker start feiyu-voice-svc` 是否成功；"
                "或设置 FEIYU_AUTOSTART=process 回退 Windows 原生。日志：E:\\feiyu\\server\\mcp_gateway_autostart.log"
            )
        runtime = current_runtime()  # 本次说话实际加载的是容器还是 Windows 原生
        voice = str(args.get("voice", "cute"))
        speed = float(args.get("speed", 1.0))
        fmt = str(args.get("format", "wav"))
        wait = bool(args.get("wait", True))
        if not wait:
            code, body, _ = _req("POST", "/v1/speech", {"text": text, "style": voice, "speed": speed, "format": fmt})
            if code in (200, 202):
                try:
                    payload = json.loads(body.decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    payload = {"raw": body.decode("utf-8", "replace")}
                payload["runtime"] = runtime
                return _text(json.dumps(payload, ensure_ascii=False))
            return _err(f"HTTP {code}: {body[:300]!r}")

        code, body, ctype = _req("POST", "/v1/audio/speech", {"input": text, "voice": voice, "speed": speed, "response_format": fmt})
        if code != 200:
            return _err(f"HTTP {code}: {body[:300]!r}")
        os.makedirs(OUT_DIR, exist_ok=True)
        ext = "mp3" if fmt == "mp3" else "wav"
        path = os.path.join(OUT_DIR, f"mcp_{time.strftime('%Y%m%d-%H%M%S')}_{voice}.{ext}")
        with open(path, "wb") as f:
            f.write(body)
        played = _play_wav(path) if ext == "wav" else "skipped(non-wav)"
        return _text(
            json.dumps(
                {
                    "audio_path": path,
                    "voice": voice,
                    "runtime": runtime,
                    "engine_hint": f"本次请求的网关运行时为 {runtime}",
                    "bytes": len(body),
                    "content_type": ctype,
                    "played": played,
                },
                ensure_ascii=False,
            )
        )

    return _err(f"未知工具: {name}")


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "feiyu-voice", "version": "0.1.0"},
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = call_tool(str(params.get("name", "")), params.get("arguments") or {})
        except Exception as exc:  # noqa: BLE001
            result = _err(f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
