"""容器内（Linux/CUDA）测试：CosyVoice3 + F6 + 可选 vLLM 加速，对比 RTF 与首块延迟。

运行（宿主机）：
  docker run --rm --gpus all -v E:\\feiyu:/data feiyu-voice:vllm \
      python /data/docker/run_vllm_test.py --vllm

说明：
- 模型与源码都从 /data 挂载（Windows 目录只读使用，vllm 导出目录除外）。
- F6 检查点在调用 load_vllm 之前载入，保证导出的 vLLM 权重就是可爱声线。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

DATA = "/data"
REPO = os.path.join(DATA, "CosyVoice")
PKG = os.path.join(DATA, "FeiyuVoice-CosyVoice3")
WETEXT_RES = os.path.join(DATA, "wetext-resources")
sys.path.insert(0, os.path.join(REPO, "third_party", "Matcha-TTS"))
sys.path.insert(0, REPO)

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("OMP_NUM_THREADS", "4")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402


def patch_env() -> None:
    # wetext 资源本地化（容器内没有登录态，禁止联网下载）
    import wetext.wetext as ww

    def _snapshot(repo_id, *a, **k):
        if repo_id == "pengzhendong/wetext":
            return WETEXT_RES
        raise ValueError(f"unexpected snapshot_download({repo_id})")

    ww.snapshot_download = _snapshot

    # torchaudio 新版 load 需要 torchcodec，这里统一走 soundfile
    import cosyvoice.cli.frontend as fe
    import cosyvoice.utils.file_utils as fu
    from torchaudio.transforms import Resample

    def load_wav(wav, target_sr):
        speech, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        speech = torch.from_numpy(speech.T)
        if sr != target_sr:
            speech = Resample(orig_freq=sr, new_freq=target_sr)(speech)
        return speech

    fu.load_wav = load_wav
    fe.load_wav = load_wav


def sync() -> None:
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="cute", choices=["cute", "gentle"])
    ap.add_argument("--text", default="今天辛苦了，要不要先休息一下？我等下再跟你讲后面的安排和明天的计划。")
    ap.add_argument("--vllm", action="store_true", help="启用 vLLM 加速 LLM 段")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    torch.set_num_threads(4)
    patch_env()

    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    if args.vllm:
        from vllm import ModelRegistry

        from cosyvoice.vllm.cosyvoice2 import CosyVoice2ForCausalLM

        ModelRegistry.register_model("CosyVoice2ForCausalLM", CosyVoice2ForCausalLM)

    manifest = json.loads(open(os.path.join(PKG, "manifest.json"), encoding="utf-8").read())
    style = manifest["styles"][args.style]
    params = manifest["inference"]
    model_dir = os.path.join(PKG, manifest["base_model_dir"])
    ckpt = os.path.join(PKG, style["llm_checkpoint"])
    ref = os.path.join(PKG, style["reference_audio"])
    prompt_text = params["prompt_prefix"] + style["reference_text"]

    print(f"[1] torch={torch.__version__} cuda={torch.cuda.is_available()} gpu={torch.cuda.get_device_name(0)}", flush=True)
    if args.vllm:
        import vllm

        print(f"    vllm={vllm.__version__}", flush=True)
    torch.cuda.reset_peak_memory_stats()
    t = time.perf_counter()
    voice = AutoModel(model_dir=model_dir, fp16=False, load_trt=False, load_vllm=False)
    sync()
    print(f"[1] 模型加载 {time.perf_counter() - t:.2f}s 显存={torch.cuda.memory_allocated() / 2**30:.2f} GiB", flush=True)

    if os.path.abspath(ckpt) != os.path.abspath(os.path.join(model_dir, "llm.pt")):
        state = torch.load(ckpt, map_location="cpu", weights_only=True)
        voice.model.llm.load_state_dict(state, strict=True)
        del state
    voice.model.llm.eval()
    set_all_random_seed(params["seed"])

    if args.vllm:
        print("[2] 导出 vLLM 权重并启动引擎（使用当前 F6 权重）", flush=True)
        # CosyVoice 源码里写死 gpu_memory_utilization=0.2，8GB 卡装不下 KV 缓存；
        # 这里在运行时把 vllm.EngineArgs 包一层，允许用环境变量调整。
        import vllm as _vllm

        _orig_engine_args = _vllm.EngineArgs
        _util = float(os.environ.get("FEIYU_VLLM_GPU_UTIL", "0.55"))
        _maxlen = int(os.environ.get("FEIYU_VLLM_MAXLEN", "1024"))

        class _PatchedEngineArgs(_orig_engine_args):  # type: ignore[misc,valid-type]
            def __init__(self, *a, **kw):
                kw.setdefault("gpu_memory_utilization", _util)
                kw.setdefault("max_model_len", _maxlen)
                super().__init__(*a, **kw)

        _vllm.EngineArgs = _PatchedEngineArgs
        t = time.perf_counter()
        voice.model.load_vllm(os.environ.get("FEIYU_VLLM_DIR", os.path.join(model_dir, "vllm")))
        sync()
        print(f"[2] vLLM 引擎就绪 {time.perf_counter() - t:.2f}s 显存={torch.cuda.memory_allocated() / 2**30:.2f} GiB", flush=True)

    # 参考音频特征缓存
    t = time.perf_counter()
    voice.add_zero_shot_spk(prompt_text, ref, "feiyu")
    print(f"[3] 参考音频缓存 {time.perf_counter() - t:.3f}s", flush=True)

    result = {"style": args.style, "vllm": args.vllm, "torch": torch.__version__, "text": args.text}
    runs = []
    audio = None
    for i in range(args.runs):
        t = time.perf_counter()
        parts = [
            x["tts_speech"].detach().cpu().numpy()[0]
            for x in voice.inference_zero_shot(
                args.text, prompt_text, ref, zero_shot_spk_id="feiyu", stream=False, text_frontend=True
            )
        ]
        sync()
        total = time.perf_counter() - t
        audio = np.concatenate(parts)
        dur = len(audio) / voice.sample_rate
        runs.append({"run": i + 1, "audio_s": round(dur, 3), "total_s": round(total, 3), "rtf": round(total / dur, 3)})
        print(f"    第 {i + 1} 次: 音频 {dur:.2f}s / 耗时 {total:.2f}s / RTF {total / dur:.2f}", flush=True)

    # 流式首块
    voice.model.stream_scale_factor = 2
    voice.model.token_hop_len = 25
    t = time.perf_counter()
    first = None
    chunks = 0
    stream_parts = []
    with torch.inference_mode():
        for x in voice.inference_zero_shot(
            args.text, prompt_text, ref, zero_shot_spk_id="feiyu", stream=True, text_frontend=True
        ):
            if first is None:
                first = time.perf_counter() - t
            chunks += 1
            stream_parts.append(x["tts_speech"].detach().cpu().numpy()[0])
    sync()
    stream_total = time.perf_counter() - t
    stream_dur = sum(len(p) for p in stream_parts) / voice.sample_rate
    result.update(
        {
            "load_and_engine": None,
            "runs": runs,
            "stream": {
                "first_chunk_s": round(first, 3) if first else None,
                "chunks": chunks,
                "total_s": round(stream_total, 3),
                "audio_s": round(stream_dur, 3),
            },
            "vram_peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 3),
        }
    )
    out = os.path.join(DATA, "outputs", f"vllm_{int(args.vllm)}_{args.style}.wav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    peak = float(abs(audio).max())
    sf.write(out, audio * (min(1.0, 0.98 / peak) if peak > 0 else 1.0), voice.sample_rate, subtype="PCM_16")
    result["out"] = out
    print("[4] 结果\n" + json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

