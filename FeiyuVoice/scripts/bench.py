"""Feiyu 热态性能分解：prompt 提取 / LLM / flow+hift，以及流式 chunk 参数对首块延迟的影响。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feiyu_env  # noqa: E402

feiyu_env.prepare()
PKG = feiyu_env.PKG

import numpy as np  # noqa: E402
import torch  # noqa: E402


def sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def main() -> int:
    torch.set_num_threads(4)
    tf32 = "--tf32" in sys.argv
    if tf32:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.set_float32_matmul_precision("high")
        print("[0] TF32 已开启", flush=True)
    manifest = json.loads(open(os.path.join(PKG, "manifest.json"), encoding="utf-8").read())
    style = manifest["styles"]["cute"]
    params = manifest["inference"]
    model_dir = os.path.join(PKG, manifest["base_model_dir"])
    ckpt = os.path.join(PKG, style["llm_checkpoint"])
    ref = os.path.join(PKG, style["reference_audio"])
    prompt_text = params["prompt_prefix"] + style["reference_text"]
    text = "今天辛苦了，要不要先休息一下？我等下再跟你讲后面的安排和明天的计划。"

    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    print("[1] 加载模型（fp32）", flush=True)
    t = time.perf_counter()
    voice = AutoModel(model_dir=model_dir, fp16=False, load_trt=False, load_vllm=False)
    sync()
    print(f"    加载 {time.perf_counter() - t:.2f}s", flush=True)
    state = torch.load(ckpt, map_location="cpu", weights_only=True)
    voice.model.llm.load_state_dict(state, strict=True)
    del state
    voice.model.llm.eval()
    set_all_random_seed(params["seed"])

    # 预热一次，排除首次 CUDA 初始化开销
    with torch.inference_mode():
        for _ in voice.inference_zero_shot("预热一下。", prompt_text, ref, stream=False):
            pass
    sync()

    result = {"text": text}

    # --- A. prompt 提取：冷 vs 缓存 ---
    t = time.perf_counter()
    mi = voice.frontend.frontend_zero_shot(text, prompt_text, ref, voice.sample_rate, "")
    sync()
    result["frontend_cold_s"] = round(time.perf_counter() - t, 3)

    voice.add_zero_shot_spk(prompt_text, ref, "feiyu_cute")
    t = time.perf_counter()
    mi = voice.frontend.frontend_zero_shot(text, prompt_text, ref, voice.sample_rate, "feiyu_cute")
    sync()
    result["frontend_cached_s"] = round(time.perf_counter() - t, 3)

    # --- B. 非流式分解：token2wav 计时包装 ---
    orig_token2wav = voice.model.token2wav
    t2w = {"total": 0.0}

    def wrapped(*a, **k):
        s = time.perf_counter()
        out = orig_token2wav(*a, **k)
        t2w["total"] += time.perf_counter() - s
        return out

    voice.model.token2wav = wrapped
    t = time.perf_counter()
    parts = [x["tts_speech"].detach().cpu().numpy()[0] for x in voice.inference_zero_shot(
        text, prompt_text, ref, zero_shot_spk_id="feiyu_cute", stream=False)]
    sync()
    total = time.perf_counter() - t
    audio = np.concatenate(parts)
    dur = len(audio) / voice.sample_rate
    voice.model.token2wav = orig_token2wav
    result.update(
        {
            "nonstream_total_s": round(total, 3),
            "audio_seconds": round(dur, 3),
            "rtf": round(total / dur, 3),
            "frontend_cached_used_s": result["frontend_cached_s"],
            "token2wav_s": round(t2w["total"], 3),
            "llm_plus_overhead_s": round(total - t2w["total"] - result["frontend_cached_s"], 3),
        }
    )

    # --- C. 流式 chunk 参数对首块延迟的影响 ---
    stream_cases = [
        ("默认 scale=2 hop=25", 2, 25),
        ("scale=1 hop=25", 1, 25),
        ("scale=1 hop=15", 1, 15),
    ]
    stream_results = []
    for name, scale, hop in stream_cases:
        voice.model.stream_scale_factor = scale
        voice.model.token_hop_len = hop
        voice.model.token_max_hop_len = 4 * 25
        t = time.perf_counter()
        first = None
        chunks = 0
        audio_parts = []
        with torch.inference_mode():
            for x in voice.inference_zero_shot(text, prompt_text, ref, zero_shot_spk_id="feiyu_cute", stream=True):
                if first is None:
                    first = time.perf_counter() - t
                chunks += 1
                audio_parts.append(x["tts_speech"].detach().cpu().numpy()[0])
        sync()
        total_s = time.perf_counter() - t
        dur_s = sum(len(p) for p in audio_parts) / voice.sample_rate
        stream_results.append(
            {
                "case": name,
                "first_chunk_s": round(first, 3) if first else None,
                "chunks": chunks,
                "total_s": round(total_s, 3),
                "audio_seconds": round(dur_s, 3),
                "rtf": round(total_s / max(dur_s, 1e-6), 3),
            }
        )
        print(f"    {name}: 首块 {first:.2f}s / {chunks} 块 / 总 {total_s:.2f}s / 音频 {dur_s:.2f}s", flush=True)
    result["stream_cases"] = stream_results
    result["vram_peak_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    print("\n[2] 结果\n" + json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
