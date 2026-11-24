# 大肥鱼语音网关 · 接口文档

本地 HTTP + MCP 语音服务，给 DSH / Codex / 其他 AI 或软件调用「大肥鱼」音色。

- 引擎：CosyVoice3 + 肥鱼权重（`E:\feiyu\FeiyuVoice-CosyVoice3`，v1.0.1）
- 两条声线：`cute`（可爱，默认）/ `gentle`（温柔）
- 采样率：24 kHz 单声道 WAV（可转 MP3）
- 默认推理运行时为 Docker 容器 `feiyu-voice-svc` 内的 PyTorch。Windows 上运行 MCP 转发进程，也提供独立的原生推理回退环境。
- 文档更新：2026-11-24

---

## 1. 启动与自检

```powershell
# ── 方式一（当前默认）：常驻容器，runtime=container-torch ──
docker start feiyu-voice-svc      # 启动（首次创建见下文"部署"）
docker stop  feiyu-voice-svc      # 停掉可立刻释放 3.4GB 显存

# ── 方式二：Windows 原生进程，runtime=windows-native ──
E:\feiyu\.venv\Scripts\python.exe E:\feiyu\server\gateway.py --port 9880 --preload

# 自检：健康检查 + 声线列表 + 合成一句（保存到 outputs\mcp）
E:\feiyu\.venv\Scripts\python.exe E:\feiyu\server\test_client.py

# MCP 路径自检（模拟客户端握手 + speak）
E:\feiyu\.venv\Scripts\python.exe E:\feiyu\server\test_mcp.py
```

### 1.1 部署方式与运行时标注（重要）

同一套代码有三种运行时，**说话时实际加载的是哪一个，看 `runtime` 字段**：

| runtime | 部署 | 说明 |
|---|---|---|
| `container-torch` | 常驻容器 `feiyu-voice-svc`（默认） | 双声线，RTF ≈ 0.67，占显存 3.4GB、容器内存 2.9–3.8GB |
| `windows-native` | Windows 原生 `gateway.py` | 容器不可用时的回退，RTF ≈ 1.49，内存约 6.4GB |
| `container-vllm` | 容器 + vLLM（实验） | 最快 RTF 0.33，但只能可爱声线，未接入服务 |
| `stopped` | 网关未运行 | 查询类工具不会触发加载 |

- `GET /health` 返回 `runtime`；
- MCP `speak` 在同步和异步提交成功时均返回 `runtime`。同步结果还包含 `engine_hint`、本地音频路径和 `played`。
- **MCP `speak` 默认直接在 Windows 主机扬声器播放**（`winsound` 同步播放，播完才返回），`played` 取值 `played` / `disabled(FEIYU_AUTOPLAY)` / `skipped(...)` / `play_failed(...)`。设 `FEIYU_AUTOPLAY=0` 只生成文件不播放；`format=mp3` 不播放。
- **MCP 自动拉起顺序**：先 `docker start feiyu-voice-svc`，失败才回退 Windows 原生进程；
  容器启动后若等待就绪超时，本次请求返回错误。已有网关时直接复用。
  可用 `FEIYU_AUTOSTART=process` 指定冷启动时使用 Windows 原生，`FEIYU_CONTAINER=<名字>` 指定容器名。

容器创建命令（首次）：

```powershell
docker run -d --name feiyu-voice-svc --gpus all -p 9880:9880 -v 'E:\feiyu:/data' `
  -e FEIYU_REPO=/data/CosyVoice -e FEIYU_PKG=/data/FeiyuVoice-CosyVoice3 `
  -e FEIYU_WETEXT_RES=/data/wetext-resources -e FEIYU_RUNTIME=container-torch `
  -e PYTHONUTF8=1 -e OMP_NUM_THREADS=4 `
  -e MALLOC_ARENA_MAX=2 -e MALLOC_TRIM_THRESHOLD_=131072 -e MALLOC_MMAP_THRESHOLD_=131072 `
  feiyu-voice:vllm09 python -u /data/server/gateway.py --host 0.0.0.0 --port 9880 --preload
