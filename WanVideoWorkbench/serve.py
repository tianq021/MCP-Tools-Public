import asyncio
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import webbrowser

from aiohttp import ClientSession, web
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
PORT = int(os.environ.get("WAN_VIDEO_PORT", "17862"))
BASE = f"http://127.0.0.1:{PORT}"
LIMIT = 16 * 1024 * 1024


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def save_reference(raw, path):
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP") or im.width * im.height > 24_000_000:
                raise ValueError("参考图需为 PNG、JPEG 或 WebP，且不超过 2400 万像素")
            ImageOps.exif_transpose(im).convert("RGB").save(path, format="PNG")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("参考图无法读取") from exc


class Studio:
    def __init__(self):
        self.active = None
        self.task = None
        self.process = None
        self.last = None
        self.resident = None
        self.monitor = None
        self.completion = None
        self.model = {"state": "unloaded", "stage": "未预加载"}

    async def preload(self, request):
        if self.model["state"] in ("ready", "loading"):
            return web.json_response(self.model)
        if self.active or self.model["state"] == "unloading":
            raise web.HTTPConflict(text="请等待当前操作完成")
        self.model = {"state": "loading", "stage": "初始化推理环境"}
        self.monitor = asyncio.create_task(self.watch_resident())
        return web.json_response(self.model, status=202)

    async def watch_resident(self):
        error = "视频模型进程已退出"
        log = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", str(ROOT / "generate.py"), "--resident", cwd=ROOT,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.resident = proc
            if self.model["state"] == "unloading":
                proc.terminate()
            async for line in proc.stdout:
                value = line.decode("utf-8", errors="replace").strip()
                if self.active and self.completion and not self.completion.done():
                    if log is None:
                        log = (OUTPUT / self.active["id"] / "engine.log").open("ab")
                    log.write(line)
                    log.flush()
                if value == "WAN_READY" and self.model["state"] == "loading":
                    self.model.update(state="ready", stage="已预加载", current=3, total=3, pid=proc.pid)
                elif value.endswith("WAN_DONE") and self.completion and not self.completion.done():
                    if log:
                        log.close()
                        log = None
                    self.completion.set_result(True)
                elif "WAN_STATUS " in value:
                    status = json.loads(value.split("WAN_STATUS ", 1)[1])
                    if self.active:
                        self.active.update(status)
                    elif self.model["state"] == "loading":
                        self.model.update(status)
                elif value:
                    error = value[-480:]
            await proc.wait()
        except Exception as exc:
            error = str(exc)
        finally:
            if log:
                log.close()
            if self.resident and self.resident.returncode is None:
                self.resident.kill()
                await self.resident.wait()
            if self.completion and not self.completion.done():
                self.completion.set_exception(RuntimeError(error))
            self.resident = None
            if self.model["state"] == "unloading":
                self.model = {"state": "unloaded", "stage": "未预加载"}
            else:
                self.model = {"state": "error", "stage": error}

    async def stop_resident(self):
        if self.monitor and not self.monitor.done():
            self.model.update(state="unloading", stage="正在释放")
            if self.resident and self.resident.returncode is None:
                self.resident.terminate()
            await self.monitor
        self.model = {"state": "unloaded", "stage": "未预加载"}

    async def unload(self, request):
        if self.active:
            raise web.HTTPConflict(text="请先取消生成或等待完成")
        await self.stop_resident()
        return web.json_response(self.model)

    def history(self):
        videos = sorted(OUTPUT.glob("*/video.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        return [json.loads((p.parent / "request.json").read_text(encoding="utf-8")) for p in videos]

    async def generate(self, job, folder):
        state = self.active
        error_line = ""
        try:
            if state["cancelled"]:
                state.update(status="cancelled", stage="已取消")
                return
            if state["preloaded"]:
                if not self.resident or self.resident.returncode is not None:
                    raise RuntimeError("预加载进程已退出，请重新加载")
                self.completion = asyncio.get_running_loop().create_future()
                self.resident.stdin.write((json.dumps({"folder": str(folder)}) + "\n").encode())
                await self.resident.stdin.drain()
                await self.completion
                if not (folder / "video.mp4").is_file():
                    raise RuntimeError("视频文件未生成")
                state.update(status="done", stage="已完成")
                return
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", str(ROOT / "generate.py"), str(folder),
                cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.process = proc
            if state["cancelled"] and proc.returncode is None:
                proc.terminate()
            with (folder / "engine.log").open("wb") as log:
                async for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    value = line.decode("utf-8", errors="replace").strip()
                    if value.startswith("WAN_STATUS "):
                        state.update(json.loads(value[11:]))
                    elif value:
                        error_line = value[-480:]
            code = await proc.wait()
            if (folder / "video.mp4").is_file() and code == 0:
                state.update(status="done", stage="已完成")
            elif state["cancelled"]:
                state.update(status="cancelled", stage="已取消")
            else:
                raise RuntimeError(error_line or f"推理进程退出，代码 {code}")
        except Exception as exc:
            if state["cancelled"]:
                state.update(status="cancelled", stage="已取消")
            else:
                state.update(status="error", stage="生成失败", error=str(exc))
        finally:
            self.completion = None
            if self.process and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
            (folder / "partial.mp4").unlink(missing_ok=True)
            self.last, self.active, self.process = state, None, None

    async def submit(self, request):
        fields = await request.post()
        preset = CONFIG["presets"].get(fields.get("preset", ""))
        if not preset:
            raise ValueError("请选择有效画幅")
        prompt = str(fields.get("prompt", "")).strip()
        negative = str(fields.get("negative", CONFIG["negative_prompt"])).strip()
        if not 1 <= len(prompt) <= 4000 or len(negative) > 4000:
            raise ValueError("请输入提示词，正向和负向提示词各限 4000 字")
        steps = int(fields.get("steps", CONFIG["steps"]))
        frames = int(fields.get("frames", preset["frames"]))
        fps = int(fields.get("fps", preset["fps"]))
        if not 5 <= frames <= 121 or (frames - 1) % 4 or not 1 <= fps <= 30:
            raise ValueError("帧数须为 5–121 之间的 4n+1，帧率须为 1–30")
        seed_input = str(fields.get("seed", "")).strip()
        seed = int(seed_input) if seed_input else secrets.randbelow(2**32)
        if not 1 <= steps <= 40 or not 0 <= seed < 2**53:
            raise ValueError("步数范围为 1–40，种子范围为 0–9007199254740991")
        mode = fields.get("mode")
        if mode not in ("text", "image"):
            raise ValueError("无效生成模式")
        raw = None
        if mode == "image":
            upload = fields.get("image")
            if not hasattr(upload, "file"):
                raise ValueError("图生视频需要一张参考图")
            raw = upload.file.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError("图片大小上限为 16 MiB")
        if self.active or self.model["state"] in ("loading", "unloading"):
            raise web.HTTPConflict(text="正在生成视频或操作模型，请稍候")
        identity = secrets.token_hex(8)
        folder = OUTPUT / identity
        folder.mkdir()
        try:
            if raw is not None:
                save_reference(raw, folder / "input.png")
            job = dict(id=identity, mode=mode, prompt=prompt, negative=negative, seed=seed, steps=steps,
                       width=preset["width"], height=preset["height"], fps=fps, frames=frames,
                       sampling={k: CONFIG[k] for k in ("sampler","scheduler","cfg","shift","denoise","decode")},
                       created=int(time.time()))
            write_json(folder / "request.json", job)
        except Exception:
            if not any(folder.iterdir()):
                folder.rmdir()
            raise
        self.active = dict(id=identity, status="running", stage="正在准备", current=0, total=0,
                           cancelled=False, preloaded=self.model["state"] == "ready", started=time.time())
        self.task = asyncio.create_task(self.generate(job, folder))
        return web.json_response(self.active, status=202)

    async def cancel(self, request):
        if self.active and self.active["id"] == request.match_info["id"]:
            self.active.update(cancelled=True, stage="正在取消")
            if self.process and self.process.returncode is None:
                self.process.terminate()
            if self.active["preloaded"]:
                await self.stop_resident()
        return web.json_response({"ok": True})

    async def cleanup(self, app):
        if self.active:
            self.active["cancelled"] = True
        if self.process and self.process.returncode is None:
            self.process.terminate()
        await self.stop_resident()
        if self.task:
            await self.task


@web.middleware
async def local_requests(request, handler):
    if request.host != BASE.split("//")[1]:
        raise web.HTTPForbidden()
    if request.method == "POST" and (request.headers.get("X-Wan-Request") != "1" or
            request.headers.get("Origin", BASE) != BASE):
        raise web.HTTPForbidden()
    try:
        return await handler(request)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc


async def main():
    studio = Studio()
    OUTPUT.mkdir(exist_ok=True)
    async def index(request):
        return web.FileResponse(ROOT / "index.html")
    async def status(request):
        return web.json_response({"app":"Wan Video", "presets": CONFIG["presets"], "steps": CONFIG["steps"],
                                  "negative": CONFIG["negative_prompt"],
                                  "sampling": {k: CONFIG[k] for k in ("sampler","scheduler","cfg","shift")},
                                  "model": studio.model, "active": studio.active, "last": studio.last, "history": studio.history()})
    async def video(request):
        name = request.match_info["id"]
        path = OUTPUT / name / "video.mp4"
        if not re.fullmatch(r"[0-9a-f]{16}", name) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type":"video/mp4"})

    app = web.Application(client_max_size=LIMIT + 65536, middlewares=[local_requests])
    app.add_routes([web.get("/",index),web.get("/api/status",status),web.post("/api/jobs",studio.submit),
                    web.post("/api/jobs/{id}/cancel",studio.cancel),web.get("/video/{id}.mp4",video),
                    web.post("/api/model/preload",studio.preload),web.post("/api/model/unload",studio.unload)])
    app.on_cleanup.append(studio.cleanup)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner,"127.0.0.1",PORT).start()
    except OSError:
        async with ClientSession() as client:
            async with client.get(BASE+"/api/status") as res:
                if (await res.json()).get("app") != "Wan Video":
                    raise RuntimeError("指定端口已被其他程序占用")
        if "--no-browser" not in sys.argv:
            webbrowser.open(BASE)
        return
    print(BASE,flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(BASE)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
