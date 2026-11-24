"""Local DSH lifecycle checks. No task submission, restart or credential output."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid


@dataclass(frozen=True)
class Config:
    root: Path = Path(r"F:\deepseekA")
    port: int = 3080
    connect_timeout: float = 4.0
    request_timeout: float = 2.0
    startup_timeout: float = 45.0
    poll_interval: float = 0.5

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class Probe:
    def __init__(self, config: Config):
        self.config = config

    def result(self, state, *, reachable=False, **details):
        return {"state": state, "ready": state == "ready", "reachable": reachable,
                "url": self.config.url, **details}

    def request(self, method, path, headers=None, body=None):
        # Direct loopback only: no proxies, redirects, or caller-supplied hosts.
        conn = http.client.HTTPConnection("127.0.0.1", self.config.port,
                                          timeout=self.config.request_timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            reply = conn.getresponse()
            data = reply.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("response too large")
            return reply.status, reply.getheaders(), data
        finally:
            conn.close()

    def auth_path(self):
        info = json.loads((self.config.root / "logs/server.json").read_text(
            encoding="utf-8-sig"))
        logs = (self.config.root / "logs").resolve()
        path = Path(info["stdout"]).resolve()
        if not path.is_relative_to(logs):
            raise ValueError("unexpected log location")
        # Launch URL is near the beginning; tail also covers restarted log output.
        with path.open("rb") as handle:
            head = handle.read(131072)
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 131072))
            tail = handle.read(131072)
        matches = re.findall(re.escape(self.config.url) + r"/\?token=([A-Za-z0-9._~-]+)",
                             (head + b"\n" + tail).decode("utf-8", errors="replace"))
        if not matches:
            raise ValueError("launch URL unavailable")
        return "/?token=" + matches[-1]

    def __call__(self):
        try:
            with socket.create_connection(("127.0.0.1", self.config.port),
                                          timeout=self.config.connect_timeout):
                pass
        except ConnectionRefusedError:
            return self.result("stopped")
        except OSError:
            # A timeout or denied connection does not prove the port is unused.
            return self.result("connection_unknown")
        try:
            path = self.auth_path()
        except (OSError, ValueError, KeyError, TypeError):
            return self.result("auth_unavailable", reachable=True)
        try:
            status, headers, _ = self.request("GET", path)
            cookies = [value.split(";", 1)[0] for key, value in headers
                       if key.lower() == "set-cookie"]
            if status not in (200, 302, 303) or not cookies:
                return self.result("authentication_failed", reachable=True)
            request_id = str(uuid.uuid4())
            request = {"type": "client-request", "rpcId": request_id,
                       "method": "session/list", "payload": {"args": {"_request": {}}}}
            status, _, raw = self.request("POST", "/api/session/list", {
                "Cookie": "; ".join(cookies), "Origin": self.config.url,
                "Content-Type": "application/json"}, json.dumps(request).encode())
            reply = json.loads(raw)
            result = reply.get("result", {})
            items = result.get("value", {}).get("items") if result.get("ok") else None
            if (status != 200 or reply.get("type") != "server-response"
                    or reply.get("rpcId") != request_id or not isinstance(items, list)
                    or not all(isinstance(item, dict) for item in items)):
                return self.result("api_unavailable", reachable=True)
            return self.result("ready", reachable=True,
                               active_tasks=sum(bool(item.get("running")) for item in items))
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            return self.result("api_unavailable", reachable=True)


@contextmanager
def startup_lock(port, timeout):
    """OS mutex serializes separate MCP instances, and recovers abandoned owners."""
    if os.name != "nt":
        raise OSError("Windows required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, f"Local\\CodexDSHService-127001-{port}")
    if not handle:
        raise OSError("mutex creation failed")
    acquired = False
    try:
        code = kernel.WaitForSingleObject(handle, max(1, int(timeout * 1000)))
        if code == 258:
            raise TimeoutError("another startup is in progress")
        if code not in (0, 128):
            raise OSError("mutex wait failed")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def launch(config):
    script = config.root / "start-dsh.ps1"
    if not script.is_file():
        raise FileNotFoundError("startup script unavailable")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe")
    # The script launches the supervisor in a hidden window. Keep launcher
    # output separate from the MCP protocol.
    return subprocess.Popen([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-File", str(script)], cwd=config.root,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)


class ServiceManager:
    def __init__(self, config=None, *, probe=None, launcher=None, lock=None):
        self.config = config or Config()
        self.probe = probe or Probe(self.config)
        self.launcher = launcher or (lambda: launch(self.config))
        self.lock = lock or (lambda seconds: startup_lock(self.config.port, seconds))
        self._starters = []

    def status(self):
        return self.probe()

    def ensure_running(self):
        deadline = time.monotonic() + self.config.startup_timeout
        before = self.probe()
        if before["ready"]:
            return {**before, "action": "already_running", "startup_requested": False}
        if before["state"] != "stopped":
            return {**before, "action": "not_started", "startup_requested": False,
                    "reason": "Existing or unknown listener; no duplicate launch or restart."}
        try:
            with self.lock(max(0.001, deadline - time.monotonic())):
                # Another caller may have started DSH while we waited for the mutex.
                current = self.probe()
                if current["ready"]:
                    return {**current, "action": "already_running", "startup_requested": False}
                if current["state"] != "stopped":
                    return {**current, "action": "not_started", "startup_requested": False,
                            "reason": "Listener appeared while waiting; not replaced."}
                if time.monotonic() >= deadline:
                    return {**current, "action": "startup_timeout", "startup_requested": False}
                self._starters = [p for p in self._starters if p.poll() is None]
                if self._starters:
                    return {**current, "state": "starting", "action": "startup_pending",
                            "startup_requested": False,
                            "reason": "Previous startup script is still running."}
                try:
                    starter = self.launcher()
                except OSError:
                    return {**current, "action": "start_failed", "startup_requested": False,
                            "reason": "Could not invoke the existing DSH startup script."}
                self._starters.append(starter)
                while time.monotonic() < deadline:
                    current = self.probe()
                    if current["ready"]:
                        return {**current, "action": "started", "startup_requested": True}
                    code = starter.poll()
                    if code is not None and code != 0:
                        return {**current, "action": "start_failed", "startup_requested": True,
                                "startup_exit_code": code}
                    time.sleep(min(self.config.poll_interval,
                                   max(0, deadline - time.monotonic())))
                return {**current, "action": "startup_timeout", "startup_requested": True,
                        "reason": "Service not ready before deadline; no process was stopped."}
        except TimeoutError:
            return {**self.probe(), "action": "startup_lock_timeout", "startup_requested": False}
        except OSError:
            return {**before, "action": "lock_failed", "startup_requested": False}


if __name__ == "__main__":
    print(Probe(Config())()["state"])
