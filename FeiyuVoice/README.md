# DSH-FeiyuVoice — 大肥鱼本地语音

给本地 AI（DSH / Codex / 其他）提供「大肥鱼」音色的语音能力：两条声线、本地推理、HTTP + MCP 两种通用接口。

- 项目路径：`E:\feiyu`
- 模型：FeiyuVoice-CosyVoice3 v1.0.1（基于 Fun-CosyVoice3-0.5B-2512，Apache-2.0）
- 文档更新：2026-11-24

## 目录

```
E:\feiyu\
├─ FeiyuVoice-CosyVoice3\   模型包（6.94 GiB，31 文件，SHA256 已校验；不含代码）
├─ CosyVoice\               CosyVoice 源码（commit 074ca6d + Matcha-TTS 子模块）
├─ .venv\                   Windows 推理环境（复用 conda qiaohaotian_new 的 torch 2.10.0+cu126）
├─ wetext-resources\        文本规范化资源（4 个 fst，离线）
├─ scripts\                 环境适配与冒烟测试（feiyu_env.py / smoke.py / bench.py）
├─ server\                  语音网关 + MCP 工具（**接口文档见 server/README.md**）
├─ outputs\                 生成的音频
├─ docker\                  Linux 容器实验（vLLM 加速，已完成测速）
└─ *.log                    各阶段日志
```

## 两条运行时路线（同一模型、同一权重）

| | A. Windows 原生 | B. Linux 容器（生产用 PyTorch，vLLM 为实验模式） |
|---|---|---|
| 环境 | `.venv`（torch 2.10.0+cu126） | 镜像 `feiyu-voice:vllm09`（torch 2.7 + vLLM 0.9） |
| 质量 | ✅ 已验证 | ✅ 已验证（需 transformers 4.51.3；4.57 会生成异常） |
| RTF | 1.487 | **0.332**（不开 vLLM 时 0.665） |
| 6 秒语音生成耗时 | 11.8 s | 4.15 s（vLLM 实验为 1.94 s） |
| 首块延迟 | 3.29 s | 1.65–1.67 s |
| 显存峰值 | 3.36 GB | 4.05 GB（不开 vLLM 3.33 GB） |
| 加载耗时 | 约 20 s | 约 40 s（vLLM 引擎再 +38 s） |
| 双声线 | ✅ 随时切换 | 仅 vLLM 模式受限（换声线需重建引擎） |
| 现状 | 可回退使用 | **生产使用中**：常驻容器 `feiyu-voice-svc`（`runtime=container-torch`） |

测速使用同一句长文本、同一 F6 权重、RTX 4060 Laptop 8GB、GPU 空闲。

### 2.1 运行时标注（说话时到底加载了什么）

`/health` 与 MCP `speak` 的返回里都有 **`runtime`** 字段，标明本次实际服务的引擎：

| runtime | 含义 | 速度（RTF） |
|---|---|---|
| `container-torch` | 常驻 Docker 容器（当前默认，双声线） | 0.665 |
| `windows-native` | Windows 原生进程（容器不可用时的回退） | 1.487 |
| `container-vllm` | 容器 + vLLM（实验，可更快但单声线） | 0.332 |
| `stopped` | 网关未运行 | — |

**说话前先看 `health` 或 `speak` 返回的 runtime**，确认当下加载的是容器还是 Windows 原生。

### 2.2 CPU 方案（不占显存，但很慢）

| 方式 | 加载 | RTF | 3.76 秒的话耗时 |
|---|---|---|---|
| 容器 CPU（4 核） | 66 s | 10.5 | 39.5 s |
| Windows CPU（12 线程） | 25 s | 12.35 | 46.4 s |

CPU 只适合**预生成**（提前合成好，播放瞬间完成）或极短句，不能实时对话；好处是完全不占显存，不会把游戏挤到 OOM。

### 2.3 游戏运行中的实测（GPU 容器 + 游戏同时跑）

| 项 | 数值 |
|---|---|
| 生成 RTF | 1.87–2.25（游戏外 0.665） |
| 显存 | 6.9–7.3 GB / 8 GB（接近上限） |
| 结论 | 能出声但慢，生成追不上播放；适合短句播报/预生成 |

## 资源与高压约束（重要）

