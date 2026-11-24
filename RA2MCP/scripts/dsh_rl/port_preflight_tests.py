"""P0-6 端口配置/Windows 端口预检测试（离线，不启动游戏、不申请/消费令牌）。

运行：
  PYTHONPATH=E:\\RA2MCP\\.venv-rl\\Lib\\site-packages;E:\\RA2MCP\\scripts;E:\\RA2MCP\\scripts\\dsh_rl
  python scripts/dsh_rl/port_preflight_tests.py
"""
import json
import pathlib
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dsh_rl"))

from dsh_rl import port_config  # noqa: E402
import train_shared as ts  # noqa: E402


class PortConfigUnitTests(unittest.TestCase):
    def test_parse_excluded_output(self):
        text = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      3336        3336
     14450       14549
     50000       50059 *
"""
        self.assertEqual(port_config.parse_excluded_output(text),
                         [(3336, 3336), (14450, 14549), (50000, 50059)])

    def test_original_ports_rejected_when_excluded(self):
        # 固定注入排除范围，避免依赖测试机后续是否移除该范围。
        report = port_config.preflight_ports(
            14521, 2, check_ipv6=False, excluded_ipv4=[(14450, 14549)],
            excluded_ipv6=[], check_listeners=False)
        self.assertFalse(report["ok"])
        codes = {failure["code"] for failure in report["failures"]}
        self.assertIn("IPV4_EXCLUDED_PORT", codes)
        failed_ports = {failure["port"] for failure in report["failures"]}
        self.assertEqual(failed_ports, {14521, 14522})

    def test_candidate_14621_and_14622_bind(self):
        report = port_config.preflight_ports(14621, 2)
        if not report["ok"]:
            self.skipTest("current Windows env does not allow 14621/14622: %r"
                          % (report["failures"],))
        self.assertEqual(report["ports"], [14621, 14622])
        self.assertTrue(all(entry["ipv4_bind"]["ok"] for entry in report["entries"]))

    def test_custom_base_port_and_unique_worker_ports(self):
        ports = port_config.worker_ports(14621, 4)
        self.assertEqual(ports, [14621, 14622, 14623, 14624])
        self.assertEqual(len(set(ports)), 4)
        report = port_config.preflight_ports(14621, 4)
        if report["ok"]:
            self.assertEqual(report["ports"], ports)

    def test_port_conflict_reported(self):
        port = 14621
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            self.skipTest("cannot occupy port 14621 for conflict test")
        try:
            report = port_config.preflight_ports(
                port, 1, check_ipv6=False, check_listeners=False)
            self.assertFalse(report["ok"])
            failure = report["failures"][0]
            self.assertEqual(failure["port"], port)
            self.assertEqual(failure["code"], "IPV4_BIND_FAILED")
            self.assertIn(failure.get("errno"), (13, 98, 10048, 10013))
        finally:
            sock.close()

    def test_run_report_fields(self):
        report = port_config.preflight_ports(14621, 2)
        fields = port_config.run_report(14621, 2, report)
        self.assertEqual(fields["base_port"], 14621)
        self.assertEqual(fields["worker_ports"], [14621, 14622])
        self.assertIn("port_preflight", fields)
        json.dumps(fields)  # 必须可写入 run.json

    def test_default_base_port_backward_compatible(self):
        parser = ts.build_parser()
        args = parser.parse_args(["--out", "x", "--fresh",
                                  "--approval-token", "T", "--approval-config", "C"])
        self.assertEqual(args.base_port, 14521)
        self.assertEqual(port_config.worker_ports(14521, 2), [14521, 14522])

    def test_custom_base_port_arg(self):
        parser = ts.build_parser()
        args = parser.parse_args(["--out", "x", "--fresh", "--base-port", "14621",
                                  "--approval-token", "T", "--approval-config", "C"])
        self.assertEqual(args.base_port, 14621)

    def test_port_config_consistency_with_approval_config(self):
        good = {"port_configuration": {"base_port": 14621, "workers": 2,
                                       "worker_ports": [14621, 14622]}}
        result = port_config.validate_approval_port_config(good, 14621, 2)
        self.assertTrue(result["declared"])
        bad = {"port_configuration": {"base_port": 14621, "workers": 2,
                                      "worker_ports": [14521, 14522]}}
        with self.assertRaises(port_config.PortPreflightError) as context:
            port_config.validate_approval_port_config(bad, 14621, 2)
        self.assertEqual(context.exception.code, "PORT_CONFIG_MISMATCH")

    def test_approval_check_only_runs_port_preflight(self):
        """--approval-check-only 在退出前必须做端口预检（不消费令牌）。"""
        config = {
            "approval": {"experiment": "P0-4", "skill": "SMOKE",
                         "budget": {"max_steps": 512}, "seed": 11},
            "port_configuration": {"base_port": 14621, "workers": 2,
                                   "worker_ports": [14621, 14622]},
        }
        fake_request = {
            "token_id": "TEST-NOT-A-REAL-TOKEN", "experiment": "P0-4",
            "skill": "SMOKE", "steps": 512, "config": config,
            "budget": {"max_steps": 512}, "model": {},
        }
        argv = ["train_shared.py", "--workers", "2", "--resume", "dummy-model",
                "--out", str(ROOT / "reports/rl/_port_checkonly_should_not_exist"),
                "--base-port", "14621", "--steps", "512",
                "--approval-token", "TEST-NOT-A-REAL-TOKEN",
                "--approval-config", "fake-config.json", "--approval-check-only"]
        with mock.patch("dsh_learning.approvals.validate_launch_request",
                        return_value=fake_request):
            with mock.patch.object(sys, "argv", argv):
                # 不应抛异常；不消费令牌、不创建输出目录
                ts.main()
        self.assertFalse((ROOT / "reports/rl/_port_checkonly_should_not_exist").exists())

    def test_prepare_port_setup_rejects_unavailable_port(self):
        with self.assertRaises(port_config.PortPreflightError):
            ts.prepare_port_setup(SimpleNamespace(base_port=14521, workers=2), {})

    def test_prepare_port_setup_accepts_available_port(self):
        base, approval, report = ts.prepare_port_setup(
            SimpleNamespace(base_port=14621, workers=2), {})
        self.assertEqual(base, 14621)
        self.assertTrue(report["ok"])
        self.assertEqual(report["ports"], [14621, 14622])

    def test_auto_base_port_selection(self):
        with mock.patch.object(port_config, "suggest_base_ports", return_value=[14621]):
            base, approval, report = ts.prepare_port_setup(
                SimpleNamespace(base_port="auto", workers=2), {})
        self.assertEqual(base, 14621)

    def test_configure_game_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ra2yrcpp.json"
            path.write_text(json.dumps({"port": 14521, "keep": True}), encoding="utf-8")
            port_config.configure_game_port(tmp, 14621)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["port"], 14621)
            self.assertTrue(data["keep"])

    def test_make_env_receives_worker_port(self):
        captured = {}

        class FakeEnv:
            def __init__(self, *args, **kwargs):
                captured["port"] = kwargs.get("port")

        with mock.patch.object(ts, "RA2FullEnv", FakeEnv), \
                mock.patch.object(ts, "Monitor", lambda env, path: env):
            with tempfile.TemporaryDirectory() as tmp:
                ts.make_env(1, tmp, None, 0.5, 60.0, base_port=14621)
        self.assertEqual(captured["port"], 14622)

    def test_ipv4_and_ipv6_excluded_ranges_are_reported(self):
        report = port_config.preflight_ports(
            14621, 1, check_ipv6=True,
            excluded_ipv4=[(14450, 14549)], excluded_ipv6=[(14621, 14621)],
            check_listeners=False)
        self.assertIn("ipv4_excluded_ranges", report)
        self.assertIn("ipv6_excluded_ranges", report)
        self.assertEqual(report["ipv6_excluded_ranges"], [[14621, 14621]])
        self.assertTrue(report["ok"])  # IPv6 排除仅警告；IPv4 14621 可绑定

    def test_no_hardcoded_old_worker_port_remains(self):
        source = (ROOT / "scripts" / "dsh_rl" / "train_shared.py").read_text(
            encoding="utf-8")
        self.assertNotIn("port=14521 + index", source)
        self.assertNotIn("cfg['port'] = 14521", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