```

> 最后三个 `MALLOC_*` 参数很关键：不加时容器内存约 7.1GB，加上后降到 2.9–3.8GB（加载后自动 `malloc_trim`）。

可选鉴权：设置环境变量 `FEIYU_API_KEY` 或创建 `E:\feiyu\server\api_key.txt`，之后所有请求需带
`Authorization: Bearer <key>`。

---

## 2. 接口总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康状态：`ready`、当前声线、队列长度、运行时、torch 版本 |
| GET | `/v1/voices` | 两条声线的完整元信息（检查点、参考音频、适用场景、推荐语速） |
| GET | `/v1/models` | OpenAI 风格模型列表（`feiyu-cute` / `feiyu-gentle`） |
| POST | `/v1/audio/speech` | **OpenAI 兼容**同步合成，直接返回音频字节 |
| POST | `/v1/speech` | 原生异步：返回 202 + `job_id` |
| GET | `/v1/jobs/{id}` | 任务状态（`queued/running/success/failed/cancelled`） |
| GET | `/v1/jobs/{id}/audio` | 下载结果音频 |
| POST | `/v1/jobs/{id}/cancel` | 取消任务（推理在 chunk 之间检查取消标志） |

合成结果按 `sha256(text|voice|speed|format)` 缓存，重复短句直接命中。

---

## 3. 两条声线详解

| id（OpenAI `model`） | 声线 | 检查点 | 参考音频 | 参考文本（前缀后） | 推荐语速 | 适用场景 |
|---|---|---|---|---|---|---|
| `feiyu-cute` | 可爱（默认） | `feiyu_cute_F6_0040.pt`（F6 微调，累计 40 次更新） | `references/cute_N01.wav` | 我已经准备好了，我们开始吧。 | 1.0 | 日常聊天、活泼回应、游戏陪玩 |
| `feiyu-gentle` | 温柔 | 原版 `llm.pt` | `references/gentle_N10.wav` | 我刚刚把桌子收拾了一下，也给自己倒了一杯温水。现在可以坐下来，安安静静地听你说话了。 | 1.0 | 轻声陪伴、安慰、睡前 |

两条声线是**同一套共享组件**（flow / hift / 文本编码器 / tokenizer）＋不同 LLM 检查点与参考音频的组合，不是两份独立模型。

### 3.1 切换代价

- 首次加载模型：约 20 秒（启动时一次）；
- **切换声线**：重载 1.93 GB 检查点，约 **1–3 秒**；
- 参考音频特征（prompt token / 音色 / 特征）在启动时缓存，单次请求不再重复提取。

### 3.2 可爱声线（feiyu-cute）

**curl（OpenAI 兼容）**

```bash
curl -X POST http://127.0.0.1:9880/v1/audio/speech ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"feiyu-cute\",\"input\":\"你好呀，今天想聊点什么？\",\"voice\":\"cute\",\"response_format\":\"wav\"}" ^
  --output cute.wav
```

**Python（OpenAI SDK）**

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:9880/v1", api_key="not-needed")
audio = client.audio.speech.create(model="feiyu-cute", voice="cute", input="你好呀，今天想聊点什么？")
audio.stream_to_file("cute.wav")
```

**MCP（DSH / Codex / Claude 工具）**

```json
{"name": "speak", "arguments": {"text": "你好呀，今天想聊点什么？", "voice": "cute"}}
```

### 3.3 温柔声线（feiyu-gentle）

**curl**

```bash
curl -X POST http://127.0.0.1:9880/v1/audio/speech ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"feiyu-gentle\",\"input\":\"慢慢来，我们还有时间。\",\"voice\":\"gentle\",\"response_format\":\"mp3\",\"speed\":1.0}" ^
  --output gentle.mp3
```

**Python**

```python
audio = client.audio.speech.create(model="feiyu-gentle", voice="gentle", input="慢慢来，我们还有时间。")
audio.stream_to_file("gentle.wav")
```

**MCP**

```json
{"name": "speak", "arguments": {"text": "慢慢来，我们还有时间。", "voice": "gentle"}}
```

### 3.4 异步任务（长文本/不想阻塞时）

```bash
# 提交
curl -X POST http://127.0.0.1:9880/v1/speech -H "Content-Type: application/json" ^
  -d "{\"text\":\"今天辛苦了，要不要先休息一下？\",\"style\":\"gentle\"}"
# -> {"job_id":"...","status":"queued","poll":"/v1/jobs/..."}

# 轮询
curl http://127.0.0.1:9880/v1/jobs/<job_id>
# 取音频
curl http://127.0.0.1:9880/v1/jobs/<job_id>/audio --output out.wav
# 取消
curl -X POST http://127.0.0.1:9880/v1/jobs/<job_id>/cancel
```

---

## 4. 参数说明

