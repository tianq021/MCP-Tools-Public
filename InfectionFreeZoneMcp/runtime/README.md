# runtime（AI 协作运行时）

目标：让 AI 不再"每条命令等返回、每轮全量读游戏"，由常驻 Node 进程承担三件事：

1. **实时游戏状态系统**：统一缓存 + 版本 + 增量差分 + 语义事件 + 长轮询；
2. **异步游戏执行系统**：模型提交动作立即返回，执行器独立执行并回注结果；
3. **持续交流系统**：常驻调度器唤醒 DSH 会话（用户消息最高优先级、流式回复、任务中断）。

设计与调研见 [架构分析-三大基础能力.md](../docs/notes/架构分析-三大基础能力.md)。

## 模块状态

| 文件 | 状态 | 职责 |
|---|---|---|
| `pipe-client.mjs` | ✅ 已完成 | 直连插件命名管道的帧式客户端；离线码归一化（bridge_offline） |
| `events.mjs` | ✅ 已完成 | 语义事件引擎：敌情逼近/被围、低血、订单完成/变化、满载、低补给、昼夜/新一天、任务变化、卡住、暂停/焦点；带去重窗口与迟滞 |
| `state-service.mjs` | ✅ P0 主体完成 | 单路状态轮询、内容版本、按类目差分、事件游标、长轮询、掉线重连、会话切换重建和独立的地图后台刷新 |
| `hub.mjs` | ✅ 已完成（5 项测试通过） | 本地 HTTP API：`/health`、`/state`（全量/增量）、`/state/wait`、`/events`、`/events/wait` |
| `runtime.mjs` | 已接入 | 装配状态服务和 HTTP API，按启动参数启用执行器和 DSH 调度 |
| `exec-service.mjs` | 假桥闭环通过 | 单写者、请求去重、排队取消、原生回执跟踪和结果事件 |
| `scheduler.mjs` | 假 DSH 闭环通过 | 用户消息优先、事件合并、危险引导、显式中断和重试 |
| `dsh-client.mjs` | 实际 DSH 只读订阅通过 | 通过本机认证 RPC 投递消息，订阅指定会话的助手流 |
| `runtime-mcp.mjs` | MCP stdio 闭环通过 | 缓存与事件读取、动作提交、查回执和取消 |
| `chat-client.mjs` | 假 DSH 闭环通过 | 为本机浮窗并行投递消息和接收实时流 |

## 游戏内呼叫助手

在项目根目录运行 `node runtime/runtime.mjs --steam`，进入存档后打开「发起无线电 → 呼叫助手」。测试副本启动 Runtime 时省略 `--steam`。

- **呼叫 Codex** 使用 Runtime 所在机器上已登录的 `codex` CLI。启动时加 `--codex-session <聊天 UUID>` 可接续指定的 Codex 聊天，省略时每次开启临时会话。呼叫附上当天时间、小队数量和任务摘要，简短回复显示在游戏 HUD。
- **呼叫 DSH** 使用下文的 `--dsh-session` 启动方式绑定本局与陪玩会话。点击后，HUD 显示投递结果，助手在已绑定的 DSH 聊天中应答。已运行绑定版 Runtime 时，两项呼叫都由它接收。

## 数据流

```
游戏 + 插件 ──命名管道──▶ pipe-client ──▶ state-service ──▶ events
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                   本地 HTTP API         exec-service        scheduler
                 （AI/浮窗/CLI）      （异步动作执行）    （DSH 唤醒/流式）
```

## 本地 HTTP 和 MCP 接口

