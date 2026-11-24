"""DSH tools for the existing local Qwen Atelier service and gallery."""
from datetime import datetime
import http.client
import json
import re
from typing import Literal
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP


PORT = 17860
BASE = f"http://127.0.0.1:{PORT}"
mcp = FastMCP("qwen_atelier", instructions=(
    "本机 Qwen Atelier 文生图和 AI 扩图。先看性能、队列与历史耗时，再提交。"
    "默认普通题材。只有本轮明确需要成人内容才选择 adult_requested；意图不清先询问。"
    "提交后查询同一任务号，完成后给出图片链接、实际尺寸、生成时间和耗时。"
))


def api(path, body=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    headers = {"Accept": "application/json"}
    if body is not None:
        headers.update({"Content-Type": "application/json", "X-Studio-Token": token})
    try:
        conn.request("GET" if body is None else "POST", "/api/" + path,
                     body=None if body is None else json.dumps(body).encode(), headers=headers)
        response = conn.getresponse()
        payload = json.loads(response.read())
        if response.status >= 400:
            raise ValueError(f"Studio HTTP {response.status}: {payload.get('detail')}")
        return payload
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        hint = ("提交结果未知，请用 job_status 查看最近任务，勿自动重发。"
                if body is not None else "请检查 Qwen Atelier 是否已通过「启动网页.cmd」启动。")
        raise RuntimeError(f"本机 Studio 无法正常响应。{hint}") from exc
    finally:
        conn.close()


def local_time(value):
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")
    return None


@mcp.tool()
def check_performance() -> dict:
    """检查显存、系统内存（字节）、电源、模型和队列。可提交不保证任意分辨率都有足够显存。"""
    status = api("status")
    engine, models = status["engine"], status["models"]
    blockers = list(models["blockers"])
    if engine["power"]["blocked"]:
        blockers.append("电源保护已触发")
    if engine["draining"] or engine["engine_lost"] or engine["state"] == "loading":
        blockers.append("引擎尚未就绪，请检查引擎状态")
    if engine["queued"] >= 8:
        blockers.append("队列已满")
    return {"can_submit": not blockers, "blockers": blockers, "gpu": status["gpu"],
            "memory": status["memory"], "engine": engine, "models": models["components"]}


def job_summary(job):
    summary = {key: job.get(key) for key in ("id", "status", "stage", "step", "total_steps",
               "progress", "time_estimate", "image_ids", "error")}
    summary.update({key + "_at": local_time(job.get(key)) for key in ("created", "started", "finished")})
    start, end = job.get("started"), job.get("finished")
    summary["job_seconds"] = round(end - start, 3) if start is not None and end is not None else None
    summary["images"] = [{"id": i, "url": f"{BASE}/api/images/{i}/file"} for i in job.get("image_ids", [])]
    return summary


@mcp.tool()
def generate_image(prompt: str, content_confirmed: bool,
                   content_intent: Literal["general", "adult_requested", "unclear"] = "general",
                   negative_prompt: str = "", width: int = 512, height: int = 512, steps: int = 20,
                   seed: int = -1, cfg: float = 1.0, batch_count: int = 1,
                   sampler: Literal["euler", "euler_a", "heun", "dpm++2m"] = "euler",
                   scheduler: Literal["default", "simple", "discrete", "karras"] = "default",
                   output_bit_depth: Literal[8, 16] = 8, vae_tiling: bool = False) -> dict:
    """DSH 聊天生图入口。提交后用 job_status 跟踪同一任务直到完成，再用 image_info 取原图、生成时刻与耗时，在聊天中展示或链接原图。普通题材默认 general，不主动成人化。仅当用户本次明确要求成人题材才选 adult_requested，模型支持或历史偏好不算授权。拿不准时先询问。content_confirmed 表示本次请求符合 Studio 内容约定（无极端血腥或欺骗用途）。"""
    if content_intent == "unclear":
        raise ValueError("尚不清楚本次是否需要成人题材，请先询问用户")
    if not content_confirmed or not prompt.strip():
        raise ValueError("需要明确的画面描述，并符合 Studio 内容约定")
    status = check_performance()
    if not status["can_submit"]:
        raise ValueError("；".join(status["blockers"]))
    session = api("session")
    if session.get("app") != "Qwen Atelier":
        raise RuntimeError(f"{PORT} 端口未返回预期的 Qwen Atelier 服务")
    job = api("jobs", {"prompt": prompt, "negative_prompt": negative_prompt, "width": width,
              "height": height, "steps": steps, "seed": seed, "cfg": cfg, "batch_count": batch_count,
              "sampler": sampler, "scheduler": scheduler, "output_bit_depth": output_bit_depth,
              "vae_tiling": vae_tiling, "content_confirmed": content_confirmed}, session["token"])
    return job_summary(job)


@mcp.tool()
def outpaint_image(source_id: str, prompt: str, content_confirmed: bool,
                   source_type: Literal["gallery", "upload"] = "gallery",
                   left: int = 128, right: int = 128, top: int = 0, bottom: int = 0,
                   seed: int = -1, seam_mode: Literal["strict", "feather"] = "strict") -> dict:
    """用 Qwen Outpaint LoRA v2 扩展已有图片。source_id 为 image_history 的图库 ID 或 17860 扩图页的上传 ID。四边单位为像素；strict 保留原图像素。描述自然延续的场景，成人主题仅在本次明确要求时使用。不确定先询问。任务入队后用 job_status 跟踪，完成后展示原图链接。"""
    if not re.fullmatch("[0-9a-f]{32}", source_id) or not prompt.strip() or not content_confirmed:
        raise ValueError("需要有效的图片 ID、场景描述，并符合工作台内容约定")
    capability = api("image-tools/capabilities")["outpaint"]
    if not capability["available"] or capability.get("adapter") != "qwen-image-2.1-outpaint-v2":
        raise ValueError(capability.get("reason") or f"{PORT} 的扩图 LoRA v2 尚未就绪")
    token = api("session")["token"]
    input_id = source_id
    if source_type == "gallery":
        input_id = api("image-tools/inputs/from-image", {"image_id": source_id}, token)["id"]
    params = api("image-tools/defaults/outpaint")
    params.update(extend_left=left, extend_right=right, extend_top=top, extend_bottom=bottom,
                  prompt=prompt, seed=seed, seam_mode=seam_mode)
    job = api("image-tools/jobs", {"task_type": "outpaint", "input_id": input_id, "params": params,
                                  "content_confirmed": content_confirmed}, token)
    return job_summary(job)


@mcp.tool()
def job_status(job_id: str = "") -> dict:
    """查询任务进度、预计剩余时间、排队/开始/结束时刻、任务耗时及图片链接。省略 ID 显示最近 5 项。"""
    jobs = sorted(api("jobs") + [j for j in api("image-tools/jobs") if j.get("task_type") == "outpaint"],
                  key=lambda j: j.get("created", 0), reverse=True)
    if not job_id:
        return {"jobs": [job_summary(j) for j in jobs[:5]]}
    for job in jobs:
        if job["id"] == job_id:
            return job_summary(job)
    raise ValueError("工作台最近的任务中没有此 ID；较早作品可在 image_history 查询")


def image_summary(item):
    cfg = item["config"]
    return {**{key: item.get(key) for key in ("id", "name", "width", "height", "bit_depth", "size")},
            "generated_at": local_time(cfg.get("generated_at") or cfg.get("finished")),
            "file_modified_at": local_time(item.get("created")),
            "inference_seconds": cfg.get("elapsed_seconds", (cfg.get("execution") or {}).get("seconds")),
            "steps": cfg.get("steps", (cfg.get("params") or {}).get("steps")),
            "seed": cfg.get("seed", (cfg.get("params") or {}).get("seed")),
            "url": f"{BASE}/api/images/{item['id']}/file"}


@mcp.tool()
def image_history(query: str = "", limit: int = 5) -> dict:
    """按名称或关键词查普通图库，返回实际像素、生成时刻和单图推理秒数。缺失的时间显示为空。"""
    if not 1 <= limit <= 50:
        raise ValueError("limit 应在 1 至 50 之间")
    rows = api("images?" + urlencode({"q": query}))
    return {"matches": len(rows), "images": [image_summary(row) for row in rows[:limit]]}


@mcp.tool()
def image_info(image_id: str) -> dict:
    """按图片 ID 查原图路径、链接、实际尺寸、位深、哈希、提示词、模型、采样参数和生成耗时。"""
    if not re.fullmatch("[0-9a-f]{32}", image_id):
        raise ValueError("请输入图库返回的图片 ID")
    item = api("images/" + image_id)
    if item.get("deleted") or item.get("tag") == "blocked":
        raise ValueError("图片不在可见图库")
    cfg = item["config"]
    if cfg.get("task_type") == "outpaint":
        return {**image_summary(item), "path": item["path"], "sha256": item["sha256"],
                "parameters": cfg.get("params"), "adapter": (cfg.get("execution") or {}).get("adapter"),
                "preservation": cfg.get("preservation")}
    return {**image_summary(item), "path": item["path"], "sha256": item["sha256"],
            "parameters": {key: cfg.get(key) for key in ("prompt", "negative_prompt", "width",
                "height", "cfg", "sampler", "scheduler", "vae_tiling", "models", "engine", "input")}}


if __name__ == "__main__":
    mcp.run(transport="stdio")
