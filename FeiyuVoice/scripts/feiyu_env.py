"""Feiyu 大肥鱼 TTS 的 Windows 运行时适配（不修改 CosyVoice 源码，只做运行时替换）。

两个必要的补丁：
1. load_wav：torchaudio 2.10 的 load() 强制要求 torchcodec，这里改用 soundfile 读取；
2. wetext 资源：wetext 默认从 ModelScope 下载 fst，本机匿名下载受限，改为读取本地资源目录。

用法：
    import feiyu_env; feiyu_env.prepare()
"""
from __future__ import annotations

import os
import sys

# 路径支持环境变量覆盖：Windows 原生用默认值，Linux 容器里传 /data/... 即可
REPO = os.environ.get("FEIYU_REPO", r"E:\feiyu\CosyVoice")
PKG = os.environ.get("FEIYU_PKG", r"E:\feiyu\FeiyuVoice-CosyVoice3")
WETEXT_RES = os.environ.get("FEIYU_WETEXT_RES", r"E:\feiyu\wetext-resources")


def prepare(verbose: bool = True) -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("PYTHONUTF8", "1")

    for p in (os.path.join(REPO, "third_party", "Matcha-TTS"), REPO):
        if p not in sys.path:
            sys.path.insert(0, p)

    import soundfile as sf
    import torch

    # --- 补丁 1: load_wav ---
    import cosyvoice.cli.frontend as fe
    import cosyvoice.utils.file_utils as fu
    from torchaudio.transforms import Resample

    def load_wav(wav, target_sr):
        speech, sample_rate = sf.read(str(wav), dtype="float32", always_2d=True)
        speech = torch.from_numpy(speech.T)  # (1, T)
        if sample_rate != target_sr:
            speech = Resample(orig_freq=sample_rate, new_freq=target_sr)(speech)
        return speech

    fu.load_wav = load_wav
    fe.load_wav = load_wav

    # --- 补丁 2: wetext 资源本地化 ---
    import wetext.wetext as ww

    def _snapshot(repo_id, *args, **kwargs):
        if repo_id == "pengzhendong/wetext":
            return WETEXT_RES
        raise ValueError(f"unexpected snapshot_download({repo_id})")

    ww.snapshot_download = _snapshot
    if verbose:
        print(f"[env] 已适配：load_wav=soundfile, wetext 资源={WETEXT_RES}", flush=True)
