"""零依赖 MCP stdio 服务：把 DSH 入口暴露为 AI 可直接调用的工具。

工具集合：

* ``dsh_run``    同步执行一个任务（适合短任务）
* ``dsh_start``  后台启动，立即返回 job_id（适合长任务，避免客户端超时）
* ``dsh_status`` 查看进度，含最近事件与被拒绝的操作
* ``dsh_wait``   在指定秒数内等待完成或返回当前状态
* ``dsh_cancel`` 终止任务
* ``dsh_doctor`` 检查 DSH 入口、主目录与凭证

权限：``grant`` 只能取 read-only / workspace-write / danger-full-access，
且不得超过启动时由人设置的 ``--max-grant``。DSH 越权被拒后，结果会带
``needs_permission`` 与 ``next_grant``，供外部 AI 向人申请。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import AgentError, load_config
from .dsh_entry import (GRANTS, DshProcess, doctor, locate_dsh, prepare_run,
                        validate_grant)
from .security import Redactor

SERVER_NAME = "dsh-entry"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_JOBS = 20


class McpError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


_JOB = {"job_id": {"type": "string", "description": "dsh_start 返回的任务 id"}}
_RUN = {
    "task": {"type": "string", "description": "交给 DSH 的完整任务文本"},
    "workspace": {"type": "string", "description": "DSH 工作目录；默认取服务配置"},
    "grant": {"type": "string", "enum": list(GRANTS),
              "description": "权限档位，不得超过服务端 max_grant"},
    "timeout_s": {"type": "number", "minimum": 10, "maximum": 7200,
                  "description": "本次运行的最长秒数"},
    "session_id": {"type": "string", "description": "续接已有 DSH 会话"},
}
_START = dict(_RUN)
_START["timeout_s"] = {"type": "number", "minimum": 10, "maximum": 7200,
                       "description": "任务总时长上限；到点强制终止"}

TOOLS = [
    {"name": "dsh_run", "description": "同步调用 DSH 执行一个任务并返回最终回答、会话 id 与运行记录。"
                                      "任务可能持续数分钟；客户端超时较短时请改用 dsh_start。",
     "inputSchema": _schema(_RUN, ["task"])},
    {"name": "dsh_start", "description": "在后台启动一个 DSH 任务，立即返回 job_id；随后用 dsh_status/dsh_wait 获取结果。",
     "inputSchema": _schema(_START, ["task"])},
    {"name": "dsh_status", "description": "查看 DSH 任务的当前状态、最近工具调用、token 用量与需要授权的操作。",
     "inputSchema": _schema(_JOB, ["job_id"])},
    {"name": "dsh_wait", "description": "等待 DSH 任务至多 N 秒；未完成时返回 running 与当前进度。",
     "inputSchema": _schema({**_JOB, "wait_s": {"type": "number", "minimum": 0, "maximum": 600}},
                            ["job_id"])},
    {"name": "dsh_cancel", "description": "终止一个正在运行的 DSH 任务（会连同子进程一起结束）。",
     "inputSchema": _schema(_JOB, ["job_id"])},
    {"name": "dsh_doctor", "description": "检查 DSH 入口、主目录、凭证与版本；不发起模型请求。",
     "inputSchema": _schema({}, [])},
]


def _text_key(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class Server:
    def __init__(self, config: dict):
        self.config = config
        self.settings = config["dsh"]
        self.jobs: dict[str, DshProcess] = {}
        self.lock = threading.Lock()
        self.redactor = Redactor()

    # -- 工具实现 -----------------------------------------------------------

    def _start(self, arguments: dict) -> dict:
        process = prepare_run(
            self.config,
            _arg_str(arguments, "task", required=True, max_length=20000),
            workspace=_arg_path(arguments, "workspace"),
            grant=_arg_str(arguments, "grant"),
            timeout_s=_arg_number(arguments, "timeout_s"),
            session_id=_arg_str(arguments, "session_id"),
        )
        process.start()
        job_id = "job-" + uuid.uuid4().hex[:12]
        with self.lock:
            if len(self.jobs) >= MAX_JOBS:
                finished = [key for key, proc in self.jobs.items() if proc.finished]
                for key in finished[: max(1, len(self.jobs) - MAX_JOBS + 1)]:
                    self.jobs.pop(key, None)
            self.jobs[job_id] = process
        return {"job_id": job_id, "status": "running", "run_dir": str(process.run_dir),
                "session_id": process.session_id, "grant": process.grant,
                "max_grant": process.max_grant}

    def _job(self, arguments: dict) -> tuple[str, DshProcess]:
        job_id = _arg_str(arguments, "job_id", required=True, max_length=100)
        with self.lock:
            process = self.jobs.get(job_id)
        if process is None:
            raise McpError(-32602, "未知的 job_id；它可能已过期（服务重启会清空任务）。")
        return job_id, process

    def call(self, name: str, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise McpError(-32602, "arguments 必须是对象。")
        if name == "dsh_doctor":
            return doctor(self.config)
        if name == "dsh_run":
            process = prepare_run(
                self.config,
                _arg_str(arguments, "task", required=True, max_length=20000),
                workspace=_arg_path(arguments, "workspace"),
                grant=_arg_str(arguments, "grant"),
                timeout_s=_arg_number(arguments, "timeout_s"),
                session_id=_arg_str(arguments, "session_id"),
            )
            process.start()
            return process.wait()
        if name == "dsh_start":
            return self._start(arguments)
        if name == "dsh_status":
            _, process = self._job(arguments)
            return process.wait(0.3)
        if name == "dsh_wait":
            job_id, process = self._job(arguments)
            wait_s = _arg_number(arguments, "wait_s")
            if wait_s is not None and not 0 <= wait_s <= 600:
                raise McpError(-32602, "wait_s 必须在 0 到 600 秒之间。")
            result = process.wait(60.0 if wait_s is None else float(wait_s))
            result["job_id"] = job_id
            return result
        if name == "dsh_cancel":
            job_id, process = self._job(arguments)
            result = process.cancel()
            result["job_id"] = job_id
            return result
        raise McpError(-32602, f"未知工具：{name}")

    def shutdown(self) -> None:
        """服务退出时结束仍在运行的 DSH 子进程，避免遗留后台任务。"""

        with self.lock:
            processes = list(self.jobs.values())
        for process in processes:
            try:
                process.cancel()
            except Exception:
                continue

    # -- JSON-RPC -----------------------------------------------------------

    def dispatch(self, message: dict) -> dict | None:
        method = message.get("method")
        message_id = message.get("id")
        is_notification = message_id is None
        params = message.get("params") or {}
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": (
                        "调用 DSH 的入口。先用 dsh_doctor 检查环境；短任务用 dsh_run，"
                        "长任务用 dsh_start + dsh_wait。DSH 越权被拒时结果会带 "
                        "permission_requests 与 next_grant，必须先向人确认再提升 grant。"
                    ),
                }
            elif method in ("notifications/initialized", "notifications/cancelled"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str):
                    raise McpError(-32602, "tools/call 缺少 name。")
                try:
                    value = self.call(name, params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": _text_key(value)}],
                              "isError": False}
                except McpError:
                    raise
                except AgentError as exc:
                    message_text = self.redactor.text(str(exc))
                    result = {"content": [{"type": "text", "text": message_text}], "isError": True}
                except Exception:
                    result = {"content": [{"type": "text",
                                           "text": "DSH 入口内部错误；请检查服务端 stderr。"}],
                              "isError": True}
            else:
                raise McpError(-32601, f"不支持的方法：{method}")
        except McpError as exc:
            if is_notification:
                return None
            return _error(message_id, exc.code, exc.message)
        except Exception:
            if is_notification:
                return None
            return _error(message_id, -32603, "内部错误")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


# -- 参数校验（JSON Schema 之外的最小防线） --------------------------------

def _arg_str(arguments: dict, name: str, required: bool = False, max_length: int = 4000) -> str | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise McpError(-32602, f"缺少参数 {name}。")
        return None
    if not isinstance(value, str) or not value.strip():
        raise McpError(-32602, f"参数 {name} 必须是非空字符串。")
    if len(value) > max_length:
        raise McpError(-32602, f"参数 {name} 过长。")
    return value


def _arg_number(arguments: dict, name: str) -> float | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise McpError(-32602, f"参数 {name} 必须是数字。")
    return float(value)


def _arg_path(arguments: dict, name: str):
    value = _arg_str(arguments, name, max_length=1000)
    return value


def build_config(args) -> dict:
    config = load_config(args.config)
    settings = config["dsh"]
    overrides = {
        "root": args.dsh_root, "entry": args.dsh_entry, "home": args.dsh_home,
        "node": args.node, "workspace": args.workspace, "output": args.output,
    }
    for key, value in overrides.items():
        if value is not None:
            settings[key] = value
    if args.max_grant:
        settings["max_grant"] = validate_grant(args.max_grant, "danger-full-access")
    if args.default_grant:
        settings["default_grant"] = validate_grant(args.default_grant, settings["max_grant"])
    if args.timeout is not None:
        settings["timeout_seconds"] = args.timeout
    return config


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="DSH 入口 MCP stdio 服务")
    parser.add_argument("--config", type=Path, help="可选 JSON 配置（含 dsh 段）")
    parser.add_argument("--dsh-root", help="DSH 根目录，默认 F:/deepseekA")
    parser.add_argument("--dsh-entry", help="显式指定 DSH 入口（覆盖启动记录）")
    parser.add_argument("--dsh-home", help="显式指定 DSH_HOME（覆盖启动记录）")
    parser.add_argument("--node", help="Node.js 可执行文件，默认 node")
    parser.add_argument("--workspace", help="默认工作区")
    parser.add_argument("--output", help="运行记录目录")
    parser.add_argument("--max-grant", choices=GRANTS, help="允许调用方申请的最高权限档位")
    parser.add_argument("--default-grant", choices=GRANTS, help="未传 grant 时使用的档位")
    parser.add_argument("--timeout", type=float, help="默认任务超时秒数")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        config = build_config(args)
        # 启动即校验入口，避免客户端在第一次工具调用时才发现配置错误。
        locate_dsh(root=config["dsh"].get("root"), entry=config["dsh"].get("entry"),
                   home=config["dsh"].get("home"), node=config["dsh"].get("node"))
    except (AgentError, OSError) as exc:
        print(Redactor().text(str(exc)), file=sys.stderr)
        return 2
    server = Server(config)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                _write(_error(None, -32700, "JSON 解析失败"))
                continue
            if not isinstance(message, dict):
                _write(_error(None, -32600, "请求必须是 JSON 对象"))
                continue
            response = server.dispatch(message)
            if response is not None:
                _write(response)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
