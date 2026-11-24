# Codex 与 DeepSeek 协作分析

这是一个已落地的 Windows 命令行工程：调度器给两个独立模型分配审查角度，先各自分析，再交换上一轮观点、交叉质疑和核验，最后保存证据、分歧及完整讨论记录。第一版仅做静态分析，不修改或执行被分析项目。

项目位置：`E:\DS_Agents`。原有《本地多模态AI_Agent平台_项目设计与30天实施方案.docx》保留。当前范围：Codex + DeepSeek 的双模型只读分析，以及新增的“外部 AI 调用 DSH”入口（后者在授权档位内可修改工作区）。尚未实现原文档中的桌面控制、ComfyUI 或 Web 界面。

## 外部 AI 调用 DSH 的入口（新增）

本工程现在也是 **“外部 AI → DSH” 的入口工具**：外部 AI（opencode、Claude Code、Codex 等）
通过 CLI 或 MCP 提交任务、工作区与权限档位，入口调用本机 DSH 的 headless 模式执行，
返回结构化结果。DSH 使用自己的凭证，本入口不复制任何 Key。

```powershell
# 检查（不调模型，不产生费用）
python -X utf8 -B scripts/dsh.py --config config.dsh.json doctor

# 同步执行
python -X utf8 -B scripts/dsh.py --config config.dsh.json run `
  --workspace 'E:\YourProject' --grant workspace-write --json `
  --task '审查核心模块，指出最高风险并给出证据'

# 作为 MCP 服务供 AI 客户端注册
python -X utf8 -B scripts/dsh_mcp.py --config config.dsh.json
```

还需要一个**专属 HTTP API** 时：

```powershell
python -X utf8 -B scripts/dsh_api.py --config config.dsh.json `
  --new-token --save --label opencode --grant workspace-write  # 自动登记 token
# 把输出的 token 交给调用方，然后：
python -X utf8 -B scripts/dsh_api.py --config config.dsh.json
# → http://127.0.0.1:3081/dsh/v1
```

该 API 使用独立端口、独立 Bearer token（只存 sha256）、每 token 独立权限上限与速率，
可选注入项目专属 DeepSeek Key 实现独立计费；与 3080 网页、官方 DeepSeek API 互不通用。
每次任务的对话可按 `GET /dsh/v1/tasks/{id}` 的 `transcript` 查看，也可轮询
`/events` 或读 `runs/api/conversations/<id>.md`；带 `session_id` 再次调用即可多轮对话。

权限分 `read-only` / `workspace-write` / `danger-full-access` 三档，`max_grant` 由人
在配置中封顶；DSH 越权被拒时结果返回 `needs_permission` 与 `next_grant`，由外部 AI
向人申请后再提权重试。详见 `docs/dsh-entry.md`。

## 已有 DSH 的推荐入口（真实联调已通过）

2026-10-24 后续验证已通过：复用 `F:\deepseekA` 的 DeepSeek 凭证，并为 Codex 子进程使用现有 Windows 代理，成功完成两轮、四次真实模型调用。当前结果见 `docs/acceptance-dsh.json` 和 `docs/dsh-integration.md`；此前的超时与缺少凭证记录保留为历史。

```powershell
Set-Location -LiteralPath 'E:\DS_Agents'
node scripts/run-with-dsh.cjs --system-proxy --config config.dsh.json run --project examples/sample_project --question '检查平均值函数的边界条件，互相质疑并引用源码'
```

该入口需要本机 Node.js 和现有 DSH 安装。它读取 DSH 已保存的 Key，只传入本次 Python 进程内存，不复制到工程、不修改 DSH 配置。`--system-proxy` 只影响子进程环境，不改变系统设置；DeepSeek 适配器仍直连其官方 HTTPS 接口。普通 `python -m ds_agents` 入口仍可单独使用，不依赖 Node.js。

`3080` 是 DSH 的带认证网页入口；当前桥接复用的是其已有 DeepSeek 凭证，推理由官方 API 完成，没有调用 DSH 的自动执行工具。`config.dsh.json` 中的 Codex 模型采用本机已配置并实际验证的 `gpt-6-astra`。

## 立即体验

在 PowerShell 中运行，无需安装第三方 Python 包，也无需模型凭证：

```powershell
Set-Location -LiteralPath 'E:\DS_Agents'
python -B -m ds_agents run --project examples/sample_project --question '检查平均值函数的边界条件，指出证据和分歧' --mock
```

`--mock` 是明确标注的模拟演示，不会访问网络、不会消耗额度，也不代表真实模型结论。成功时显示报告路径。也可运行 `scripts\demo.ps1`。

默认执行两轮，每轮分别调用 Codex 和 DeepSeek，共四次。串行发出请求便于控制用量，但两位参与者同一轮看到完全相同的上一轮信息，不会因执行顺序提前看到对方本轮回答。可把 `rounds` 设为 3，以得到“独立分析 → 交叉质疑 → 回应与收敛”三轮。

## 环境与接口

- Python 3.10 或更高；开发机已验证 Python 3.10.11。
- 原生官方 Codex CLI；已核对并测试 0.153.4 的参数。`doctor` 检查必需参数和功能开关，缺失时停止。Windows npm 安装的 `.cmd` 包装器不直接执行；请指定其背后的原生可执行文件或使用桌面应用附带的 `codex.exe`。
- Codex 使用独立的 OpenAI provider 和现有 `codex login` 登录；忽略用户自定义 provider 配置。不会把 DeepSeek 当成 Codex 的后端。
- DeepSeek 使用官方 `https://api.deepseek.com/chat/completions`，TLS 证书正常校验，不跟随重定向。模型名外置；示例采用核实于 2026-10-24 的 `deepseek-flash`。
- Python 运行时依赖为零，直接 `python -m ds_agents` 即可；`pyproject.toml` 提供可选的 Python 包元数据，不必为运行而安装构建依赖。

