# Feiyu 大肥鱼 TTS —— Linux/CUDA 实验环境（含 vLLM 加速）
#
# 目的：在 Docker 里跑 CosyVoice3 + vLLM，验证 LLM 自回归阶段能否显著提速。
# 本机基线（Windows 原生、torch 2.10+cu126、无 vLLM）：RTF ~1.5，流式首块 ~3.3s，LLM 占 90% 耗时。
#
# 构建：
#   docker build -t feiyu-voice:vllm -f E:\feiyu\docker\Dockerfile E:\feiyu\docker
# 运行（模型目录只读挂载）：
#   docker run --rm --gpus all -v E:\feiyu:/data feiyu-voice:vllm bash

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    OMP_NUM_THREADS=4

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg libsndfile1 libgomp1 build-essential ninja-build curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# vLLM 0.11.0 锁定 torch 2.8.0 / torchaudio 2.8.0（PyPI linux 轮子自带 CUDA 运行时）
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0

# vLLM（CosyVoice 官方支持的版本线：0.11.x V1 engine）
RUN pip install --no-cache-dir vllm==0.11.0

# CosyVoice 推理依赖（Linux 侧；TRT/deepspeed/ttsfrd 不需要）
RUN pip install --no-cache-dir \
        "transformers==4.57.1" "numpy==1.26.4" \
        onnxruntime==1.18.0 onnx==1.16.0 pyarrow==18.1.0 \
        pyworld==0.3.4 inflect==7.3.1 hyperpyyaml==1.2.3 omegaconf==2.3.0 \
        hydra-core==1.3.2 antlr4-python3-runtime==4.9.3 \
        wetext==0.0.4 librosa==0.10.2 soundfile==0.12.1 protobuf==4.25 \
        x-transformers==2.11.24 conformer==0.3.2 lightning==2.2.4 \
        gdown==5.1.0 matplotlib==3.7.5 wget==3.2 wandb \
        einops regex tqdm tiktoken modelscope==1.20.0 scipy packaging pyyaml \
        diffusers==0.29.0

# openai-whisper 只有 sdist，且构建需要 pkg_resources：先在构建期做成 wheel 再装，避免隔离环境缺 setuptools
RUN pip install --no-cache-dir "setuptools<81" wheel \
    && pip wheel --no-cache-dir --no-build-isolation --no-deps -w /tmp/wheels \
        -i https://pypi.tuna.tsinghua.edu.cn/simple openai-whisper==20231117 \
    && pip install --no-cache-dir --no-deps /tmp/wheels/openai_whisper-20231117-py3-none-any.whl \
    && rm -rf /tmp/wheels

WORKDIR /opt
CMD ["bash"]