| 接口 | 语义 |
|---|---|
| `GET /health` | 桥接状态、版本、轮询统计、错误和当前分工 `assignment`。仅 `assignment.ready` 为真时可提交新订单 |
| `GET /state?since=<v>&cats=...` | 有 since 返回增量（added/updated/removed），否则全量 |
| `GET /state/wait?since=<v>&timeoutMs=...` | 长轮询：有变化才返回，超时返回 `changed:false` |
| `GET /events?since=<cursor>&types=...` | 语义事件增量 |
| `GET /events/wait?since=<cursor>&timeoutMs=...` | 事件长轮询（调度器主循环） |
| `POST /actions` | 入队后返回 HTTP 202 和任务。请求含 `requestId`、`sessionId`、`squadId`、`command`、`params`、`notice` |
| `GET /actions/<id>` | 读取任务状态、游戏回执和错误 |
| `DELETE /actions/<id>` | 取消排队任务，或请求中止提交前的准备 |
| `POST /actions/<id>/stop` | 传入 `reason` 强制终止该任务。游戏已接单时，用原生订单 ID 取消，并返回队列现状 |
| `POST /actions/<id>/resolve` | 玩家核对游戏后释放待确认的任务。Runtime 会再次检查小队当前订单与队列 |
| `POST /messages` | 将含 `requestId` 和 `text` 的用户消息优先投递给 DSH。设置 `interrupt: true` 时先中断当前回合。DSH 确认接收后返回 |
| `GET /stream` | SSE 推送 DSH 助手片段、消息读入回执、回合边界、连接状态和订单结果 |
| `GET /dialogue` | 从游戏读取当前无线电正文、可选回复和上一次选择后的台词 |
| `POST /dialogue/choose` | 通过游戏原生按钮提交指定的无线电回复 |

同一个动作或用户消息重试时沿用原 `requestId`。每类已结束记录在进程内最多保留 30 分钟或 20,000 条。重启 Runtime 或记录过期后，先核对游戏或 DSH 的实际状态，再决定是否提交。执行器串行提交原生订单，并在提交后继续观察。缺少完成证据时，任务显示为 `unknown`、`held: true`，同时保留小队和搜刮目标的占用，直到查到结果或玩家核对后释放。

写接口和浮窗事件流使用 `x-ifz-token`。启用动作或 DSH 时，Runtime 默认在 `%LOCALAPPDATA%\ifz-runtime\token` 生成令牌，同一账户下的 Runtime MCP 和浮窗会自动读取。可用 `IFZ_RUNTIME_TOKEN_FILE` 指定受当前用户保护的路径，或给各进程设置相同的 `IFZ_RUNTIME_TOKEN`。使用 `--require-token` 可同时保护查询接口。动作与消息接口分别限制为每 10 秒 20 和 10 次。

MCP 工具对应 `runtime_health`、`world_snapshot`、`world_wait`、`events_poll`、`events_wait`、`action_submit`、`action_status`、`action_cancel`、`action_force_stop` 和 `action_resolve`。核对待确认任务的游戏结果后，可用 `action_resolve` 释放占用。Runtime 会回读小队当前和排队订单，空闲后释放并保留原来的未知结果。

攻击结束后，Runtime 会重新核对已消失的目标是否仍在原生名册里。搜刮若只取到部分物资，订单结束且小队空闲后，证据会标为 `partial: true`。待确认任务仍占用各自的小队和目标，其他小队可以继续接单。

## 启动

默认只读。Steam 模式加入 `--steam`，端口默认为 8790。加 `--buildings` 时缓存总部 200 世界单位内的搜刮候选。

```powershell
node runtime/runtime.mjs --steam --port 8790
curl http://127.0.0.1:8790/health
```

### 直接读本地态势文件

Steam 模式启动 Runtime 后，打开项目根目录下的 `.local/live/current.md`。Runtime 默认每秒用已有缓存替换这份中文摘要，包含当前无线电、最近事件、总部敌情、共享库存、全部玩家小队和活动任务。`events.jsonl` 追加记录 Runtime 检测到的事件，以及采样到的无线电选项变化与通话结束，换档可用每条记录的 `sessionId` 区分。

文件头显示刷新时间、数据采样时间和会话。库存按现有的约 2 秒节奏采集。断线时保留上次数据并标记过期，Runtime 意外退出时可用刷新时间判断文件是否停更。无线电正文来自游戏当前可见内容，文件可能遗漏没有采样到的短暂台词。结束后保留近期采样到的台词，已选回执及后续播放记录仍可从 `get_active_dialogue` 核对。

DSH 游戏通知会提示先读此文件。常规观察只需本地文件读取，地图细节、无线电提交或游戏操作仍用对应工具。隔离测试模式写入 `.local/live-test/`，多个 Runtime 实例并行时各自用 `--live-dir <目录>` 指定输出目录。

启用动作需要当前游戏会话 UUID、玩家保留队和 AI 可操作队。会话变化后重新确认分工。

