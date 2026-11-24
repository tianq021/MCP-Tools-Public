"""Feiyu 大肥鱼 TTS 冒烟测试：加载 CosyVoice3 + 指定声线检查点，合成并计时。

用法:
    python smoke.py --style cute  --runs 3
    python smoke.py --style gentle --text "..." --fp16
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feiyu_env  # noqa: E402

feiyu_env.prepare()
REPO, PKG = feiyu_env.REPO, feiyu_env.PKG

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="cute", choices=["cute", "gentle"])
    ap.add_argument("--text", default="你好呀，今天想聊点什么？")
    ap.add_argument("--out", default="")
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--runs", type=int, default=3, help="同一句重复合成次数（第 1 次含预热）")
    ap.add_argument("--text-frontend", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--stream", action="store_true", help="流式生成，记录首块延迟")
    ap.add_argument("--threads", type=int, default=4, help="CPU 推理线程数")
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    manifest = json.loads(open(os.path.join(PKG, "manifest.json"), encoding="utf-8").read())
    style = manifest["styles"][args.style]
    params = manifest["inference"]
    model_dir = os.path.join(PKG, manifest["base_model_dir"])
    ckpt = os.path.join(PKG, style["llm_checkpoint"])
    ref = os.path.join(PKG, style["reference_audio"])
    out = args.out or fr"E:\feiyu\outputs\smoke_{args.style}.wav"
    os.makedirs(os.path.dirname(out), exist_ok=True)

    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    use_cuda = torch.cuda.is_available()
    print(f"[1] 加载模型 {model_dir} fp16={args.fp16} device={'cuda' if use_cuda else 'cpu'}", flush=True)
    if use_cuda:
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    voice = AutoModel(model_dir=model_dir, fp16=args.fp16, load_trt=False, load_vllm=False)
    load_s = time.time() - t0
    if use_cuda:
        print(
            f"[1] 加载完成 {load_s:.2f}s，前端={voice.frontend.text_frontend or '(无)'}，"
            f"显存 allocated={torch.cuda.memory_allocated() / 2**30:.2f} GiB "
            f"reserved={torch.cuda.memory_reserved() / 2**30:.2f} GiB",
            flush=True,
        )
    else:
        print(f"[1] 加载完成 {load_s:.2f}s，前端={voice.frontend.text_frontend or '(无)'}，device=cpu", flush=True)

    default_llm = os.path.join(model_dir, "llm.pt")
    if os.path.abspath(ckpt) != os.path.abspath(default_llm):
        print(f"[2] 载入声线检查点 {os.path.basename(ckpt)}", flush=True)
        state = torch.load(ckpt, map_location="cpu", weights_only=True)
        voice.model.llm.load_state_dict(state, strict=True)
        del state
    else:
        print("[2] 使用原版 llm.pt（gentle 声线）", flush=True)
    voice.model.llm.eval()
    set_all_random_seed(params["seed"])
    prompt_text = params["prompt_prefix"] + style["reference_text"]
    use_frontend = {"auto": True, "on": True, "off": False}[args.text_frontend]

    print(f"[3] 合成（{args.runs} 次）: {args.text}", flush=True)
    runs = []
    audio = None
    for i in range(args.runs):
        t1 = time.time()
        first_chunk_s = None
        parts = []
        with torch.inference_mode():
            for x in voice.inference_zero_shot(
                args.text, prompt_text, ref, stream=args.stream, speed=params["speed"], text_frontend=use_frontend
            ):
                if first_chunk_s is None:
                    first_chunk_s = time.time() - t1
                parts.append(x["tts_speech"].detach().cpu().numpy()[0])
        infer_s = time.time() - t1
        audio = np.concatenate(parts)
        dur = len(audio) / voice.sample_rate
        runs.append(
            {
                "run": i + 1,
                "audio_seconds": round(dur, 3),
                "infer_seconds": round(infer_s, 3),
                "first_chunk_seconds": round(first_chunk_s, 3) if first_chunk_s else None,
                "chunks": len(parts),
                "rtf": round(infer_s / max(dur, 1e-6), 3),
                "vram_peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 3) if use_cuda else None,
            }
        )
        print(
            f"    第 {i + 1} 次: 音频 {dur:.2f}s / 耗时 {infer_s:.2f}s / RTF {infer_s / max(dur, 1e-6):.2f}"
            + (f" / 首块 {first_chunk_s:.2f}s（{len(parts)} 块）" if first_chunk_s else ""),
            flush=True,
        )

    peak = float(abs(audio).max())
    gain = min(1.0, 0.98 / peak) if peak > 0 else 1.0
    sf.write(out, audio * gain, voice.sample_rate, subtype="PCM_16")
    stats = {
        "style": args.style,
        "text": args.text,
        "text_frontend": voice.frontend.text_frontend,
        "sample_rate": voice.sample_rate,
        "load_seconds": round(load_s, 3),
        "steady_rtf": runs[-1]["rtf"],
        "runs": runs,
        "vram_allocated_gib": round(torch.cuda.memory_allocated() / 2**30, 3) if use_cuda else None,
        "vram_peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 3) if use_cuda else None,
        "peak_amplitude": round(peak, 4),
        "out": out,
    }
    print("[4] 完成\n" + json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())