`POST /v1/audio/speech`（OpenAI 兼容）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model` | string | `feiyu-cute` | `feiyu-cute` / `feiyu-gentle`（也可用 `voice` 指定） |
| `input` | string | 必填 | 要说的话（也接受 `text`） |
| `voice` | string | `cute` | `cute` / `gentle`；也接受 `feiyu-cute`、`可爱`、`温柔` |
| `speed` | number | `1.0` | 语速倍率，0.9–0.95 更自然，1.0 以上更急 |
| `response_format` | string | `wav` | `wav`（PCM16 24kHz）/ `mp3`（需要本机 ffmpeg） |

`POST /v1/speech`（原生异步）：`text`（或 `input`）、`style`（或 `voice`）、`speed`、`format`。

---

## 5. 返回与错误

- 成功：`200` + 音频字节（`audio/wav` 或 `audio/mpeg`）；
- 异步提交：`202` + `{"job_id","status":"queued","poll"}`；
- `400`：`input/text` 为空；
- `401`：启用鉴权但 Bearer 缺失/错误；
- `404`：job 不存在；
- `409`：job 未成功时取音频；
- `500`：合成失败（返回 `error.message`）。

---

## 6. MCP 工具（给 AI 客户端）

客户端配置（DSH / Codex / Claude Desktop 等）：

```json
{
  "mcpServers": {
    "feiyu-voice": {
      "command": "E:\\feiyu\\.venv\\Scripts\\python.exe",
      "args": ["E:\\feiyu\\server\\mcp_server.py"],
      "env": { "FEIYU_URL": "http://127.0.0.1:9880" }
    }
  }
}
```

| 工具 | 参数 | 返回 | 会不会加载模型 |
|---|---|---|---|
| `speak` | `text, voice=cute, speed=1.0, wait=true, format=wav` | `{"audio_path","voice","runtime","engine_hint","bytes","content_type","played"}` | ✅ 会（网关没起时先 docker start，失败回退 Windows 原生）；`wait=true` 时顺带在本机播放 wav |
| `list_voices` | — | 两条声线元信息 | ❌ 不会（网关未启动或模型未就绪时返回静态列表） |
| `job_status` | `job_id` | 任务状态 | ❌ 不会 |
| `cancel_job` | `job_id` | 取消结果 | ❌ 不会 |
| `health` | — | 服务状态 + `runtime`（container-torch / windows-native / stopped） | ❌ 不会 |

> `wait=false` 时 `speak` 走异步接口，返回 `job_id`，需用 `job_status` 轮询。
> 查询类工具刻意不加载模型，避免「查一下状态就吃掉几个 GB 内存」。
> **说话前可先调 `health` 看 `runtime`**，确认当下是容器还是 Windows 原生在服务。

---

## 7. 性能与资源（实测，RTX 4060 Laptop 8GB）

### 7.1 GPU（游戏未运行，GPU 空闲）

| 项 | 容器 `container-torch` | Windows 原生 |
|---|---|---|
| 模型加载 | 40–70 秒（挂载盘读模型慢） | 约 20 秒 |
| 切换声线 | 1–3 秒 | 1–3 秒 |
| **RTF** | **0.665** | 1.487 |
| 6 秒语音生成耗时 | 4.15 秒 | 11.8 秒 |
| 首块延迟（流式） | 1.65 秒 | 3.29 秒 |
| 显存峰值 | 3.33 GB | 3.36 GB |
| 容器/进程内存 | **2.9–3.8 GB**（带 MALLOC 参数） | 约 6.4 GB |

### 7.2 游戏运行中（GPU 容器 + 游戏同时跑）

| 项 | 数值 |
|---|---|
| RTF | **1.87–2.25**（游戏外 0.665） |
| 4 秒语音生成耗时 | 7–10 秒 |
| 显存 | 6.9–7.3 GB / 8 GB（接近上限） |
| 结论 | 能出声但生成追不上播放；适合短句播报/预生成 |

### 7.3 CPU（无 GPU，显存零占用）

| 方式 | 加载 | RTF | 3.76 秒语音耗时 |
|---|---|---|---|
| 容器 CPU（4 核） | 66 秒 | 10.5 | 39.5 秒 |
| Windows CPU（12 线程） | 25 秒 | 12.35 | 46.4 秒 |

CPU 只适合预生成或极短句，不能实时对话；好处是不占显存。

### 7.4 vLLM 对照（实验，未接入服务）

RTF 0.332、6 秒语音 1.94 秒、显存 4.05 GB；需 `gpu_memory_utilization=0.9`、`max_model_len=1024`、且只支持可爱声线。

## 8. 游戏 / 高压场景建议

- 显存是硬约束：模型 3.4 GB + 游戏 4–6 GB 容易到 7 GB 以上，接近 8 GB 上限；
- 游戏时**只说短句**（10–20 字），生成 7–10 秒，可接受但无法实时对答；
- 最省资源的做法：**预生成**——提前合成好台词，播放瞬间完成；
- 不需要说话时 `docker stop feiyu-voice-svc`，3.4 GB 显存立刻归还；VM 内存要 `docker desktop restart` 或 `wsl --shutdown` 才会明显回落；
- 显存不足时可改用 Kokoro（0.5 GB、RTF 0.03）临时兜底。

## 9. 已知限制

1. 单 GPU 串行，长文本建议切句或走异步任务；
2. 英文单词按字母规则读；超长句会加速（训练数据特性），需切分；
3. 声线切换需要 1–3 秒；
4. 缓存按文本+声线+语速+格式哈希，改写一个标点就会重新生成；
5. **容器从 Windows 挂载盘读取模型很慢**（加载 40–70 秒，首次合成还要读 1.93GB 检查点），
   未做本地卷优化前，容器首次说话会明显慢；
6. vLLM 模式只能可爱声线（换声线需重建引擎，约 40–60 秒），因此未接入生产服务；
7. 同一张 8GB 卡上不能同时加载容器和 Windows 原生两份模型。
8. **残留多个旧 MCP 客户端会让 `speak` 误报「语音网关启动失败」**（网关其实健康，`/health` 正常）。
   现象：`health` 报 `stopped` 但 `http://127.0.0.1:9880/health` 返回 `ready:true`。
   处理：清掉所有 `feiyu\server\mcp_server.py` 进程（旧客户端持锁/状态异常），客户端下次调用会自动重新拉起。
   排查命令：`Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'mcp_server\.py' }`。