```powershell
$gameSession = '替换为当前游戏会话 UUID'
node runtime/runtime.mjs --steam --enable-actions --game-session $gameSession --player-squad '834' --squads '835,836'
```

开启 DSH 自动唤醒需要同一个游戏会话 UUID、明确的 DSH 会话 UUID，以及 `--dsh-home` 或 `DSH_HOME`。在 DSH 会话里选择外部大模型。危险和普通事件会合并后投递，用户消息优先。运行时可在只读模式下启用唤醒。

```powershell
$dshSession = '替换为已在 DSH 打开的会话 UUID'
node runtime/runtime.mjs --steam --game-session $gameSession --dsh-session $dshSession --dsh-home $env:DSH_HOME
```

用 [DSH 配置示例](../config/dsh.patch.yml) 或 [Codex 配置示例](../config/codex.toml) 注册 MCP。两份示例的原生 MCP 使用 `--workforce-only`，玩家负责小队。使用旧版小队协作流程时，Runtime 进程和 `runtime-mcp.mjs` 都需要显式传入 `--enable-actions`。若 Runtime 使用其他端口，在 MCP 进程的环境中设置 `IFZ_RUNTIME_PORT`。

## DSH 自主带队

1. 进入聚落后确认游戏会话、玩家保留队、AI 小队和要绑定的 DSH 会话，再同时启用执行和唤醒。分工首次有效时，调度器会通知 DSH 调查局势并开始带队。
2. 运行时在后台采集状态。小队、车辆和敌情的轮询间隔默认为 500 毫秒，另计桥接读取耗时。DSH 优先读本地态势文件，文件不可访问时可用 `world_snapshot` 按类目读缓存。具体建筑和路线用原生只读 MCP 查询。
3. DSH 用 `action_submit` 下单，拿到任务 ID 后结束本轮。执行器独立提交原生订单并跟踪结果，调度器在行动结束或重要局势变化时唤醒 DSH。玩家消息可直接在 DSH 发送，或通过 `/messages` 优先引导下一步。打断 DSH 回合时，已入队的后台任务仍由执行器处理。`action_cancel` 可取消尚未提交给游戏的任务。
4. 换档、缺少已分配的小队或桥接离线时，`assignment.ready` 会变为 `false`。恢复协作前先核对分工和仍在执行的游戏订单。

紧急中止时传 `action_force_stop({ taskId, reason })`。原生取消成功后，HUD 和浮窗会显示原因。`stopReceipt` 会列出当前订单和排队订单，确认小队空闲后可以用 `action_submit` 下撤退订单。若玩家已替换该订单，工具返回 `order_not_active`。

### 无线电剧情选择

安装本版编译的插件并进入聚落。Runtime 的 `--enable-actions` 同时允许无线电选择。只授权无线电时使用 `--enable-dialogue-choices`，可省略小队分工。绑定 DSH 会话后，`dialogue_options_available` 会在选项可点时唤醒模型。

1. 调用 `get_active_dialogue`。返回当前已显示的 `dialogue.text`、选项全文、可选状态、是否结束通话以及游戏提示的 `effect_hints`。提示会列出已知资源、士气或人口变化等，空数组表示游戏没预告后果。
2. 用返回的 `session_id`、`choice_id` 和 `option_id` 调用 `choose_dialogue_option`，并生成一个 `request_id`。请求超时可用原 ID 重试。玩家先选或对话已换到下一步时，原选项会失效。
3. 再次调用 `get_active_dialogue`。即使无线电面板已经关闭，也能在 `last_choice.observed_lines` 读取这次选择之后实际播放的台词。资源等实际变化可用现有状态工具核对。

原生 MCP 的 `get_active_dialogue` 要求传入当前 `session_id`。原生写模式可用同名写工具，调用时传入写租约。无线电选择沿用游戏自身的暂停和恢复规则。普通系统确认框由玩家处理。

无线电管理暂停或等待选择时，`action_submit` 会返回 `dialogue_choice_pending`。已入队的普通订单等待对话结束；准备中的协作订单遇到无线电接管暂停时不会主动推进时间。

### 游戏浮窗实时模式

绑定 DSH 会话并启用运行时后，在 Windows 上用 PowerShell 7 启动仓库自带的浮窗脚本，连接同一个端口。