检查环境（不发送付费请求）：

```powershell
python -B -m ds_agents doctor
codex login status
```

初次开发时 Codex 已登录，普通终端未设置 DeepSeek Key，因此直接 `doctor` 可能返回退出码 2。使用上述 DSH 入口时，凭证在启动时注入，可执行 `node scripts/run-with-dsh.cjs --system-proxy doctor` 检查。已登录不等于网络、额度和模型权限都已验证。

## 配置 DeepSeek Key 并真实运行

Key 从 [DeepSeek 官方平台](https://platform.deepseek.com/) 获取。请只在本机输入，不要发到聊天中。

```powershell
Set-Location -LiteralPath 'E:\DS_Agents'
. .\scripts\set-key.ps1
python -B -m ds_agents doctor
python -B -m ds_agents run --project examples/sample_project --question '检查平均值函数的空输入行为，互相质疑并引用源码'
```

脚本隐藏输入，不把 Key 写入命令历史或文件；环境变量只在当前 PowerShell 进程有效，关闭窗口后需要重新设置。也可由你已有的凭证管理工具注入 `DEEPSEEK_API_KEY`。`.env.example` 仅为说明，不自动读取 `.env`，不保存明文凭证。

所有参与者在首次付费调用之前做预检查；缺少 DeepSeek Key 时不会先消耗 Codex 调用。没有实现“配置成功就自动开始分析”的后台任务。

单独验证一次 Codex（仅发送内置两行示例，会使用 Codex 额度）：

```powershell
python -B -m ds_agents smoke-codex
```

## 分析自己的项目

先查看将要发送给模型的脱敏快照，整个 preview 过程不联网：

```powershell
python -B -m ds_agents preview --project 'E:\YourProject' --question '检查登录流程的边界条件'
```

打开命令输出目录内的 `manifest.json` 和 `snapshot.json`。前者说明实际覆盖和跳过的文件，后者就是待发送文本。确认范围符合需要后：

```powershell
python -B -m ds_agents run --project 'E:\YourProject' --question '检查登录流程的边界条件；分别说明证据、反例和不确定点'
```

`run` 会重新采集当前快照，因此若文件在 preview 后改变，输入也会改变。实时模式会把筛选后的文本发送给 OpenAI 和 DeepSeek。脱敏是尽力检测，不是所有秘密的识别保证；敏感项目应通过 include 选择必要文件并检查快照。未配置 `.gitignore` 语义解析；程序采用自己的保守规则和显式 include/exclude。

报告默认写入 `E:\DS_Agents\runs`。若要分析 `E:\DS_Agents` 自身，必须把报告放到外部：

```powershell
python -B -m ds_agents run --project 'E:\DS_Agents' --output 'E:\DS_Agents_Reports' --question '审查这个调度器的风险'
```

## 配置与费用限制

复制 `config.example.json` 为 `config.local.json` 后编辑；配置文件不得写入 Key。使用方式：

```powershell
Copy-Item -LiteralPath config.example.json -Destination config.local.json
python -B -m ds_agents --config config.local.json run --project examples/sample_project --question '审查边界条件'
```

| 配置 | 默认值 | 意义 |
| --- | --- | --- |
| `rounds` | 2 | 每个模型轮数，允许 1–5；多轮质疑请至少设为 2 |
| `max_calls` | 6 | 全部模型的调度调用上限，失败尝试也计数 |
| `codex.max_calls` | 3 | Codex 调用上限 |
| `timeout_seconds` | 120 | 单次请求超时；Codex 超时结束子进程树 |
| `max_total_tokens` | 120000 | 根据服务返回的累计 token 限制后续请求 |
| `max_prompt_bytes` | 90000 | 系统提示、快照和上一轮观点的总大小限制 |
| `deepseek.max_output_tokens` | 2500 | DeepSeek API 的最大输出 token |
| `deepseek.max_usd` | 0.25 | DeepSeek 费用预留上限，美元 |
| `snapshot.max_files` | 40 | 最多发送 40 个文本文件 |
| `snapshot.max_file_bytes` | 24000 | 超过此大小的文件整体跳过 |
| `snapshot.max_total_bytes` | 48000 | 脱敏后文件总字节上限 |
| `snapshot.include/exclude` | 空数组 | 基于相对路径的 glob 列表，如 `src/*.py` |
| `codex.model` | null | 使用官方 CLI 的 OpenAI 默认模型；可填自己账号可用的模型名 |

DeepSeek 请求前以提示 UTF-8 字节数加 4096 的消息开销，结合最大输出做保守预留；失败或超时不退回预留，不自动重试。计价配置采用核实日的 Flash 高峰、输入缓存未命中单价：输入 $0.30/百万 token，输出 $1.20/百万 token。实际费用可能因缓存、时段、模型及价格变动而不同。更换模型必须一起更新费率；程序的估算不替代服务商账单。

**Codex 限额边界：** 该 CLI 没有本项目可用的单次美元硬封顶接口。调用次数、提示大小和超时是本地限制；token 在响应后核对，单次回复可能越过累计 token 上限。超时也不能保证服务端停止计费。Codex CLI 内部可能自行进行网络重试，调度器不追加重试。报告的 Codex 美元费用标记为未知，不会显示成零。需要严格金额封顶时还应在提供商侧设置预算或额度。

`rounds=3` 时两模型共 6 次调用，需保证 `max_calls>=6` 且 `codex.max_calls>=3`；更多轮数也要一起调整相关限制。程序不会为满足轮数自动提高预算。

## 记录与核验含义

每次运行建立独立目录，已有记录不覆盖：

- `report.md`：各模型最新结论、引用、质疑回应、未决分歧和费用说明。
- `discussion.json`：完整各轮公开观点、用量、核验结果、结束状态。
- `events.jsonl`：请求开始、完成、中止等可追踪事件；失败请求保留调用预留。
- `snapshot.json`：实际分析的脱敏文件内容；不保存未脱敏原文。
- `manifest.json`：文件哈希、行数、大小和跳过原因。
- `config.json`：本次非敏感配置。
- `call-*/schema.json`：发送给 Codex 的输出结构定义。

程序验证文件存在于快照、行号合法、引用与对应原文匹配；不把“引用匹配”升级成“结论正确”。共同判断仅按完全相同的陈述做机械归组；即使两个模型都支持，也仍需人工判断。语义相近的结论可能分列，模型未回应的观点会保留为未确认。

汇总是确定性的，不额外调用第三个模型，避免隐含付费和把分歧改写成共识。所有轮次保留，但下一轮仅传递上一轮双方完整结构化观点，避免历史无限增长。默认不早停于“意见一致”。

## 只读与隐私边界

被分析项目仅由 Python 读取；不会运行其代码、安装其依赖、调用其测试、加载其配置或执行其 `AGENTS.md` 指令。源码只是模型提示里的数据。

Codex 在报告目录的空白调用子目录运行，使用 `--sandbox read-only`、禁止审批、`--ignore-user-config`、`--ignore-rules`、`--ephemeral`，关闭 shell、插件、应用、hooks、浏览器、多 Agent 委托及记忆等功能，禁用网页搜索，项目指令字节数设为零。它不以被分析项目为工作目录。DeepSeek 不提供任何工具。

默认跳过隐藏文件/目录、环境文件、凭证命名文件、依赖和构建目录、二进制、过大文件、符号链接、Windows junction 和硬链接。源文件选定后做运行结束哈希检查；若其他软件同时改动了这些文件，报告会标记 `source_changed`。检查只覆盖入选文件，不是对整块磁盘的完整性证明。

本项目不是用于对抗恶意本地进程的系统级安全边界；分析期间不要并发替换文件、链接或报告目录。它沿用官方 CLI 的操作系统隔离和本机账号安全能力。日志和报告可能仍含项目业务信息，请按源代码同等方式管理。

不记录认证头、API Key、原始 CLI stderr、DeepSeek 原始错误响应或内部推理文本；只保存结构化公开结论及用量。运行时按环境中的密钥值和常见敏感格式进行脱敏。`runs/`、`.env` 与本地配置在 `.gitignore` 中。

## 测试与故障处理

```powershell
python -X utf8 -B -m unittest discover -s tests -v
node --test tests/test_dsh_bridge.cjs
```

测试使用模拟 API，不需凭证。包含 Windows 链接隔离、来源不变、轮次屏障、坏引用、秘密过滤、HTTP 错误、Codex 事件解析、超时、输出上限、预算和缺少凭证的预检查。Windows 链接测试需要当前进程能在工程的 `.test-tmp` 创建测试链接；受限进程可能拒绝该能力，正常用户终端已实测通过。

- `doctor` 退出码 2：查看缺失项，常见原因是未设置 DeepSeek Key；不会打印 Key。
- Codex 登录失败：在普通用户 PowerShell 执行 `codex login`，再用 `smoke-codex` 验证。
- Codex 缓存拒绝访问：先核对当前进程对 Codex 用户缓存目录的正常权限，不使用跳过权限检查的危险模式。
- DeepSeek 401 / 402 / 429：分别检查 Key、余额、限流；程序不无限重试。
- JSON 不完整或输出截断：检查报告中的失败原因，必要时在预算范围内增加 `max_output_tokens`，再手动重跑。
- 按 Ctrl+C 取消：保存已完成轮次并标记中断；服务端是否已计费仍需看服务商记录。
- 程序被强制杀死：已有 `events.jsonl` 和检查点保留，状态可能停在 `running`；没有自动恢复或重放付费请求。先查看最后一个事件。

## 扩展更多模型

在 `providers.py` 中实现 `Provider` 协议的 `name`、`role`、`preflight()`、`generate()`，返回 `Reply`。然后由 CLI 的 providers 列表加入独立实例；引擎支持任意数量、名称唯一的参与者，继续使用同一轮次屏障。

接入新云服务时必须同时补充认证、调用前预算策略、结果协议验证及适配器测试；目前费用模型仅实现 DeepSeek，不能宣称新增服务自动受到美元上限保护。不要把模型返回的文本作为可执行命令。

选型依据、官方链接及与既有设计文档的关系见 `docs/architecture.md`；初次验收记录见 `docs/validation.md`，最新真实联调见 `docs/acceptance-dsh.json`。
