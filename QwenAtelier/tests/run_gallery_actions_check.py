"""Check gallery actions with an isolated application, temporary images and headless Chrome."""
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
import threading, socket, uvicorn
from PIL import Image
sys.path.insert(0,str(ROOT))
from app.main import create_app
from app.storage import Store
class TestServer:
    def __init__(self):
        self.temp=tempfile.TemporaryDirectory(prefix='gallery-data-',dir=ROOT/'.tmp')
        root=Path(self.temp.name)
        (root/'图片').mkdir()
        for index,color in enumerate(('teal','slateblue')):
            Image.new('RGB',(512,512),color).save(root/'图片'/f'sample{index}.png')
        Store(root).scan()
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen()
        self.server_address=self.sock.getsockname()
        self.server=uvicorn.Server(uvicorn.Config(create_app(root),log_level='error'))
        self.thread=threading.Thread(target=lambda:self.server.run(sockets=[self.sock]),daemon=True)
        self.thread.start()
    def shutdown(self):
        self.server.should_exit=True;self.thread.join(timeout=10)
    def server_close(self):
        self.sock.close();self.temp.cleanup()


CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
OUT = ROOT / "tests" / "artifacts" / "gallery-actions"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    if not CHROME.is_file():
        print(f"Chrome not found: {CHROME}", file=sys.stderr)
        return 2
    server = TestServer()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    profile = Path(tempfile.mkdtemp(prefix="gallery-browser-", dir=ROOT / ".tmp"))
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
                print("Chrome exited early; see tests/artifacts/gallery-actions/chrome.log", file=sys.stderr)
                return 2
            time.sleep(0.2)
        if not port_file.is_file():
            print("Chrome DevTools port did not appear", file=sys.stderr)
            return 2
        port = port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        result = subprocess.run(
            ["node", str(HERE / "gallery_actions_check.cjs"), base, port,
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
