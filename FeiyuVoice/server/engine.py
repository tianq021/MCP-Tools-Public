"""大肥鱼（Feiyu）语音引擎：常驻内存，按声线缓存参考音频特征，支持中英文数字读法。

设计要点（针对本机 RTF ≈ 1.5 的现状）：
- 模型只在启动时加载一次（约 19 秒），之后常驻，不再重复加载；
- 两种声线的参考音频特征在启动时就算好并缓存，单次请求省掉约 1 秒；
- 因为 GPU 只能串行推理，内部用一把锁，调用方拿到的是"排队后"的结果；
- 检查点切换（可爱 F6 ↔ 温柔原版 llm.pt）需要重新载入 1.93GB 权重，约 1~3 秒。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import feiyu_env  # noqa: E402

feiyu_env.prepare(verbose=False)

import numpy as np  # noqa: E402
import torch  # noqa: E402


def release_host_memory() -> None:
    """把已释放的堆内存还给系统：Linux 用 glibc malloc_trim，Windows 只做 gc。"""
    import gc

    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass


class FeiyuEngine:
    def __init__(self, verbose: bool = True) -> None:
        self.pkg = feiyu_env.PKG
        manifest_path = os.path.join(self.pkg, "manifest.json")
        self.manifest = json.loads(open(manifest_path, encoding="utf-8").read())
        self.base_dir = os.path.join(self.pkg, self.manifest["base_model_dir"])
        self.params = self.manifest["inference"]
        self.verbose = verbose
        self._lock = threading.Lock()
        self._voice = None
        self._current_style: str | None = None
        self._speaker_ready: set[str] = set()

    # ---------- 加载 ----------
    def load(self) -> None:
        from cosyvoice.cli.cosyvoice import AutoModel

        t = time.perf_counter()
        torch.set_num_threads(4)
        self._voice = AutoModel(model_dir=self.base_dir, fp16=False, load_trt=False, load_vllm=False)
        if self.verbose:
            print(f"[engine] 模型加载 {time.perf_counter() - t:.1f}s，前端={self._voice.frontend.text_frontend}", flush=True)
        # 两种声线的参考特征都提前算好
        for style in self.manifest["styles"]:
            self._ensure_speaker(style)
        self._current_style = None
        release_host_memory()

    def _style_conf(self, style: str) -> dict:
        if style not in self.manifest["styles"]:
            raise ValueError(f"不支持的声线: {style}，可选 {list(self.manifest['styles'])}")
        return self.manifest["styles"][style]

    def _ensure_speaker(self, style: str) -> None:
        """把某声线的参考音频特征缓存进 frontend（只算一次）。"""
        if style in self._speaker_ready:
            return
        conf = self._style_conf(style)
        prompt_text = self.params["prompt_prefix"] + conf["reference_text"]
        self._voice.add_zero_shot_spk(prompt_text, os.path.join(self.pkg, conf["reference_audio"]), style)
        self._speaker_ready.add(style)
        if self.verbose:
            print(f"[engine] 声线 {style} 参考特征已缓存", flush=True)

    def _ensure_style(self, style: str) -> None:
        if self._current_style == style:
            return
        from cosyvoice.utils.common import set_all_random_seed

        conf = self._style_conf(style)
        ckpt = os.path.join(self.pkg, conf["llm_checkpoint"])
        default_llm = os.path.join(self.base_dir, "llm.pt")
        t = time.perf_counter()
        if os.path.abspath(ckpt) == os.path.abspath(default_llm):
            state = torch.load(default_llm, map_location="cpu", weights_only=True)
            self._voice.model.llm.load_state_dict(state, strict=True)
            del state
        else:
            state = torch.load(ckpt, map_location="cpu", weights_only=True)
            self._voice.model.llm.load_state_dict(state, strict=True)
            del state
        self._voice.model.llm.eval()
        release_host_memory()
        set_all_random_seed(self.params["seed"])
        self._current_style = style
        if self.verbose:
            print(f"[engine] 切换到 {style} 声线，耗时 {time.perf_counter() - t:.1f}s", flush=True)

    # ---------- 合成 ----------
    def synthesize(self, text: str, style: str = "cute", speed: float | None = None, cancel: threading.Event | None = None) -> tuple[np.ndarray, int]:
        """阻塞式合成，返回 (float32 波形, 采样率)。cancel 置位时抛 CancelledError。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        with self._lock:
            self._ensure_speaker(style)
            self._ensure_style(style)
            conf = self._style_conf(style)
            prompt_text = self.params["prompt_prefix"] + conf["reference_text"]
            speed = self.params["speed"] if speed is None else float(speed)
            parts: list[np.ndarray] = []
            with torch.inference_mode():
                for chunk in self._voice.inference_zero_shot(
                    text,
                    prompt_text,
                    os.path.join(self.pkg, conf["reference_audio"]),
                    zero_shot_spk_id=style,
                    stream=True,
                    speed=speed,
                    text_frontend=True,
                ):
                    if cancel is not None and cancel.is_set():
                        raise CancelledError()
                    parts.append(chunk["tts_speech"].detach().cpu().numpy()[0])
            audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
            peak = float(abs(audio).max())
            if peak > 0:
                audio = audio * min(1.0, 0.98 / peak)
            return audio.astype(np.float32), int(self._voice.sample_rate)

    def voices(self) -> list[dict]:
        meta = {
            "cute": {
                "name": "可爱",
                "best_for": "日常聊天、活泼回应、游戏陪玩默认",
                "description": "F6 微调检查点（累计 40 次更新）+ N01 参考音频",
            },
            "gentle": {
                "name": "温柔",
                "best_for": "轻声陪伴、安慰、睡前语气",
                "description": "原版预训练 LLM + N10 参考音频（零样本音色控制）",
            },
        }
        out = []
        for name, conf in self.manifest["styles"].items():
            m = meta.get(name, {})
            out.append(
                {
                    "id": f"feiyu-{name}",
                    "style": name,
                    "name": m.get("name", name),
                    "candidate": conf["candidate"],
                    "checkpoint": conf["llm_checkpoint"],
                    "reference_audio": conf["reference_audio"],
                    "reference_text": conf["reference_text"],
                    "sample_rate": 24000,
                    "recommended_speed": self.params.get("speed", 1.0),
                    "best_for": m.get("best_for", ""),
                    "description": m.get("description", ""),
                }
            )
        return out


class CancelledError(RuntimeError):
    pass


_ENGINE: FeiyuEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> FeiyuEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            e = FeiyuEngine()
            e.load()
            _ENGINE = e
        return _ENGINE