```powershell
pwsh -STA -File .\scripts\Start-RuntimeChat.ps1 -Port 8790
```

- 回车把消息优先送到 DSH 的下一步。若 DSH 还在前台命令中，`Ctrl+回车` 可打断当前回合。后端执行器负责已经入队的游戏动作。
- 浮窗分别显示 DSH 已收件、消息已读入对话，并持续追加回复文本和订单结果。投递未确认时，用同一原文和快捷键重试会沿用请求 ID。
- 浮窗保留最近 50 条消息。一条流式回复按一条消息计数。每次打开浮窗都会在 `.local/logs/chat/` 新建 UTF-8 日志，完整保留本次消息。流式回复在结束、断线或关闭浮窗时写入。
- 下单保留 HUD 预告，预告写入后立刻做安全检查并提交。游戏暂停或因浮窗失焦时，后台保留待发订单。游戏恢复可操作后自动提交，超过 30 秒的待发订单会过期。

## 事件类型（events.mjs）

`enemy_approach`（info/warn/critical）、`squad_low_hp`、`squad_threat`、`order_completed`、
`order_changed`、`cargo_full`、`low_stock`、`nightfall`、`new_day`、`mission_updated`、
`squad_stuck`、`game_paused_changed`、`focus_changed`、`session_changed`、
`bridge_offline`、`bridge_restored`、`bridge_error`、`action_finished`。

## 开发与验证

- 测试：
  ```
  node --test runtime/test/*.test.mjs
  ```
  覆盖：时钟漂移不算变化、差分只返回变化字段、敌情分级/去重、低血/订单完成事件、
  长轮询、会话切换、地图故障隔离、异步提交和原生回执、重复请求、DSH 重试及 SSE 转发。
- 实机验收待做。用隔离存档验证游戏事件唤醒 DSH、DSH 提交动作和游戏回执返回。

## 关键实测数据（2026-11-10，第 6 天存档）

- 单次 MCP 调用 16–20 ms（≈60fps 一帧）；一轮 observe（7 类）115 ms；
- 旧版全量 buildings 6749 条 / 68 页 / 1.34–1.83 s。新版由 AI 通过 `get_map_buildings` 选择范围和分页；
- groups 15 条但每群最多 32 人明细，约几十 KB；
- MCP 层支持并发：7 个调用并发 30.8 ms vs 串行 184.9 ms；
- DSH `/api/remote.mux` + `session/follow` 已验证可拿到 snapshot/事件/流式 token。

## 实时事件通知修正（2026-11-14）

本机 `.local/start-runtime-8790.cmd` 清空事件屏蔽列表、关闭静音，设置 `IFZ_EVENT_DIGEST_MS=300` 和 `IFZ_EVENT_MIN_WAKE_MS=0`。这是检测后的合并时间，不包含游戏轮询、传输及模型响应耗时。DSH 收到事件后先读取相关最新状态再决策。

任务通知按任务 ID 比较新增、移除及目标完成状态变化，忽略任务列表重排；持续敌情使用状态去重。无线电播放及选项变化通知 DSH，旧的 `.local/agents/auto-dialogue.mjs` 已退役，不再连接 MCP 或自动选择回复。源码及环境配置需在下一次启动 Runtime 时加载；本次未启动 DSH 或 Runtime。

### 陪玩通知原则

危险、无线电与待核对行动及时引导 DSH 查看；新的一天、入夜、任务变化、行动结果、首次敌群接近和资源问题提供讨论契机。焦点切换、时钟推进不唤醒。事件只传递观察信息，DSH 根据玩家对话、共同计划和最新状态判断如何回应及是否行动，不要求每条播报，不用定时闲聊或关键词代选。玩家授权和分工不因事件扩展。

2026-11-14：无线电按整段通话唤醒一次，DSH按正文与后果提示自主回复并跟进到结束；中途台词/选项通过状态差分及world_wait读取，不逐条注入对话。本地启动器开启--enable-dialogue-choices。确认DSH回合结束且空闲90秒、游戏在线且无无线电/待处理事件时，提供一次陪伴回应机会；新对话或游戏通知后才能再次提供，重连未知忙闲状态不主动插入。自动关键词选择保持停用。未启动Runtime，需下次启动加载。
