from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import AgentError
from .protocol import SCHEMA, SYSTEM, parse_answer


@dataclass
class Reply:
    answer: dict
    input_tokens: int
    output_tokens: int
    model: str


class Provider(Protocol):
    name: str
    role: str

    def preflight(self) -> None: ...
    def generate(self, prompt: str, call_dir: Path) -> Reply: ...


def usage_counts(usage, input_key, output_key):
    if not isinstance(usage, dict):
        raise AgentError("服务未返回用量统计，无法继续执行预算控制。")
    counts = [usage.get(input_key), usage.get(output_key)]
    if any(type(n) is not int or n < 0 for n in counts):
        raise AgentError("服务用量统计无效，已停止。")
    return counts


def diagnostic_hint(text):
    """Classify locally; never return the original process output."""
    lower = text.lower()
    if any(s in lower for s in ("401", "unauthorized", "refresh token", "not logged in")):
        return "认证失败，请重新检查 codex login。"
    if any(s in lower for s in ("403", "forbidden")):
        return "服务拒绝访问，请检查账号权限或网络出口。"
    if any(s in lower for s in ("certificate", "tls", "ssl")):
        return "TLS 或证书连接失败，请检查系统网络证书。"
    if any(s in lower for s in ("reconnect", "stream disconnected", "error sending request", "timed out", "connection")):
        return "检测到网络或流连接失败，请检查网络和代理。"
    if any(s in lower for s in ("unsupported", "not supported", "unknown variant", "invalid value")):
        return "参数或模型不兼容，请检查 CLI 版本及模型权限。"
    if any(s in lower for s in ("access is denied", "permission denied", "os error 5")):
        return "本地缓存访问被拒绝，请检查目录权限。"
    return "请检查登录状态、模型权限与网络。"


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Numeric PID only. No shell and no user-controlled command text.
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def bounded_process(args, cwd, env, stdin, timeout, cap):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    try:
        process = subprocess.Popen(args, cwd=cwd, env=env, shell=False, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    except OSError:
        raise AgentError("Codex 进程无法启动；请检查可执行文件路径和安装。") from None
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()
    def drain(stream, index):
        while True:
            data = stream.read1(4096)
            if not data:
                break
            room = cap - len(buffers[index])
            buffers[index].extend(data[:max(0, room)])
            if len(data) > room:
                exceeded.set()
    def write_input():
        try:
            process.stdin.write(stdin.encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    workers = [threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
               threading.Thread(target=write_input, daemon=True)]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if exceeded.is_set():
                raise AgentError("Codex 输出超过大小限制；进程已终止。")
            if time.monotonic() >= deadline:
                hint = diagnostic_hint(buffers[1].decode("utf-8", "replace") + buffers[0].decode("utf-8", "replace"))
                raise AgentError("Codex 请求超时；进程已终止，不自动重试。" + hint)
            time.sleep(0.05)
    finally:
        stop_process(process)
        process.wait(timeout=10)
        for worker in workers:
            worker.join(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    if exceeded.is_set():
        raise AgentError("Codex 输出超过大小限制。")
    return process.returncode, buffers[0].decode("utf-8", "replace"), buffers[1].decode("utf-8", "replace")


DISABLED_FEATURES = ("shell_tool", "apps", "plugins", "hooks", "multi_agent", "multi_agent_v2",
                     "browser_use", "browser_use_external", "computer_use", "image_generation",
                     "memories", "shell_snapshot", "code_mode", "code_mode_host", "in_app_local_automation")


class CodexProvider:
    name = "codex"
    role = "检查实现逻辑、边界条件和可复核的代码证据。"

    def __init__(self, config):
        self.config = config
        self.executable = shutil.which(config["codex"]["executable"])
        if not self.executable and Path(config["codex"]["executable"]).is_file():
            self.executable = str(Path(config["codex"]["executable"]).resolve())

    def environment(self):
        env = {k: v for k, v in os.environ.items()
               if not re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)
               or k in ("OPENAI_API_KEY", "CODEX_API_KEY")}
        # Do not inherit an OpenAI endpoint override that could silently route Codex to another model vendor.
        for key in ("OPENAI_BASE_URL", "OPENAI_API_BASE"):
            env.pop(key, None)
        return env

    def preflight(self):
        if not self.executable or Path(self.executable).suffix.lower() in (".cmd", ".bat", ".ps1"):
            raise AgentError("需要原生 Codex 可执行文件；设置 codex.executable 为 codex.exe 绝对路径。")
        code, out, _ = bounded_process([self.executable, "exec", "--help"], None, self.environment(), "", 15, 100000)
        required = ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema", "--json", "read-only")
        if code or any(flag not in out for flag in required):
            raise AgentError("Codex CLI 缺少必需的隔离参数；请升级。已验证版本为 0.153.4。")
        code, out, _ = bounded_process([self.executable, "features", "list"], None, self.environment(), "", 15, 100000)
        names = {line.split()[0] for line in out.splitlines() if line.split()}
        if code or not set(DISABLED_FEATURES).issubset(names):
            raise AgentError("Codex 功能开关与已验证版本不兼容；请核对版本。")

    def command(self, call_dir):
        args = [self.executable, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
                "--cd", str(call_dir), "--output-schema", str(call_dir / "schema.json"),
                "-c", "approval_policy=\"never\"", "-c", "web_search=\"disabled\"",
                "-c", "model_provider=\"openai\"", "-c", "project_doc_max_bytes=0",
                "-c", "model_reasoning_effort=\"low\"", "-c", "hide_agent_reasoning=true"]
        for feature in DISABLED_FEATURES:
            args += ["--disable", feature]
        if self.config["codex"]["model"]:
            args += ["--model", self.config["codex"]["model"]]
        return args + ["-"]

    def generate(self, prompt, call_dir):
        (call_dir / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        code, stdout, stderr = bounded_process(self.command(call_dir), call_dir, self.environment(),
                                              SYSTEM + "\n\n" + prompt, self.config["timeout_seconds"],
                                              self.config["max_response_bytes"])
        if code:
            # Never persist raw stderr; it may contain credentials or provider payloads.
            reason = diagnostic_hint(stderr + stdout)
            raise AgentError(f"Codex 执行失败（退出码 {code}）。{reason}")
        answer, usage, completed = None, None, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") in ("error", "turn.failed"):
                raise AgentError("Codex 返回失败事件；" + diagnostic_hint(json.dumps(event)))
            item = event.get("item", {})
            if item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                raise AgentError("Codex 意外返回工具调用；已拒绝此次结果并停止。")
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                answer = item.get("text")
            if event.get("type") == "turn.completed":
                usage, completed = event.get("usage"), True
        if not completed:
            raise AgentError("Codex 未完成本轮，不能将部分输出视为成功。")
        input_tokens, output_tokens = usage_counts(usage, "input_tokens", "output_tokens")
        return Reply(parse_answer(answer), input_tokens, output_tokens,
                     self.config["codex"]["model"] or "Codex CLI OpenAI default (model not reported)")


def deepseek_post(body, key, timeout, max_bytes):
    """Fixed official TLS endpoint. Never follows redirects or logs response bodies."""
    conn = http.client.HTTPSConnection("api.deepseek.com", timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        conn.request("POST", "/chat/completions", body=json.dumps(body).encode("utf-8"),
                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            detail = {401: "密钥无效", 402: "账户余额不足", 429: "请求限流"}.get(response.status, "服务请求失败")
            raise AgentError(f"DeepSeek HTTP {response.status}：{detail}；未自动重试。")
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentError("DeepSeek 请求超时；未自动重试。")
            if conn.sock:
                conn.sock.settimeout(remaining)
            chunk = response.read1(min(8192, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise AgentError("DeepSeek 响应超过大小限制。")
        return json.loads(data)
    except (OSError, http.client.HTTPException, ValueError):
        raise AgentError("DeepSeek 网络、超时或响应格式错误；未自动重试。") from None
    finally:
        conn.close()


class DeepSeekProvider:
    name = "deepseek"
    role = "独立审查假设、反例和替代解释，质疑缺少证据的结论。"

    def __init__(self, config, transport=deepseek_post):
        self.config, self.transport = config, transport

    def preflight(self):
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise AgentError("未配置 DEEPSEEK_API_KEY。按 README 设置后重试，或先使用 --mock。")

    def generate(self, prompt, call_dir):
        options = self.config["deepseek"]
        body = {"model": options["model"], "messages": [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt}], "max_tokens": options["max_output_tokens"],
                "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}, "stream": False}
        response = self.transport(body, os.environ["DEEPSEEK_API_KEY"], self.config["timeout_seconds"],
                                  self.config["max_response_bytes"])
        try:
            choice = response["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("tool_calls"):
                raise AgentError("DeepSeek 输出被截断、过滤或请求工具；不作为完整分析使用。")
            text = choice["message"]["content"]
            input_tokens, output_tokens = usage_counts(response.get("usage"), "prompt_tokens", "completion_tokens")
            return Reply(parse_answer(text), input_tokens, output_tokens, response.get("model", options["model"]))
        except (KeyError, TypeError, IndexError):
            raise AgentError("DeepSeek 响应字段不符合官方 Chat Completions 协议。") from None


class MockProvider:
    """Deterministic offline fixture; never presented as a real model answer."""
    def __init__(self, name, role):
        self.name, self.role = name, role

    def preflight(self):
        pass

    def generate(self, prompt, call_dir):
        data = json.loads(prompt)
        file = data["untrusted_snapshot"][0]
        line = file["numbered_lines"][0].split(": ", 1)[1] if file["numbered_lines"] else ""
        answer = {"summary": f"[MOCK 演示] {self.name} 第 {data['round']} 轮，不代表真实模型判断。",
                  "claims": [{"id": "sample", "statement": "样本包含可读取的文本。", "stance": "supported",
                              "evidence": [{"path": file["path"], "line_start": 1, "line_end": 1, "quote": line}],
                              "reason": "仅演示文件引用核验。"}],
                  "questions": ["实际运行行为仍需另行测试。"],
                  "responses": ["已读取上一轮双方意见；未执行项目代码。"] if data["previous_round"] else [],
                  "limitations": ["这是模拟适配器，不会访问任何模型服务。"]}
        return Reply(answer, 100, 80, "MOCK")
