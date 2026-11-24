"""Direct Wan inference with optional reuse of the loaded model objects."""
import gc
import json
import os
from pathlib import Path
import sys


def progress(stage, current=0, total=0):
    print("WAN_STATUS " + json.dumps(dict(stage=stage, current=current, total=total)), flush=True)


def load_vae(weights):
    import comfy.sd
    import comfy.utils

    sd, metadata = comfy.utils.load_torch_file(weights["vae"], return_metadata=True)
    vae = comfy.sd.VAE(sd=sd, metadata=metadata)
    vae.throw_exception_if_invalid()
    return vae


def load_clip(weights):
    import comfy.sd
    return comfy.sd.load_clip(ckpt_paths=[weights["encoder"]], clip_type=comfy.sd.CLIPType.WAN)


def release():
    import comfy.model_management as memory
    memory.unload_all_models()
    gc.collect()
    memory.soft_empty_cache()


def generate(folder, config, cache=None):
    import torch
    import av
    import numpy as np
    from PIL import Image
    import comfy.sd
    import comfy.utils
    import nodes
    from comfy_extras.nodes_model_advanced import ModelSamplingSD3
    from comfy_extras.nodes_wan import Wan22ImageToVideoLatent

    job = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    sampling = job.get("sampling", config)
    weights = config["models"]
    with torch.inference_mode():
        progress("编码提示词")
        clip = cache["encoder"] if cache else load_clip(weights)
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(job["prompt"]))
        negative = clip.encode_from_tokens_scheduled(clip.tokenize(job["negative"]))
        del clip
        release()

        progress("准备视频潜空间")
        vae = None
        image = None
        if job["mode"] == "image":
            vae = cache["vae"] if cache else load_vae(weights)
            with Image.open(folder / "input.png") as im:
                image = torch.from_numpy(np.array(im.convert("RGB"), dtype=np.float32) / 255.0).unsqueeze(0)
        latent = Wan22ImageToVideoLatent.execute(vae, job["width"], job["height"], job["frames"], 1, image).result[0]
        del vae, image
        release()

        progress("复用 Wan 2.2 主模型" if cache else "加载 Wan 2.2 主模型")
        model = cache["diffusion"] if cache else comfy.sd.load_diffusion_model(weights["diffusion"])
        model = ModelSamplingSD3().patch(model, sampling["shift"])[0]
        progress("采样", 0, job["steps"])
        comfy.utils.set_progress_bar_global_hook(lambda value, total, *a, **kw: progress("采样", value, total))
        try:
            samples = nodes.common_ksampler(model, job["seed"], job["steps"], sampling["cfg"],
                sampling["sampler"], sampling["scheduler"], positive, negative, latent, denoise=sampling["denoise"])[0]
        finally:
            comfy.utils.set_progress_bar_global_hook(None)
        del model, positive, negative, latent
        release()

        progress("分块解码")
        vae = cache["vae"] if cache else load_vae(weights)
        frames = nodes.VAEDecodeTiled().decode(vae, samples, **sampling["decode"])[0]
        if len(frames) != job["frames"]:
            raise ValueError(f"解码得到 {len(frames)} 帧，预期 {job['frames']} 帧")
        progress("编码 MP4")
        temp = folder / "partial.mp4"
        with av.open(str(temp), "w") as video:
            stream = video.add_stream("libx264", rate=job["fps"])
            stream.width, stream.height = job["width"], job["height"]
            stream.pix_fmt = "yuv420p"
            stream.options = {"crf": "18", "preset": "fast"}
            for frame in frames:
                rgb = frame[..., :3].detach().clamp(0, 1).mul(255).round().to(torch.uint8).cpu().numpy()
                for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                    video.mux(packet)
            for packet in stream.encode():
                video.mux(packet)
        os.replace(temp, folder / "video.mp4")


def resident(config):
    import torch
    import comfy.sd
    import nodes  # Initialize the same inference runtime used by generation.

    weights = config["models"]
    with torch.inference_mode():
        cache = {}
        progress("加载文本编码器", 0, 3)
        cache["encoder"] = load_clip(weights)
        progress("加载视频 VAE", 1, 3)
        cache["vae"] = load_vae(weights)
        progress("加载 Wan 2.2 主模型", 2, 3)
        cache["diffusion"] = comfy.sd.load_diffusion_model(weights["diffusion"])
        if cache["diffusion"] is None:
            raise RuntimeError("无法识别 Wan 主模型")
        release()
        print("WAN_READY", flush=True)
        for line in sys.stdin:
            folder = Path(json.loads(line)["folder"])
            generate(folder, config, cache)
            release()
            print("WAN_DONE", flush=True)


if __name__ == "__main__":
    config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
    if sys.argv[1] == "--resident":
        resident(config)
    else:
        generate(Path(sys.argv[1]), config)