- **显存是硬约束**：模型占 3.3–4.1 GB，8 GB 卡上如果游戏占 4–6 GB，两者叠加会 OOM；
- 默认容器启用 glibc 内存回收参数，项目实测容器内存约 2.9–3.8 GiB。Windows 原生网关此前测得约 6.3 GiB 驻留内存。查看容器占用用 `docker stats --no-stream feiyu-voice-svc`，WSL2 虚拟机的工作集已包含容器占用。
- 默认网关启动时预加载模型，空闲时仍占显存。需要释放时运行 `docker stop feiyu-voice-svc`。MCP 的 `health` 和 `list_voices` 可在停服时查询，下次 `speak` 会尝试启动网关。
- 游戏运行时可预生成台词，或用 10–20 字的短句降低单次合成时间。

## 快速开始（容器常驻，推荐）

```powershell
# 启动/停止常驻容器（模型常驻，首次加载约 40–70 秒）
docker start feiyu-voice-svc
docker stop  feiyu-voice-svc

# 查看当前运行时（runtime=container-torch 即容器在服务）
curl http://127.0.0.1:9880/health

# 自检
E:\feiyu\.venv\Scripts\python.exe E:\feiyu\server\test_client.py
```

首次创建容器请使用[网关部署说明](server/README.md#11-部署方式与运行时标注重要)中的命令，其中包含内存回收参数。

回退到 Windows 原生（容器不可用时）：

```powershell
E:\feiyu\.venv\Scripts\python.exe E:\feiyu\server\gateway.py --port 9880 --preload
```

> MCP 工具的自动拉起策略：先 `docker start feiyu-voice-svc`；失败则回退 Windows 原生进程（可用 `FEIYU_AUTOSTART=process` 指定冷启动时使用 Windows 原生）。

## 已知问题：容器从 Windows 挂载盘读模型很慢

容器内模型加载 67 秒、首次合成还要再读 1.93 GB 检查点（约 +30–50 秒），因为 `/data` 是虚拟文件系统。待优化：把模型复制到 Docker 本地卷（预计加载回到约 20 秒），未做之前容器首次说话会明显慢。

Codex 已注册全局 MCP 服务 `feiyu-voice`。Windows 上的 MCP 进程向网关转发请求，实际推理位置由网关报告。

DSH 已注册 MCP 服务 `feiyu_voice`（配置在 `F:\deepseekA\dsh-home-020-20261007\cordis.patch.yml`），直接可用的工具：

`speak(text, voice, speed, wait)`、`list_voices`、`job_status`、`cancel_job`、`health`。

## 两条声线（一句话版）

| id | 声线 | 检查点 | 参考音频 | 适用 |
|---|---|---|---|---|
| `feiyu-cute` | 可爱（默认） | `feiyu_cute_F6_0040.pt`（F6 微调 40 次） | `cute_N01.wav` | 日常聊天、游戏陪玩 |
| `feiyu-gentle` | 温柔 | 原版 `llm.pt` | `gentle_N10.wav` | 陪伴、安慰、睡前 |

调用时把 `voice` 传 `cute` / `gentle`（或 `feiyu-cute` / `feiyu-gentle`）即可；切换声线需要重载 1.93 GB 检查点，约 1–3 秒。完整调用示例见 [`server/README.md`](server/README.md)。

## 实验记录（2026-11-24）

1. 模型包下载并 SHA256 校验通过（6.94 GiB，31 文件，hf-mirror + ModelScope 双清单核对）；
2. Windows 原生环境跑通：两种声线、数字/日期/电话规范化正常；
3. 发现 `transformers 4.57.1` 会让 CosyVoice3 生成异常（同一句 2.4s 变 9.6s），换回 4.51.3 正常；
4. vLLM 0.11 与 transformers 4.51.3 不兼容（`AutoVideoProcessor` ImportError），改用官方组合 vLLM 0.9 + transformers 4.51.3；
5. vLLM 在 8GB 卡上需把 `gpu_memory_utilization` 从源码写死的 0.2 提到 0.9、`max_model_len` 限制到 1024 才能启动；
6. Docker 数据盘从 C 盘迁到 `E:\Docker`（C 盘释放约 51 GB）；
7. Dify 容器已全部停止并把重启策略改为 `no`（原为 `always`，会反复自动启动并占 CPU）。

## 可清理项

| 对象 | 大小 | 说明 |
|---|---|---|
| 镜像 `feiyu-voice:vllm` | 18.0 GB | 早期实验（transformers 4.57，质量异常） |
| 镜像 `feiyu-voice:tf4513` | 18.1 GB | 质量验证用中间镜像 |

## 已知限制

1. 单 GPU 串行：同一时刻只合成一句；长文本建议切句；
2. 英文单词按字母规则读，长句可能「赶」；数字/日期/电话由 wetext 规范化；
3. 声线切换要重载检查点（1–3 秒），频繁切换会拖慢；
4. 当前容器网关使用 PyTorch。vLLM 仅用于独立测速。
