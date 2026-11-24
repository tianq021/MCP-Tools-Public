import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from service import Config, Probe, ServiceManager, launch, startup_lock


STOPPED = {"state": "stopped", "ready": False, "reachable": False}
READY = {"state": "ready", "ready": True, "reachable": True, "active_tasks": 1}


class ManagerTests(unittest.TestCase):
    def manager(self, probe, launcher=None, **config):
        return ServiceManager(Config(startup_timeout=config.get("timeout", 0.08),
                                     poll_interval=0.005), probe=probe,
                              launcher=launcher or Mock(), lock=lambda _: nullcontext())

    def test_existing_service_is_not_started(self):
        launcher = Mock()
        result = self.manager(lambda: READY, launcher).ensure_running()
        self.assertEqual(result["action"], "already_running")
        launcher.assert_not_called()

    def test_occupied_unknown_or_unauthenticated_port_is_not_started(self):
        for state in ("connection_unknown", "authentication_failed", "auth_unavailable",
                      "api_unavailable"):
            with self.subTest(state=state):
                launcher = Mock()
                probe = lambda: {**STOPPED, "state": state}
                self.assertEqual(self.manager(probe, launcher).ensure_running()["action"],
                                 "not_started")
                launcher.assert_not_called()

    def test_launch_error_is_sanitized(self):
        result = self.manager(lambda: STOPPED,
                              Mock(side_effect=OSError("secret-token"))).ensure_running()
        self.assertEqual(result["action"], "start_failed")
        self.assertNotIn("secret-token", json.dumps(result))

    def test_failed_launcher_reports_exit_code(self):
        result = self.manager(lambda: STOPPED, lambda: Mock(poll=lambda: 3)).ensure_running()
        self.assertEqual(result["action"], "start_failed")
        self.assertEqual(result["startup_exit_code"], 3)

    def test_timeout_never_kills_or_relaunches_pending_starter(self):
        process = Mock(poll=lambda: None)
        launcher = Mock(return_value=process)
        manager = self.manager(lambda: STOPPED, launcher, timeout=0.02)
        self.assertEqual(manager.ensure_running()["action"], "startup_timeout")
        self.assertEqual(manager.ensure_running()["action"], "startup_pending")
        launcher.assert_called_once()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_service_appearing_while_waiting_is_reused(self):
        launcher = Mock()
        probe = Mock(side_effect=[STOPPED, READY])
        self.assertTrue(self.manager(probe, launcher).ensure_running()["ready"])
        launcher.assert_not_called()

    def test_lock_timeout_does_not_launch(self):
        @contextmanager
        def busy(_):
            raise TimeoutError()
            yield
        launcher = Mock()
        manager = self.manager(lambda: STOPPED, launcher)
        manager.lock = busy
        self.assertEqual(manager.ensure_running()["action"], "startup_lock_timeout")
        launcher.assert_not_called()

    def test_two_managers_share_windows_startup_lock(self):
        active = threading.Event()
        count = []
        def start():
            count.append(1)
            time.sleep(0.05)
            active.set()
            return Mock(poll=lambda: 0)
        probe = lambda: READY if active.is_set() else STOPPED
        config = Config(port=65342, startup_timeout=2, poll_interval=0.005)
        managers = [ServiceManager(config, probe=probe, launcher=start) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda m: m.ensure_running(), managers))
        self.assertEqual(len(count), 1)
        self.assertTrue(all(result["ready"] for result in results))

    def test_launcher_is_hidden_and_suppresses_auth_url_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "start-dsh.ps1").write_text("# fixture", encoding="utf-8")
            with patch("service.subprocess.Popen") as popen:
                launch(Config(root=root))
                args, kwargs = popen.call_args
                self.assertEqual(args[0][-2:], ["-File", str(root / "start-dsh.ps1")])
                self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "logs").mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.config = Config(root=self.root, port=self.port, request_timeout=0.5,
                             startup_timeout=8, poll_interval=0.01)
        self.log = self.root / "logs/stdout.log"
        self.log.write_text(self.config.url + "/?token=fixture-private-token", encoding="utf-8")
        (self.root / "logs/server.json").write_text(json.dumps({"stdout": str(self.log)}),
                                                  encoding="utf-8")

    def start_fixture(self, *, invalid_rpc=False, auth_status=302):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                status = auth_status if self.path == "/?token=fixture-private-token" else 401
                self.send_response(status)
                if status == 302:
                    self.send_header("Set-Cookie", "dsh=fixture-private-cookie; HttpOnly")
                    self.send_header("Location", "/")
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                valid = (self.path == "/api/session/list"
                         and self.headers.get("Cookie") == "dsh=fixture-private-cookie"
                         and body["method"] == "session/list")
                self.send_response(200 if valid else 403)
                self.end_headers()
                self.wfile.write(json.dumps({"type": "server-response",
                    "rpcId": "wrong" if invalid_rpc else body["rpcId"],
                    "result": {"ok": True, "value": {"items": [
                        {"running": True, "title": "private task"}, {"running": False}]}}
                }).encode())

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(cleanup)
        return Mock(poll=lambda: 0)

    def test_cold_start_with_real_authenticated_http_probe(self):
        probe = Probe(self.config)
        self.assertEqual(probe()["state"], "stopped")
        launcher = Mock(side_effect=self.start_fixture)
        manager = ServiceManager(self.config, launcher=launcher)
        result = manager.ensure_running()
        self.assertEqual(result["action"], "started")
        self.assertTrue(result["ready"])
        self.assertEqual(result["active_tasks"], 1)
        self.assertEqual(manager.ensure_running()["action"], "already_running")
        launcher.assert_called_once()
        for secret in ("fixture-private-token", "fixture-private-cookie", "private task"):
            self.assertNotIn(secret, json.dumps(result))

    def test_incorrect_rpc_response_is_not_healthy(self):
        self.start_fixture(invalid_rpc=True)
        self.assertEqual(Probe(self.config)()["state"], "api_unavailable")

    def test_invalid_authentication_is_not_healthy(self):
        self.start_fixture(auth_status=401)
        self.assertEqual(Probe(self.config)()["state"], "authentication_failed")

    def test_launch_log_outside_logs_is_rejected(self):
        self.start_fixture()
        (self.root / "logs/server.json").write_text(json.dumps({"stdout": str(
            self.root / "elsewhere.log")}), encoding="utf-8")
        self.assertEqual(Probe(self.config)()["state"], "auth_unavailable")


if __name__ == "__main__":
    unittest.main()
