"""本项目的独立 HTTP API：与 DSH 网页(3080)和官方 DeepSeek API 相互隔离。

隔离点：

* 端口与路径：``127.0.0.1:3081``、``/dsh/v1/*``，不复用 DSH 网页的 ``/api``；
* 凭证：多 Bearer token（只存 sha256），与 DSH 网页 token、DeepSeek Key 不同；
* 权限：每个 token 有自己的 ``max_grant`` 与速率，永不高于配置总上限；
* 计费：可只为本服务注入项目专属 ``DEEPSEEK_API_KEY``（环境变量或仓库外文件），
  3080 仍用 DSH 自己的凭证；
* 记录：``runs/api/audit.jsonl``，不含任务正文与任何凭证明文。

用法::

    python -X utf8 -B scripts/dsh_api.py --new-token
    python -X utf8 -B scripts/dsh_api.py --hash-token
    python -X utf8 -B scripts/dsh_api.py --config config.dsh.json
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from getpass import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import AgentError, load_config
from .dsh_entry import (PROJECT_ROOT, DshProcess, grant_rank, locate_dsh,
                        prepare_run, validate_grant)
from .security import Redactor

VERSION = "1.0.0"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_TOKEN_LEN = 512
_TASK_RE = re.compile(r"/dsh/v1/tasks/([A-Za-z0-9-]{1,64})")
_CANCEL_RE = re.compile(r"/dsh/v1/tasks/([A-Za-z0-9-]{1,64})/cancel")
_EVENTS_RE = re.compile(r"/dsh/v1/tasks/([A-Za-z0-9-]{1,64})/events")
_PAYLOAD_FIELDS = {"task", "workspace", "grant", "timeout_s", "session_id"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def sha256_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_provider_key(api: dict) -> str:
    """返回凭证来源；若配置了项目专属 Key，只写入本进程环境。

    返回值只用于健康检查展示，绝不返回 Key 本身。
    """

    env_name = api["provider_key_env"]
    value = (os.environ.get(env_name) or "").strip()
    source = "project_env" if value else None
    if not value and api.get("provider_key_file"):
        path = Path(api["provider_key_file"]).expanduser()
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            raise AgentError("无法读取 api.provider_key_file 指向的项目专属 Key 文件。") from None
        source = "project_file"
    if value:
        if not value.startswith("sk-") or any(ch.isspace() for ch in value):
            raise AgentError("项目专属 DeepSeek Key 格式不可识别，已停止；未记录 Key 内容。")
        os.environ["DEEPSEEK_API_KEY"] = value
        return source or "project_env"
    return "dsh_home"


def _dsh_version(location) -> str | None:
    try:
        import subprocess
        completed = subprocess.run(
            [location.node, str(location.entry), "--version"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "DSH_HOME": str(location.home)},
        )
        return (completed.stdout or completed.stderr).strip()[:80] or None
    except Exception:
        return None


class ApiJob:
    __slots__ = ("id", "process", "token_label", "grant", "workspace", "created", "released", "written")

    def __init__(self, job_id: str, process: DshProcess, token_label: str):
        self.id = job_id
        self.process = process
        self.token_label = token_label
        self.grant = process.grant
        self.workspace = str(process.workspace)
        self.created = time.time()
        self.released = False
        self.written = False


class ApiState:
    def __init__(self, config: dict):
        self.config = config
        self.settings = config["dsh"]
        self.api = config["api"]
        self.tokens = list(self.api["tokens"])
        if not self.tokens:
            raise AgentError("api.tokens 为空：请先用 --new-token 生成并写入配置，避免无鉴权启动。")
        self.redactor = Redactor()
        self.provider_key_source = resolve_provider_key(self.api)
        self.location = locate_dsh(root=self.settings.get("root"), entry=self.settings.get("entry"),
                                   home=self.settings.get("home"), node=self.settings.get("node"))
        self.dsh_version = _dsh_version(self.location)
        self.jobs: dict[str, ApiJob] = {}
        self.lock = threading.RLock()
        self.audit_lock = threading.Lock()
        self.semaphore = threading.BoundedSemaphore(self.api["max_concurrency"])
        self.running = 0
        self.rate: dict[str, deque] = {}
        self.started = time.time()
        output = Path(self.settings.get("output") or PROJECT_ROOT / "runs")
        self.audit_path = Path(self.api.get("audit") or output / "api" / "audit.jsonl")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.conversation_dir = self.audit_path.parent / "conversations"
        self.conversation_dir.mkdir(parents=True, exist_ok=True)

    # -- 鉴权与限流 ---------------------------------------------------------

    def authenticate(self, header: str | None) -> dict | None:
        if not header or not header.lower().startswith("bearer "):
            return None
        token = header[7:].strip()
        if not token or len(token) > MAX_TOKEN_LEN:
            return None
        digest = sha256_token(token)
        for entry in self.tokens:
            if hmac.compare_digest(entry["sha256"].lower(), digest):
                return entry
        return None

    def check_rate(self, entry: dict) -> None:
        limit = int(entry.get("requests_per_minute", self.api["requests_per_minute"]))
        now = time.monotonic()
        with self.lock:
            bucket = self.rate.setdefault(entry["label"], deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= limit:
                raise ApiError(429, "rate_limited", "该 token 请求过于频繁，请稍后再试。")
            bucket.append(now)

    def effective_cap(self, entry: dict) -> str:
        configured = self.settings["max_grant"]
        return entry["max_grant"] if grant_rank(entry["max_grant"]) < grant_rank(configured) else configured

    # -- 任务 ---------------------------------------------------------------

    def _validate_payload(self, payload) -> dict:
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_body", "请求体必须是 JSON 对象。")
        unknown = set(payload) - _PAYLOAD_FIELDS
        if unknown:
            raise ApiError(400, "invalid_body", "请求包含未知字段：" + "、".join(sorted(unknown)))
        if not isinstance(payload.get("task"), str):
            raise ApiError(400, "invalid_body", "缺少 task 字符串。")
        return payload

    def _check_workspace(self, workspace: str | None, fallback) -> Path:
        roots = self.api["allowed_workspace_roots"]
        if not roots:
            return Path(workspace or fallback)
        candidate = Path(workspace or fallback).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise ApiError(400, "invalid_workspace", "工作区不存在或不可访问。") from None
        for root in roots:
            try:
                resolved.relative_to(Path(root).expanduser().resolve(strict=True))
                return resolved
            except (ValueError, OSError):
                continue
        raise ApiError(403, "workspace_denied", "工作区不在 api.allowed_workspace_roots 允许范围内。")

    def _prepare(self, entry: dict, payload: dict) -> DshProcess:
        payload = self._validate_payload(payload)
        cap = self.effective_cap(entry)
        requested = payload.get("grant")
        if requested is not None:
            if not isinstance(requested, str) or requested not in ("read-only", "workspace-write",
                                                                   "danger-full-access"):
                raise ApiError(400, "invalid_grant", "grant 必须是三档之一。")
            try:
                validate_grant(requested, cap)
            except AgentError:
                raise ApiError(403, "grant_denied",
                               f"该 token 的权限上限是 {cap}，不能申请 {requested}。") from None
        workspace_value = payload.get("workspace")
        if workspace_value is not None and not isinstance(workspace_value, str):
            raise ApiError(400, "invalid_body", "workspace 必须是字符串。")
        session_value = payload.get("session_id")
        if session_value is not None and not isinstance(session_value, str):
            raise ApiError(400, "invalid_body", "session_id 必须是字符串。")
        timeout_s = payload.get("timeout_s")
        if timeout_s is not None:
            if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
                raise ApiError(400, "invalid_body", "timeout_s 必须是数字。")
            if not 1 <= timeout_s <= self.api["max_timeout_s"]:
                raise ApiError(400, "invalid_body",
                               f"timeout_s 必须在 1 到 {self.api['max_timeout_s']} 秒之间。")
        fallback = self.settings.get("workspace") or Path.cwd()
        workspace = self._check_workspace(workspace_value, fallback)
        try:
            return prepare_run(
                self.config, payload["task"], workspace=workspace,
                grant=requested, timeout_s=timeout_s,
                session_id=session_value, max_grant=cap,
            )
        except AgentError as exc:
            raise ApiError(400, "invalid_request", self.redactor.text(str(exc))) from None

    def _acquire(self) -> None:
        if not self.semaphore.acquire(blocking=False):
            raise ApiError(429, "busy", "并发上限已满，请稍后重试。")
        with self.lock:
            self.running += 1

    def _release(self, job: ApiJob) -> None:
        with self.lock:
            if job.released:
                return
            job.released = True
            self.running = max(0, self.running - 1)
        self.semaphore.release()

    def start(self, entry: dict, payload: dict, remote: str) -> dict:
        process = self._prepare(entry, payload)
        self._acquire()
        try:
            process.start()
        except Exception:
            self.semaphore.release()
            with self.lock:
                self.running = max(0, self.running - 1)
            raise
        job = ApiJob("job-" + secrets.token_hex(8), process, entry["label"])
        with self.lock:
            self.jobs[job.id] = job
            self._prune()
        self._audit("started", job, {"remote": remote, "task_chars": len(process.task)})
        threading.Thread(target=self._watch, args=(job,), name=f"api-{job.id}", daemon=True).start()
        return {
            "job_id": job.id, "status": "running", "run_dir": str(process.run_dir),
            "session_id": process.session_id, "grant": process.grant,
            "max_grant": process.max_grant, "workspace": str(process.workspace),
        }

    def _watch(self, job: ApiJob) -> None:
        while not job.process.finished:
            time.sleep(0.5)
        self._release(job)
        result = job.process.result()
        self._write_conversation(job, result)
        self._audit("finished", job, {
            "status": result["status"], "usage": result["usage"],
            "seconds": result["seconds"], "error": result["error"],
        })

    def run_sync(self, entry: dict, payload: dict, remote: str) -> dict:
        process = self._prepare(entry, payload)
        self._acquire()
        job = ApiJob("job-" + secrets.token_hex(8), process, entry["label"])
        try:
            process.start()
            with self.lock:
                self.jobs[job.id] = job
                self._prune()
            self._audit("started", job, {"remote": remote, "task_chars": len(process.task)})
            result = process.wait()
        finally:
            self._release(job)
        self._write_conversation(job, result)
        self._audit("finished", job, {
            "status": result["status"], "usage": result["usage"],
            "seconds": result["seconds"], "error": result["error"],
        })
        result["job_id"] = job.id
        return result

    def _job(self, job_id: str, entry: dict | None = None) -> ApiJob:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None or (entry is not None and job.token_label != entry["label"]):
            # 其他 token 的 job 也按“不存在”处理，避免泄露 job id 是否存在。
            raise ApiError(404, "not_found", "未知的 job_id。")
        return job

    def result(self, job_id: str, entry: dict | None = None, seconds: float = 0.2) -> dict:
        job = self._job(job_id, entry)
        data = job.process.wait(seconds)
        data["job_id"] = job.id
        return data

    def cancel(self, job_id: str, entry: dict | None = None) -> dict:
        job = self._job(job_id, entry)
        data = job.process.cancel()
        data["job_id"] = job.id
        return data

    def events(self, job_id: str, entry: dict, after: int, limit: int = 500) -> tuple[list[str], int]:
        job = self._job(job_id, entry)
        run_dir = job.process.run_dir
        if run_dir is None:
            return [], after
        path = run_dir / "events.jsonl"
        if not path.is_file():
            return [], after
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[after:after + limit]
        return chunk, after + len(chunk)

    def _write_conversation(self, job: ApiJob, result: dict) -> None:
        with self.lock:
            if job.written:
                return
            job.written = True
        try:
            transcript = [item["text"] for item in result.get("transcript") or []
                          if item.get("text", "").strip()]
            final = result.get("answer")
            if final and transcript and transcript[-1].strip() == final.strip():
                transcript = transcript[:-1]
            usage = result.get("usage") or {}
            rows = [
                f"# DSH 对话 {job.id}", "",
                f"- 调用方：{job.token_label}",
                f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
                f"- 工作区：{result.get('workspace')}",
                f"- 权限：{result.get('grant')}（上限 {result.get('max_grant')}）",
                f"- 会话：{result.get('session_id') or '未知'}",
                f"- 状态：{result.get('status')}；耗时 {result.get('seconds')} 秒；"
                f"用量 input={usage.get('inputTokens', 0)} output={usage.get('outputTokens', 0)}",
                "", "## 调用方请求", "", result.get("task") or "", "",
                "## DSH 过程", "",
            ]
            rows += transcript or ["（没有中间文本，直接给出最终回答）"]
            rows += ["", "## 工具调用", ""]
            if result.get("tool_calls"):
                rows += [f"- {call.get('tool')}: {call.get('input')}" for call in result["tool_calls"]]
            else:
                rows += ["（无）"]
            rows += ["", "## DSH 最终回答", "", final or "（无）"]
            if result.get("permission_requests"):
                rows += ["", "## 被拒绝的操作", ""]
                rows += [f"- {item.get('tool')}: {item.get('excerpt')}"
                         for item in result["permission_requests"]]
            body = self.redactor.text("\n".join(rows) + "\n")
            path = self.conversation_dir / f"{job.id}.md"
            temp = path.with_suffix(".md.tmp")
            temp.write_text(body, encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass

    def _prune(self) -> None:
        if len(self.jobs) <= 100:
            return
        finished = [key for key, job in self.jobs.items() if job.process.finished]
        for key in finished[: max(1, len(self.jobs) - 100)]:
            self.jobs.pop(key, None)

    # -- 审计与健康检查 -----------------------------------------------------

    def _audit(self, action: str, job: ApiJob, extra: dict) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action, "job_id": job.id, "token": job.token_label,
            "grant": job.grant, "workspace": job.workspace, **extra,
        }
        line = json.dumps(self.redactor.clean(record), ensure_ascii=False) + "\n"
        with self.audit_lock:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def health(self) -> dict:
        return {
            "status": "ok", "ready": True, "version": VERSION,
            "dsh_version": self.dsh_version,
            "credential_source": self.provider_key_source,
            "jobs_running": self.running,
            "max_concurrency": self.api["max_concurrency"],
            "uptime_s": round(time.time() - self.started, 1),
        }

    def shutdown(self) -> None:
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            try:
                job.process.cancel()
            except Exception:
                continue
            self._release(job)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "dsh-entry-api/" + VERSION

    @property
    def state(self) -> ApiState:
        return self.server.state  # type: ignore[attr-defined]

    # -- 入口 ---------------------------------------------------------------

    def do_GET(self) -> None:
        self._guarded("GET")

    def do_POST(self) -> None:
        self._guarded("POST")

    def _guarded(self, method: str) -> None:
        self._body_consumed = False
        try:
            self._check_host()
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if path == "/dsh/v1/healthz" and method == "GET":
                self._send(200, self.state.health())
                return
            if method == "GET":
                match = _EVENTS_RE.fullmatch(path)
                if match:
                    entry = self._require_auth()
                    after = self._query_int("after", 0)
                    lines, next_offset = self.state.events(match.group(1), entry, after)
                    self._send_lines(lines, next_offset)
                    return
                match = _TASK_RE.fullmatch(path)
                if match:
                    entry = self._require_auth()
                    self._send(200, self.state.result(match.group(1), entry))
                    return
            elif method == "POST":
                if path == "/dsh/v1/tasks":
                    entry = self._require_auth()
                    payload = self._read_json()
                    remote = self.client_address[0]
                    self._send(202, self.state.start(entry, payload, remote))
                    return
                if path == "/dsh/v1/run":
                    entry = self._require_auth()
                    payload = self._read_json()
                    self._send(200, self.state.run_sync(entry, payload, self.client_address[0]))
                    return
                match = _CANCEL_RE.fullmatch(path)
                if match:
                    entry = self._require_auth()
                    self._send(200, self.state.cancel(match.group(1), entry))
                    return
            if path in ("/dsh/v1/tasks", "/dsh/v1/run", "/dsh/v1/healthz") or \
                    _TASK_RE.fullmatch(path) or _CANCEL_RE.fullmatch(path) or \
                    _EVENTS_RE.fullmatch(path):
                self._send(405, {"error": {"code": "method_not_allowed", "message": "方法不允许。"}},
                           extra={"Allow": "GET, POST"})
                return
            self._send(404, {"error": {"code": "not_found", "message": "未知路径。"}})
        except ApiError as exc:
            self._send(exc.status, {"error": {"code": exc.code, "message": exc.message}},
                       extra={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._send(500, {"error": {"code": "internal_error", "message": "服务内部错误。"}})

    # -- 辅助 ---------------------------------------------------------------

    def _check_host(self) -> None:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            raise ApiError(400, "invalid_host", "缺少 Host 头。")
        name, _, port = host.rpartition(":")
        if not name:
            name, port = host, ""
        name = name.strip("[]")
        configured = str(self.server.server_address[1])
        if name not in LOOPBACK or (port and port != configured):
            # 防 DNS rebinding：只接受回环 Host 与本服务端口。
            raise ApiError(421, "invalid_host", "只接受回环地址访问。")

    def _require_auth(self) -> dict:
        entry = self.state.authenticate(self.headers.get("Authorization"))
        if entry is None:
            raise ApiError(401, "unauthorized", "缺少或无效的 Bearer token。")
        self.state.check_rate(entry)
        return entry

    def _read_json(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(411, "length_required", "需要 Content-Length。")
        try:
            length = int(raw_length)
        except ValueError:
            raise ApiError(400, "invalid_body", "Content-Length 不合法。") from None
        if length <= 0:
            raise ApiError(400, "invalid_body", "请求体为空。")
        if length > self.state.api["max_body_bytes"]:
            raise ApiError(413, "body_too_large", "请求体超过限制。")
        body = self.rfile.read(length)
        self._body_consumed = True
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "invalid_body", "请求体不是有效 JSON。") from None

    def _query_int(self, name: str, default: int) -> int:
        from urllib.parse import parse_qs

        values = parse_qs(urlsplit(self.path).query).get(name)
        if not values:
            return default
        try:
            value = int(values[0])
        except ValueError:
            raise ApiError(400, "invalid_query", f"{name} 必须是非负整数。") from None
        if value < 0:
            raise ApiError(400, "invalid_query", f"{name} 必须是非负整数。")
        return value

    def _send(self, status: int, payload: dict, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_raw(status, body, "application/json; charset=utf-8", extra)

    def _send_lines(self, lines: list[str], next_offset: int) -> None:
        body = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        self._send_raw(200, body, "application/x-ndjson; charset=utf-8",
                       {"X-Next-Offset": str(next_offset)})

    def _send_raw(self, status: int, body: bytes, content_type: str,
                  extra: dict | None = None) -> None:
        if not getattr(self, "_body_consumed", True):
            try:
                pending = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                pending = 0
            if pending > 0:
                # 未读取的请求体会被 HTTP/1.1 管线误当作下一条请求；直接断开。
                self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[api] %s %s\n" % (self.address_string(), fmt % args))


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, state: ApiState):
        self.state = state
        super().__init__(address, Handler)


def build_state(args) -> ApiState:
    config = load_config(args.config)
    if args.host is not None:
        config["api"]["host"] = args.host
    if args.port is not None:
        config["api"]["port"] = args.port
    if config["api"]["host"] not in LOOPBACK:
        raise AgentError("只允许绑定回环地址。")
    if not 1024 <= int(config["api"]["port"]) <= 65535:
        raise AgentError("api.port 必须在 1024–65535 之间。")
    return ApiState(config)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="本项目的独立 DSH HTTP API")
    parser.add_argument("--config", type=Path, help="JSON 配置（含 dsh 与 api 段）")
    parser.add_argument("--host", help="覆盖 api.host；只允许回环地址")
    parser.add_argument("--port", type=int, help="覆盖 api.port")
    parser.add_argument("--new-token", action="store_true",
                        help="生成新 token 并打印其 sha256；不启动服务")
    parser.add_argument("--save", action="store_true",
                        help="配合 --new-token：把 sha256 直接写入 --config 的 api.tokens")
    parser.add_argument("--label", default="default", help="配合 --save：调用方名称，如 opencode")
    parser.add_argument("--grant", default="workspace-write",
                        choices=("read-only", "workspace-write", "danger-full-access"),
                        help="配合 --save：该 token 的权限上限")
    parser.add_argument("--hash-token", action="store_true",
                        help="从标准输入或提示符读取 token，打印 sha256；不启动服务")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.new_token:
        token = secrets.token_urlsafe(32)
        digest = sha256_token(token)
        result = {"token": token, "sha256": digest,
                  "hint": "token 只显示这一次；把它交给调用方，仓库里只保存 sha256。"}
        if args.save:
            if not args.config:
                print("--save 需要同时提供 --config。", file=sys.stderr)
                return 2
            path = Path(args.config)
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                print("无法读取配置文件；未写入 token。", file=sys.stderr)
                return 2
            if not isinstance(raw, dict) or not isinstance(raw.get("api", {}), dict):
                print("配置结构不可识别；未写入 token。", file=sys.stderr)
                return 2
            api_section = raw.setdefault("api", {})
            tokens = api_section.setdefault("tokens", [])
            if not isinstance(tokens, list):
                print("api.tokens 不是数组；未写入 token。", file=sys.stderr)
                return 2
            if any(isinstance(item, dict) and item.get("label") == args.label for item in tokens):
                print(f"label={args.label} 已存在；请换一个 --label。", file=sys.stderr)
                return 2
            tokens.append({"label": args.label, "sha256": digest, "max_grant": args.grant})
            temp = path.with_suffix(path.suffix + ".tmp")
            try:
                temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                load_config(temp)  # 先校验候选配置，避免写入后才发现不合法
                temp.replace(path)
            except (AgentError, OSError):
                try:
                    temp.unlink()
                except OSError:
                    pass
                print("写入后校验失败；配置未改动。", file=sys.stderr)
                return 2
            result.update({"saved_to": str(path), "label": args.label,
                           "max_grant": args.grant})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.hash_token:
        token = getpass("Token: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
        if not token:
            print("没有读到 token。", file=sys.stderr)
            return 2
        print(sha256_token(token))
        return 0
    try:
        state = build_state(args)
    except (AgentError, OSError) as exc:
        print(Redactor().text(str(exc)), file=sys.stderr)
        return 2
    try:
        server = ApiServer((state.api["host"], state.api["port"]), state)
    except OSError:
        print(f"无法监听 {state.api['host']}:{state.api['port']}；端口可能被占用。", file=sys.stderr)
        return 2
    print(json.dumps({
        "listening": f"http://{state.api['host']}:{state.api['port']}/dsh/v1",
        "tokens": len(state.tokens),
        "max_concurrency": state.api["max_concurrency"],
        "credential_source": state.provider_key_source,
        "dsh_version": state.dsh_version,
    }, ensure_ascii=False), file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
