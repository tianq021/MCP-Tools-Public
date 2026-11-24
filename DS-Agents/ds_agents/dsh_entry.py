"""把 DSH 变成外部 AI 可调用的入口。

外部 AI（或其 MCP 客户端）只需提交任务、工作区与权限档位，本模块负责：

* 从 DSH 的 ``logs/server.json`` 解析真实入口与 ``DSH_HOME``，不复制凭证；
* 以 ``--profile headless --json`` 启动 DSH，逐行收集 NDJSON 事件；
* 把最终回答、会话 id、工具调用、被拒绝的越权操作归一化为 JSON；
* 记录每次运行到 ``runs/dsh-*``，并做脱敏与超时/取消处理。

权限模型：DSH 自带 ``DSH_PERMISSION_MODE`` 三档。调用方只能在
``max_grant`` 上限内申请；DSH 因无审批应答方而拒绝的操作会出现在
``permission_requests``，并附带 ``next_grant``，由外部 AI 向人说明后再提升。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import AgentError, load_config
from .security import Redactor, reject_link_ancestors

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRANTS = ("read-only", "workspace-write", "danger-full-access")
_RANK = {name: index for index, name in enumerate(GRANTS)}
MAX_TASK_CHARS = 20000
_ANSWER_TAIL = 4000

# DSH 的审批拒绝与沙箱拒绝通常以工具错误文本返回；只对 error 结果做匹配，避免误报。
_DENIAL = re.compile(
    r"(approval|permission|sandbox|not allowed|not permitted|denied|escalat|"
    r"越权|权限|沙箱|拒绝|不允许|未授权)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DshLocation:
    """一次调用所需的 DSH 安装位置；不含任何凭证内容。"""

    root: Path | None
    entry: Path
    home: Path
    node: str


def grant_rank(value: str) -> int:
    if value not in _RANK:
        raise AgentError("grant 必须是 read-only、workspace-write 或 danger-full-access。")
    return _RANK[value]


def validate_grant(value: str, max_grant: str) -> str:
    if grant_rank(value) > grant_rank(max_grant):
        raise AgentError(f"请求的 grant={value} 超过本入口允许的上限 {max_grant}。")
    return value


def next_grant(value: str, max_grant: str) -> str | None:
    index = _RANK[value] + 1
    if index < len(GRANTS) and index <= _RANK[max_grant]:
        return GRANTS[index]
    return None


def _contained(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def locate_dsh(root=None, entry=None, home=None, node=None) -> DshLocation:
    """解析 DSH 入口。优先读取启动记录；显式参数可覆盖。

    ``server.json`` 记录的 ``dshHome`` 必须位于声明的 DSH 根目录内，避免把
    任意目录当作凭证主目录。入口本身允许位于根目录之外（实测安装在
    ``F:\\deepseekA-020-test``），但必须是真实存在的文件。
    """

    root_path = Path(root).expanduser().absolute() if root else None
    derived = entry is None or home is None
    if derived:
        if root_path is None:
            raise AgentError("缺少 DSH 根目录，且没有显式指定 --dsh-entry/--dsh-home。")
        server_path = root_path / "logs" / "server.json"
        try:
            server = json.loads(server_path.read_text(encoding="utf-8-sig"))
        except OSError:
            raise AgentError(
                "未找到 DSH 启动记录 logs/server.json；请先运行 F:\\deepseekA\\start-dsh.ps1，"
                "或用 --dsh-root/--dsh-entry/--dsh-home 指定。"
            ) from None
        except ValueError:
            raise AgentError("DSH 的 logs/server.json 不是有效 JSON。") from None
        if not isinstance(server, dict):
            raise AgentError("DSH 的 logs/server.json 结构不可识别。")
        if entry is None:
            entry = server.get("entry")
        if home is None:
            home = server.get("dshHome")
        if not isinstance(entry, str) or not isinstance(home, str):
            raise AgentError("DSH 启动记录缺少 entry 或 dshHome。")
        if not _contained(Path(home), root_path):
            raise AgentError("DSH 凭证主目录不在指定根目录内，已停止。")

    entry_path, home_path = Path(entry), Path(home)
    if not entry_path.is_file():
        raise AgentError("DSH 入口文件不存在；请检查安装或在启动记录中确认 entry。")
    if not home_path.is_dir():
        raise AgentError("DSH 主目录不存在；请检查 dshHome。")

    node_path = shutil.which(node or "node")
    if not node_path:
        raise AgentError("找不到 Node.js；请安装 Node.js 或在配置中指定 dsh.node。")
    return DshLocation(root_path, entry_path.resolve(), home_path.resolve(), node_path)


def windows_proxy_environment() -> dict[str, str]:
    """读取当前 Windows 系统代理，仅用于本次子进程环境，不修改系统设置。"""

    if os.name != "nt":
        raise AgentError("--system-proxy 仅支持 Windows。")
    import winreg  # 仅 Windows 可用

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        raise AgentError("无法读取 Windows 代理设置。") from None
    if int(enabled) != 1 or not isinstance(server, str) or not server.strip():
        raise AgentError("系统代理未启用；可省略 --system-proxy 直连。")
    address = server.strip()
    if not re.match(r"^[a-z]+://", address, re.IGNORECASE):
        address = "http://" + address
    from urllib.parse import urlsplit

    parts = urlsplit(address)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise AgentError("系统代理格式不受支持；请在启动环境显式设置 HTTP_PROXY/HTTPS_PROXY。")
    origin = f"{parts.scheme}://{parts.netloc}"
    return {"HTTP_PROXY": origin, "HTTPS_PROXY": origin, "NO_PROXY": "localhost,127.0.0.1,::1"}


def build_environment(location: DshLocation, grant: str, use_system_proxy: bool = False) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in
           {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
    env["DSH_HOME"] = str(location.home)
    env["DSH_PERMISSION_MODE"] = grant
    env["NO_COLOR"] = "1"
    if use_system_proxy:
        env.update(windows_proxy_environment())
    return env


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _brief(value, limit: int = 300):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "…"


class DshProcess:
    """一次 ``dsh --profile headless --json`` 运行，可同步等待或后台观察。"""

    def __init__(self, location: DshLocation, task: str, workspace: Path, grant: str,
                 timeout_s: float, output: Path, max_grant: str, session_id: str | None = None,
                 use_system_proxy: bool = False, max_answer_chars: int = 200000,
                 redactor: Redactor | None = None):
        self.location = location
        self.task = task
        self.workspace = workspace
        self.grant = grant
        self.max_grant = max_grant
        self.timeout_s = timeout_s
        self.session_id_arg = session_id
        self.use_system_proxy = use_system_proxy
        self.max_answer_chars = max_answer_chars
        self.redactor = redactor or Redactor()
        self.output = output
        self.run_dir: Path | None = None
        self.process: subprocess.Popen | None = None
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.started_at = time.monotonic()
        self.deadline = self.started_at + timeout_s
        self.finished = False
        self.timed_out = False
        self.cancelled = False
        self.exit_code: int | None = None
        self.session_id = session_id
        self.final_text: str | None = None
        self.error_message: str | None = None
        self.event_count = 0
        self.tool_calls: list[dict] = []
        self.messages: list[dict] = []
        self._message_chars = 0
        self.denials: list[dict] = []
        self.usage: dict = {}
        self.last_event: dict | None = None
        self._events = None
        self._stderr = None
        self._pump_thread: threading.Thread | None = None
        self._result_written = False

    # -- 准备与启动 ---------------------------------------------------------

    def _argv(self) -> list[str]:
        argv = [self.location.node, str(self.location.entry), "--profile", "headless", "--json"]
        if self.session_id_arg:
            argv += ["--session-id", self.session_id_arg]
        argv += ["--", self.task]
        return argv

    def start(self) -> "DshProcess":
        if self.process is not None:
            return self
        self.output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = self.output / f"dsh-{stamp}-{uuid.uuid4().hex[:8]}"
        self.run_dir.mkdir(parents=True)
        request = {
            "task": self.task,
            "workspace": str(self.workspace),
            "grant": self.grant,
            "max_grant": self.max_grant,
            "timeout_seconds": self.timeout_s,
            "session_id": self.session_id_arg,
            "dsh_entry": str(self.location.entry),
            "dsh_home": str(self.location.home),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        (self.run_dir / "request.json").write_text(
            json.dumps(self.redactor.clean(request), ensure_ascii=False, indent=2), encoding="utf-8")
        self._events = (self.run_dir / "events.jsonl").open("a", encoding="utf-8")
        self._stderr = (self.run_dir / "stderr.log").open("w", encoding="utf-8", errors="replace")
        env = build_environment(self.location, self.grant, self.use_system_proxy)
        try:
            self.process = subprocess.Popen(
                self._argv(), cwd=str(self.workspace), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self._stderr,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            self._events.close()
            self._stderr.close()
            raise AgentError("无法启动 DSH 进程；请检查 Node.js 与入口文件权限。") from None
        self._pump_thread = threading.Thread(target=self._pump, name="dsh-stdout", daemon=True)
        self._pump_thread.start()
        return self

    def _pump(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.queue.put(line)
        finally:
            self.queue.put(None)

    # -- 事件处理 -----------------------------------------------------------

    def _handle(self, line: str) -> None:
        raw = line.strip()
        if not raw:
            return
        self.event_count += 1
        if self._events is not None:
            self._events.write(self.redactor.text(raw) + "\n")
            self._events.flush()
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        self.last_event = event
        kind = event.get("type")
        if kind == "session":
            session = event.get("sessionId")
            if isinstance(session, str) and session:
                self.session_id = session
        elif kind == "final":
            text = event.get("text")
            if isinstance(text, str):
                self.final_text = text
        elif kind == "text":
            text = event.get("text")
            if isinstance(text, str) and text.strip() and self._message_chars < 100000:
                trimmed = text if len(text) <= 20000 else text[:20000] + "…"
                self.messages.append({"type": "text", "text": trimmed})
                self._message_chars += len(trimmed)
                if len(self.messages) > 200:
                    self.messages.pop(0)
        elif kind == "tool_call":
            if len(self.tool_calls) < 50:
                self.tool_calls.append({"tool": event.get("tool"), "input": _brief(event.get("input"))})
        elif kind == "tool_result" and event.get("status") == "error":
            text = event.get("result")
            if isinstance(text, str) and _DENIAL.search(text) and len(self.denials) < 5:
                self.denials.append({
                    "call_id": event.get("callId"),
                    "tool": next((c["tool"] for c in reversed(self.tool_calls)
                                  if c.get("tool")), None),
                    "excerpt": text[:500],
                })
        elif kind == "status" and event.get("phase") == "step_end":
            usage = event.get("usage")
            if isinstance(usage, dict):
                for key in ("inputTokens", "outputTokens"):
                    if isinstance(usage.get(key), int):
                        self.usage[key] = self.usage.get(key, 0) + usage[key]
        elif kind == "error":
            message = event.get("message") or event.get("error")
            self.error_message = str(message) if message else raw[:1000]

    # -- 观察与终止 ---------------------------------------------------------

    def wait(self, seconds: float | None = None) -> dict:
        """观察至完成或 ``seconds`` 秒；观察超时只返回 running，不终止任务。"""

        with self.lock:
            if not self.finished:
                observation_deadline = None if seconds is None else time.monotonic() + seconds
                while not self.finished:
                    now = time.monotonic()
                    if now >= self.deadline:
                        self.timed_out = True
                        if self.process is not None:
                            _kill_tree(self.process)
                        self._finish()
                        break
                    if observation_deadline is not None and now >= observation_deadline:
                        return self.result()
                    try:
                        item = self.queue.get(timeout=min(0.5, max(0.01, self.deadline - now)))
                    except queue.Empty:
                        continue
                    if item is None:
                        self._finish()
                    else:
                        self._handle(item)
            return self.result()

    def _finish(self) -> None:
        if self.finished:
            return
        if self.process is not None:
            try:
                self.exit_code = self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                _kill_tree(self.process)
                try:
                    self.exit_code = self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.exit_code = None
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=5)
        # 清空 EOF 前的残余事件。
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                self._handle(item)
        self.finished = True
        if self.timed_out:
            self.error_message = self.error_message or "DSH 运行超过本次允许的时间，已终止。"
        elif self.cancelled:
            self.error_message = self.error_message or "调用方取消了本次 DSH 运行。"
        elif self.exit_code not in (0, None) and not self.error_message:
            self.error_message = f"DSH 退出码为 {self.exit_code}。"
        self._close_streams()
        self._write_result()

    def cancel(self) -> dict:
        with self.lock:
            if not self.finished:
                self.cancelled = True
                if self.process is not None:
                    _kill_tree(self.process)
                self._finish()
            return self.result()

    def _close_streams(self) -> None:
        if self.process is not None and self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except OSError:
                pass
        if self._events is not None:
            try:
                self._events.close()
            except OSError:
                pass
            self._events = None
        if self._stderr is not None:
            try:
                self._stderr.close()
            except OSError:
                pass
            self._stderr = None

    def _stderr_tail(self) -> str | None:
        if self.run_dir is None:
            return None
        path = self.run_dir / "stderr.log"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        text = self.redactor.text(text.strip())
        return text[-_ANSWER_TAIL:] if text else None

    def status(self) -> str:
        if self.cancelled and (self.timed_out is False):
            return "cancelled"
        if self.timed_out:
            return "timeout"
        if not self.finished:
            return "running"
        if self.error_message or self.exit_code not in (0, None):
            return "failed"
        if self.final_text is None:
            return "failed"
        return "completed"

    def result(self) -> dict:
        status = self.status()
        answer = self.final_text
        truncated = False
        if answer is not None and len(answer) > self.max_answer_chars:
            answer, truncated = answer[: self.max_answer_chars], True
        if status == "failed" and answer is None:
            error = self.error_message or "DSH 结束但没有返回最终回答。"
        else:
            error = self.error_message if status in ("failed", "timeout") else None
        data = {
            "status": status,
            "ok": status == "completed",
            "task": self.redactor.text(self.task),
            "workspace": str(self.workspace),
            "grant": self.grant,
            "max_grant": self.max_grant,
            "session_id": self.session_id,
            "answer": self.redactor.text(answer) if answer is not None else None,
            "answer_truncated": truncated,
            "run_dir": str(self.run_dir) if self.run_dir else None,
            "events": self.event_count,
            "transcript": self.redactor.clean(self.messages),
            "tool_calls": self.tool_calls,
            "permission_requests": self.denials,
            "needs_permission": bool(self.denials),
            "next_grant": next_grant(self.grant, self.max_grant) if self.denials else None,
            "usage": self.usage or None,
            "exit_code": self.exit_code,
            "seconds": round(time.monotonic() - self.started_at, 2),
            "error": self.redactor.text(error) if error else None,
        }
        if status in ("failed", "timeout"):
            tail = self._stderr_tail()
            if tail:
                data["stderr_tail"] = tail
        return self.redactor.clean(data)

    def _write_result(self) -> None:
        if self._result_written or self.run_dir is None:
            return
        self._result_written = True
        path = self.run_dir / "result.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.result(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def prepare_run(config: dict, task: str, workspace: Path | None = None, grant: str | None = None,
                timeout_s: float | None = None, output: Path | None = None,
                session_id: str | None = None, use_system_proxy: bool = False,
                max_grant: str | None = None) -> DshProcess:
    """校验参数并构造（未启动的）运行对象；供 CLI、MCP 与 API 共用。

    ``max_grant`` 只能收紧配置中的上限，不能让调用方突破它。
    """

    settings = config["dsh"]
    text = task.strip() if isinstance(task, str) else ""
    if not text:
        raise AgentError("任务不得为空。")
    if len(text) > MAX_TASK_CHARS:
        raise AgentError(f"任务最多 {MAX_TASK_CHARS} 个字符。")
    if text.startswith("-"):
        raise AgentError("任务不能以 '-' 开头；DSH 会把它当作命令行选项。")
    if "\x00" in text:
        raise AgentError("任务包含非法字符。")
    if session_id is not None:
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", session_id):
            raise AgentError("session_id 格式不合法。")

    workspace_path = Path(workspace or settings.get("workspace") or Path.cwd()).expanduser().absolute()
    try:
        workspace_path = workspace_path.resolve(strict=True)
    except OSError:
        raise AgentError("工作区不存在或不可访问。") from None
    if not workspace_path.is_dir():
        raise AgentError("工作区必须是目录。")
    reject_link_ancestors(workspace_path)

    max_grant = settings["max_grant"] if max_grant is None else (
        settings["max_grant"] if grant_rank(max_grant) > grant_rank(settings["max_grant"]) else max_grant)
    requested = grant or settings.get("default_grant") or "workspace-write"
    if grant is None and grant_rank(requested) > grant_rank(max_grant):
        requested = max_grant  # 调用方未指定时，默认档位不得超过其上限。
    chosen = validate_grant(requested, max_grant)

    location = locate_dsh(root=settings.get("root"), entry=settings.get("entry"),
                          home=settings.get("home"), node=settings.get("node"))
    if _contained(workspace_path, location.home) or (
            location.root is not None and _contained(workspace_path, location.root)):
        raise AgentError("工作区不能位于 DSH 安装或主目录内。")

    output_path = Path(output or settings.get("output") or PROJECT_ROOT / "runs").expanduser().absolute()
    reject_link_ancestors(output_path)
    timeout = float(timeout_s if timeout_s is not None else settings["timeout_seconds"])
    if not 1 <= timeout <= 7200:
        raise AgentError("timeout 必须在 1 到 7200 秒之间。")

    return DshProcess(
        location=location, task=text, workspace=workspace_path, grant=chosen,
        timeout_s=timeout, output=output_path, max_grant=max_grant,
        session_id=session_id, use_system_proxy=use_system_proxy,
        max_answer_chars=int(settings["max_answer_chars"]),
    )


def run_task(config: dict, task: str, **kwargs) -> dict:
    """同步执行一个任务，直到完成、失败或超时。"""

    process = prepare_run(config, task, **kwargs)
    process.start()
    return process.wait()


def doctor(config: dict, use_system_proxy: bool = False) -> dict:
    settings = config["dsh"]
    info: dict = {
        "node": None, "dsh_entry": None, "dsh_home": None, "dsh_version": None,
        "profiles": [], "max_grant": settings["max_grant"], "credentials_present": False,
        "proxy": None, "errors": [],
    }
    try:
        location = locate_dsh(root=settings.get("root"), entry=settings.get("entry"),
                              home=settings.get("home"), node=settings.get("node"))
        info.update({"node": location.node, "dsh_entry": str(location.entry),
                     "dsh_home": str(location.home)})
        info["credentials_present"] = (location.home / ".credentials.yaml").is_file()
        try:
            completed = subprocess.run(
                [location.node, str(location.entry), "--version"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "DSH_HOME": str(location.home)},
            )
            info["dsh_version"] = (completed.stdout or completed.stderr).strip()[:120] or None
        except (OSError, subprocess.SubprocessError):
            info["errors"].append("无法读取 DSH 版本。")
        profile_root = location.home / "profiles"
        if profile_root.is_dir():
            info["profiles"] = sorted(p.name for p in profile_root.iterdir() if p.is_dir())
    except AgentError as exc:
        info["errors"].append(str(exc))
    try:
        info["proxy"] = "available" if windows_proxy_environment() else "unavailable"
    except AgentError:
        # 代理是可选传输路径；不影响入口本身可用性。
        info["proxy"] = "unavailable"
    info["ready"] = not info["errors"] and info["credentials_present"]
    return Redactor().clean(info)


def _human(result: dict) -> str:
    lines = [
        f"状态：{result['status']}（grant={result['grant']}，上限={result['max_grant']}）",
        f"工作区：{result['workspace']}",
        f"会话：{result['session_id'] or '未知'}",
        f"耗时：{result['seconds']} 秒；事件：{result['events']}；工具调用：{len(result['tool_calls'])}",
        f"记录：{result['run_dir']}",
    ]
    if result.get("error"):
        lines.append(f"错误：{result['error']}")
    if result.get("permission_requests"):
        lines.append(f"被拒绝的操作：{len(result['permission_requests'])} 条；"
                     f"如需继续，可申请 grant={result.get('next_grant') or '（已达上限）'}")
    if result.get("answer"):
        lines += ["", result["answer"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m ds_agents.dsh_entry",
        description="外部 AI 调用 DSH 的入口：任务、工作区、权限档位 → 结构化结果")
    parser.add_argument("--config", type=Path, help="可选 JSON 配置（含 dsh 段，不存放 Key）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_run_options(command, session_required=False):
        command.add_argument("--task", required=True, help="交给 DSH 的任务文本")
        command.add_argument("--workspace", type=Path, help="DSH 的工作目录，默认取配置或当前目录")
        command.add_argument("--grant", choices=GRANTS,
                             help="权限档位；不得超过配置中的 max_grant")
        command.add_argument("--timeout", type=float, help="本次运行最长秒数，10–7200")
        command.add_argument("--output", type=Path, help="运行记录目录，默认 runs")
        command.add_argument("--session-id", required=session_required,
                             help="续接已有 DSH 会话时必填；直接运行时可选")
        command.add_argument("--system-proxy", action="store_true",
                             help="把当前 Windows 系统代理传入子进程；不修改系统设置")
        command.add_argument("--json", action="store_true", help="输出结构化 JSON，便于 AI 解析")
        command.add_argument("--progress", action="store_true", help="在 stderr 打印工具调用进度")

    for name, help_text in (("run", "执行一个任务并等待结果"),
                            ("resume", "续接指定会话再执行")):
        command = sub.add_parser(name, help=help_text)
        add_run_options(command, session_required=name == "resume")
    sub.add_parser("doctor", help="检查 DSH 入口、主目录与凭证是否存在；不发起模型请求")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.command == "doctor":
            report = doctor(config)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ready"] else 2
        process = prepare_run(
            config, args.task, workspace=args.workspace, grant=args.grant,
            timeout_s=args.timeout, output=args.output, session_id=args.session_id,
            use_system_proxy=args.system_proxy,
        )
        process.start()
        last = 0
        while True:
            result = process.wait(1.0)
            if args.progress and len(result["tool_calls"]) > last:
                for call in result["tool_calls"][last:]:
                    print(f"[dsh] {call['tool']}: {call['input']}", file=sys.stderr)
                last = len(result["tool_calls"])
            if result["status"] != "running":
                break
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json
              else _human(result))
        return 0 if result["status"] == "completed" else 1
    except (AgentError, OSError) as exc:
        message = str(exc) if isinstance(exc, AgentError) else "本地路径不可用或没有访问权限。"
        print(Redactor().text(message), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
