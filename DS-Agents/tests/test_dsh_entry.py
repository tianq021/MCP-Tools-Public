from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ds_agents.config import AgentError, load_config
from ds_agents.dsh_entry import (locate_dsh, next_grant, prepare_run, run_task,
                                  validate_grant)
from ds_agents.mcp_server import Server  # noqa: F401  (导入即验证模块可加载)

NODE = shutil.which("node")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

STUB = r"""
const args = process.argv.slice(2);
const out = (value) => process.stdout.write(JSON.stringify(value) + "\n");
if (args.includes("--version")) { process.stdout.write("stub-dsh 0.0.0\n"); process.exit(0); }
out({type: "session", sessionId: "session-stub-1", cwd: process.cwd()});
out({type: "text", text: "正在分析"});
out({type: "tool_call", callId: "c1", tool: "read", input: {path: "a.py"}});
out({type: "tool_result", callId: "c1", status: "completed", result: "file contents"});
out({type: "tool_call", callId: "c2", tool: "write", input: {path: "../outside.txt"}});
out({type: "tool_result", callId: "c2", status: "error",
     result: "Permission denied by sandbox: path outside workspace requires approval"});
out({type: "status", phase: "step_end", turn: 1, step: 1,
     usage: {inputTokens: 12, outputTokens: 8}});
out({type: "final", text: "完成：已读取 a.py，外部写入被拒绝。"});
if (args.join(" ").includes("slow")) setInterval(() => {}, 1000);
else process.exit(0);
"""


@unittest.skipUnless(NODE, "需要 Node.js 才能运行 DSH 桩")
class DshEntryCase(unittest.TestCase):
    def setUp(self):
        base = PROJECT_ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.dsh_root = self.root / "dsh-root"
        (self.dsh_root / "logs").mkdir(parents=True)
        self.home = self.dsh_root / "dsh-home"
        self.home.mkdir()
        (self.home / ".credentials.yaml").write_text("refs:\n  DEEPSEEK_API_KEY: sk-test-not-used\n",
                                                     encoding="utf-8")
        self.stub = self.root / "stub-dsh.js"
        self.stub.write_text(STUB, encoding="utf-8")
        (self.dsh_root / "logs" / "server.json").write_text(json.dumps({
            "entry": str(self.stub), "dshHome": str(self.home),
        }), encoding="utf-8")
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.output = self.root / "output"
        self.config = load_config(None)
        self.config["dsh"].update({
            "root": str(self.dsh_root), "entry": str(self.stub), "home": str(self.home),
            "node": NODE, "workspace": str(self.project), "output": str(self.output),
            "max_grant": "workspace-write", "timeout_seconds": 30,
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_locate_uses_server_record_and_rejects_outside_home(self):
        location = locate_dsh(root=self.dsh_root, node=NODE)
        self.assertEqual(location.entry, self.stub.resolve())
        self.assertEqual(location.home, self.home.resolve())
        outside = self.root / "outside-home"
        outside.mkdir()
        (self.dsh_root / "logs" / "server.json").write_text(json.dumps({
            "entry": str(self.stub), "dshHome": str(outside),
        }), encoding="utf-8")
        with self.assertRaises(AgentError):
            locate_dsh(root=self.dsh_root, node=NODE)

    def test_grant_cap_and_ordering(self):
        self.assertEqual(next_grant("read-only", "workspace-write"), "workspace-write")
        self.assertIsNone(next_grant("workspace-write", "workspace-write"))
        with self.assertRaises(AgentError):
            validate_grant("danger-full-access", "workspace-write")
        with self.assertRaises(AgentError):
            prepare_run(self.config, "任务", grant="danger-full-access")
        with self.assertRaises(AgentError):
            prepare_run(self.config, "  ")
        with self.assertRaises(AgentError):
            prepare_run(self.config, "-rm", workspace=self.project)

    def test_run_normalizes_events_and_reports_denials(self):
        result = run_task(self.config, "读取 a.py 并总结", grant="read-only")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["session_id"], "session-stub-1")
        self.assertIn("完成", result["answer"])
        self.assertEqual(result["usage"], {"inputTokens": 12, "outputTokens": 8})
        self.assertTrue(result["needs_permission"])
        self.assertEqual(result["next_grant"], "workspace-write")
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "events.jsonl").is_file())
        self.assertTrue((run_dir / "request.json").is_file())
        saved = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "completed")
        # 凭证内容不得进入运行记录。
        for path in run_dir.iterdir():
            if path.is_file():
                self.assertNotIn("sk-test-not-used", path.read_text(encoding="utf-8", errors="replace"))

    def test_timeout_kills_process_tree(self):
        result = run_task(self.config, "slow 任务：读取后等待", grant="read-only", timeout_s=2)
        self.assertEqual(result["status"], "timeout", result)
        self.assertIsNotNone(result.get("error"))

    def test_mcp_handshake_and_jobs(self):
        script = (
            "import sys;from ds_agents.mcp_server import main;"
            f"sys.exit(main(['--dsh-root', {str(self.dsh_root)!r}, '--dsh-entry', {str(self.stub)!r},"
            f" '--dsh-home', {str(self.home)!r}, '--node', {NODE!r}, '--workspace', {str(self.project)!r},"
            f" '--output', {str(self.output)!r}, '--max-grant', 'workspace-write']))"
        )
        process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-B", "-c", script],
            cwd=str(PROJECT_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        )
        try:
            def exchange(payload):
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
                return json.loads(process.stdout.readline())

            init = exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "dsh-entry")
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            process.stdin.flush()
            tools = exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {tool["name"] for tool in tools["result"]["tools"]}
            self.assertIn("dsh_start", names)
            start = exchange({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "dsh_start",
                                         "arguments": {"task": "读取 a.py", "grant": "read-only",
                                                       "workspace": str(self.project)}}})
            payload = json.loads(start["result"]["content"][0]["text"])
            self.assertEqual(payload["status"], "running")
            wait = exchange({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "dsh_wait",
                                        "arguments": {"job_id": payload["job_id"], "wait_s": 10}}})
            final = json.loads(wait["result"]["content"][0]["text"])
            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["session_id"], "session-stub-1")
            doctor = exchange({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                               "params": {"name": "dsh_doctor", "arguments": {}}})
            report = json.loads(doctor["result"]["content"][0]["text"])
            self.assertTrue(report["credentials_present"])
        finally:
            process.stdin.close()
            process.wait(timeout=15)
            stderr_text = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            if process.returncode not in (0, None):
                self.fail(stderr_text)


if __name__ == "__main__":
    unittest.main()
