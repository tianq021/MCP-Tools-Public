from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ds_agents.api_server import ApiServer, ApiState, resolve_provider_key, sha256_token
from ds_agents.config import AgentError, load_config

NODE = shutil.which("node")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

STUB = r"""
const args = process.argv.slice(2);
const out = (value) => process.stdout.write(JSON.stringify(value) + "\n");
if (args.includes("--version")) { process.stdout.write("stub-dsh 0.0.0\n"); process.exit(0); }
out({type: "session", sessionId: "session-api-1", cwd: process.cwd()});
out({type: "text", text: "正在读取 a.py"});
out({type: "tool_call", callId: "c1", tool: "read", input: {path: "a.py"}});
out({type: "tool_result", callId: "c1", status: "completed", result: "contents"});
out({type: "status", phase: "step_end", turn: 1, step: 1,
     usage: {inputTokens: 5, outputTokens: 3}});
out({type: "final", text: "完成：已读取 a.py。"});
if (args.join(" ").includes("slow")) setInterval(() => {}, 1000);
else process.exit(0);
"""

TOKEN = "test-token-abcdef"
READONLY_TOKEN = "test-token-readonly"
LIMITED_TOKEN = "test-token-limited"


@unittest.skipUnless(NODE, "需要 Node.js 才能运行 DSH 桩")
class ApiCase(unittest.TestCase):
    def setUp(self):
        base = PROJECT_ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.dsh_root = self.root / "dsh-root"
        (self.dsh_root / "logs").mkdir(parents=True)
        self.home = self.dsh_root / "dsh-home"
        self.home.mkdir()
        self.stub = self.root / "stub-dsh.js"
        self.stub.write_text(STUB, encoding="utf-8")
        (self.dsh_root / "logs" / "server.json").write_text(json.dumps({
            "entry": str(self.stub), "dshHome": str(self.home)}), encoding="utf-8")
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.other = self.root / "other"
        self.other.mkdir()
        self.output = self.root / "output"
        self.config = load_config(None)
        self.config["dsh"].update({
            "root": str(self.dsh_root), "entry": str(self.stub), "home": str(self.home),
            "node": NODE, "workspace": str(self.project), "output": str(self.output),
            "max_grant": "workspace-write", "default_grant": "workspace-write",
            "timeout_seconds": 30,
        })
        self.config["api"].update({
            "host": "127.0.0.1", "port": 3081,
            "tokens": [
                {"label": "main", "sha256": sha256_token(TOKEN), "max_grant": "workspace-write"},
                {"label": "reader", "sha256": sha256_token(READONLY_TOKEN), "max_grant": "read-only"},
                {"label": "limited", "sha256": sha256_token(LIMITED_TOKEN),
                 "max_grant": "workspace-write", "requests_per_minute": 2},
            ],
            "max_concurrency": 2, "max_body_bytes": 2048,
            "requests_per_minute": 100, "max_timeout_s": 60,
            "allowed_workspace_roots": [str(self.project)],
            "provider_key_env": "DSH_TEST_PROVIDER_KEY",
            "provider_key_file": None,
            "audit": str(self.output / "api-audit.jsonl"),
        })
        self.state = ApiState(self.config)
        self.server = ApiServer(("127.0.0.1", 0), self.state)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.state.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        time.sleep(0.2)  # 让 watcher 完成审计写入后再删临时目录
        self.temp.cleanup()

    def request(self, method, path, body=None, token=TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {}
        payload = None
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw or b"{}")

    def request_raw(self, method, path, token=TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Authorization": "Bearer " + token} if token else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8", "replace")
        result = (response.status, {key.lower(): value for key, value in response.getheaders()}, raw)
        connection.close()
        return result

    def wait_for(self, job_id, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline:
            status, body = self.request("GET", f"/dsh/v1/tasks/{job_id}")
            self.assertEqual(status, 200, body)
            if body["status"] != "running":
                return body
            time.sleep(0.1)
        self.fail("任务未在预期时间内完成")

    def wait_audit(self, action, seconds=3):
        path = Path(self.config["api"]["audit"])
        deadline = time.time() + seconds
        while time.time() < deadline:
            if path.exists():
                actions = [json.loads(line)["action"] for line in
                           path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if action in actions:
                    return actions
            time.sleep(0.05)
        self.fail(f"审计日志中没有 {action}")

    # -- 测试 ---------------------------------------------------------------

    def test_healthz_is_public_and_reports_isolation(self):
        status, body = self.request("GET", "/dsh/v1/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["credential_source"], "dsh_home")
        self.assertEqual(body["max_concurrency"], 2)
        self.assertEqual(body["dsh_version"], "stub-dsh 0.0.0")

    def test_auth_required(self):
        for token in (None, "", "wrong-token"):
            status, body = self.request("GET", "/dsh/v1/tasks/job-unknown", token=token)
            self.assertEqual(status, 401, body)

    def test_task_lifecycle(self):
        status, created = self.request("POST", "/dsh/v1/tasks",
                                       {"task": "读取 a.py", "grant": "read-only"})
        self.assertEqual(status, 202, created)
        job_id = created["job_id"]
        final = self.wait_for(job_id)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["session_id"], "session-api-1")
        self.assertIn("完成", final["answer"])
        self.assertEqual(final["usage"], {"inputTokens": 5, "outputTokens": 3})
        self.assertTrue(final["transcript"])
        self.assertIn("正在读取", final["transcript"][0]["text"])

        # 其他 token 不能读取该任务。
        status, denied = self.request("GET", f"/dsh/v1/tasks/{job_id}",
                                      token=READONLY_TOKEN)
        self.assertEqual(status, 404, denied)

        # 事件流可分页读取，供外部“实时看”对话。
        status, headers, raw = self.request_raw(
            "GET", f"/dsh/v1/tasks/{job_id}/events?after=0")
        self.assertEqual(status, 200)
        self.assertIn("session-api-1", raw)
        self.assertGreater(int(headers["x-next-offset"]), 0)
        status, headers2, raw2 = self.request_raw(
            "GET", f"/dsh/v1/tasks/{job_id}/events?after=1")
        self.assertEqual(status, 200)
        self.assertLess(len(raw2.splitlines()), len(raw.splitlines()))

        # 双方对话落盘为人可读文件。
        conversation = Path(self.output) / "conversations" / f"{job_id}.md"
        deadline = time.time() + 3
        while time.time() < deadline and not conversation.exists():
            time.sleep(0.05)
        text = conversation.read_text(encoding="utf-8")
        self.assertIn("调用方请求", text)
        self.assertIn("读取 a.py", text)
        self.assertIn("完成", text)

        actions = self.wait_audit("finished")
        self.assertIn("started", actions)

    def test_token_cap_and_workspace_roots(self):
        status, body = self.request("POST", "/dsh/v1/tasks",
                                    {"task": "写入", "grant": "workspace-write"},
                                    token=READONLY_TOKEN)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "grant_denied")
        status, body = self.request("POST", "/dsh/v1/tasks",
                                    {"task": "读取", "workspace": str(self.other)})
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "workspace_denied")

    def test_validation_and_body_limit(self):
        status, body = self.request("POST", "/dsh/v1/tasks", {"task": "x", "extra": 1})
        self.assertEqual(status, 400, body)
        status, body = self.request("POST", "/dsh/v1/tasks", {"task": "x" * 4000})
        self.assertEqual(status, 413, body)
        status, body = self.request("POST", "/dsh/v1/tasks", {"task": "x", "timeout_s": 9999})
        self.assertEqual(status, 400, body)

    def test_rate_limit(self):
        for _ in range(2):
            status, _ = self.request("GET", "/dsh/v1/tasks/job-none", token=LIMITED_TOKEN)
            self.assertEqual(status, 404)
        status, body = self.request("GET", "/dsh/v1/tasks/job-none", token=LIMITED_TOKEN)
        self.assertEqual(status, 429, body)
        self.assertEqual(body["error"]["code"], "rate_limited")

    def test_cancel_releases_concurrency(self):
        status, body = self.request("POST", "/dsh/v1/tasks",
                                    {"task": "slow 任务", "timeout_s": 60})
        self.assertEqual(status, 202, body)
        job_id = body["job_id"]
        status, cancelled = self.request("POST", f"/dsh/v1/tasks/{job_id}/cancel", {})
        self.assertEqual(status, 200, cancelled)
        self.assertEqual(cancelled["status"], "cancelled")
        deadline = time.time() + 10
        while time.time() < deadline and self.state.running:
            time.sleep(0.1)
        self.assertEqual(self.state.running, 0)
        self.wait_audit("finished")

    def test_project_key_is_injected_and_reported(self):
        try:
            with patch.dict(os.environ, {"DSH_TEST_PROVIDER_KEY": "sk-project-test-key"}):
                self.assertEqual(resolve_provider_key(self.config["api"]), "project_env")
                self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "sk-project-test-key")
                from ds_agents.dsh_entry import build_environment
                env = build_environment(self.state.location, "read-only")
                self.assertEqual(env.get("DEEPSEEK_API_KEY"), "sk-project-test-key")
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_config_rejects_public_bind(self):
        path = self.root / "bad.json"
        path.write_text(json.dumps({"api": {"host": "0.0.0.0"}}), encoding="utf-8")
        with self.assertRaises(AgentError):
            load_config(path)

    def test_token_hashing(self):
        self.assertEqual(len(sha256_token("abc")), 64)
        self.assertEqual(sha256_token("abc"), hashlib.sha256(b"abc").hexdigest())


if __name__ == "__main__":
    unittest.main()
