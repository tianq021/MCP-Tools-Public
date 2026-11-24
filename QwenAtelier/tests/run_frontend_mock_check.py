"""Run the mock-API headless browser check. No model, no real service, no network APIs."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import mock_frontend_server  # noqa: E402

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
OUT = ROOT / "tests" / "artifacts" / "frontend-mock"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    if not CHROME.is_file():
        print(f"Chrome not found: {CHROME}", file=sys.stderr)
        return 2
    server = mock_frontend_server.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    profile = Path(tempfile.mkdtemp(prefix="frontend-mock-", dir=ROOT / ".tmp"))
    log = (OUT / "chrome.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(CHROME), "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--remote-debugging-port=0",
         f"--user-data-dir={profile}", "--window-size=1512,1100", "about:blank"],
        stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not port_file.is_file():
            if process.poll() is not None:
                print("Chrome exited early; see tests/artifacts/frontend-mock/chrome.log", file=sys.stderr)
                return 2
            time.sleep(0.2)
        if not port_file.is_file():
            print("Chrome DevTools port did not appear", file=sys.stderr)
            return 2
        port = port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        result = subprocess.run(
            ["node", str(HERE / "frontend_mock_check.cjs"), base, port,
             str(OUT / "checks.json"), str(OUT)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300,
        )
        (OUT / "node.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.stdout:
            print(result.stdout)
        if result.returncode:
            print(result.stderr, file=sys.stderr)
        return result.returncode
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        server.shutdown()
        server.server_close()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
