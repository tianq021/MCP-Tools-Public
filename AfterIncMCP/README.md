# After Inc: Revival MCP

把《劫后公司》（After Inc: Revival）包装成 MCP 服务，让 AI 模型能**直接通过游戏内桥接**读取状态、选择并执行游戏动作。DSH、Codex 或任何标准 MCP 客户端共用同一套入口和同一个游戏实例。

> 已在第一关 Eastern Breeze 实测读取聚落、选址、建造伐木场与劈柴机、读取施工和产出，以及暂停推进并点开地图事件。历史加载器排障记录见 [debug-notes-20261028.md](analysis/debug-notes-20261028.md)。

## 架构

```mermaid
flowchart LR
    A[AI 模型 / MCP 客户端] <-->|MCP stdio| B[server.mjs]
    B <-->|HTTP 127.0.0.1:17840| C[游戏内插件 AfterIncBridge]
    C <-->|Unity 主线程| D[GameController / ZoneActions / Simulator]
```

- `server.mjs`：MCP 服务，负责工具描述、参数校验、错误返回；默认 stdio 传输。
- `lib/bridge-client.mjs`：桥接 HTTP 客户端。只允许 `http://127.0.0.1:<port>/v1/call`，不自动重试写操作。
- `plugin/`：BepInEx IL2CPP 插件。提供 HTTP 入口、请求去重账本、共享控制权租约、动作句柄与 revision 校验。
- 游戏侧只暴露小型 JSON 快照，不把 Unity 对象图交给模型。

## 工具

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `status` | 只读 | 桥接版本、场景、`ready`、控制权、焦点、自动暂停摘要 |
| `get_state` | 只读 | 回合、速度、人口、权威、民心、资源、revision |
| `get_map` | 只读 | 玩家可见区域、相邻、探索/占领、地形与资源 |
| `list_actions(zone_id)` | 只读 | 区域真实动作候选与费用，返回 60 秒有效的 `action_id` |
| `execute_action(action_id, expected_revision)` | 写 | 经游戏 `ZoneActions.PerformAction` 执行，校验动作与状态未过期 |
| `acquire_control(label, ttl_seconds)` | 写 | 取得/续租共享写控制权（15~300 秒），冲突时拒绝 |
| `release_control` | 写 | 释放控制权并暂停游戏 |
| `set_speed(speed)` | 写 | `Paused` / `Normal` / `Faster` / `Fastest` |
| `advance(turns)` | 写 | 实时推进 1~20 回合后自动暂停 |
| `step_turn` | 写 | 直接调用 `Simulator.AdvanceGameTurn()` 推进一次（实验性，只走回合计数） |
| `unpause` | 写 | 已禁用，返回 `UNSAFE_OPERATION_DISABLED`。用 `get_game_controls` 读取并处理当前弹窗 |
| `debug_pause` | 只读 | 暂停诊断（`ProbeAutopause` 开启时才有数据） |

推荐调用顺序：`acquire_control` → `set_speed(Paused)` → `get_state`/`get_map` → `list_actions` → `execute_action` → `advance` → `release_control`。

## 战役地图选关（桥接 0.3.0）

使用 `get_campaign_map` 读取当前阶段。在欢迎菜单申请控制权后调用 `open_campaign_map`。在地图上从 `regions` 选择地区并调用 `select_campaign_region`，再从 `levels` 取得同一行的 `campaign_id` 与 `level_id`。每行包含解锁状态、锁定原因和通关记录。用 `enter_campaign_level` 进入选定关卡的原生开局流程。在 `level_setup` 阶段从返回的 `options` 选取 `option_id` 调用 `choose_campaign_option`。

所有选关写操作都需要当前读数的 `expected_revision` 和控制权。每次操作后重新读取；遇到 `MENU_TRANSITION` 待动画完成再读。只有阶段为 `in_settlement` 且 `level_id` 匹配才表示已进入关卡。`other_screen` 表示当前为尚未接入的菜单。聚落地图仍使用 `get_map`。

## 聚落任务与游戏按钮（0.4.0）

进场后用 `get_objectives` 获取本关目标、进度和结算。以游戏结算卡的 `won` 或 `lost` 判断结果。遇到暂停、介绍或事件时，用 `get_game_controls` 读取游戏文本与当前可交互的按钮。取得控制权后，调用 `activate_game_control(control_id, expected_revision)`，再读游戏状态确认选项是否生效。状态变化后重新取得按钮和 revision。

## 定居选址（0.4.1）

在开局读取 `get_settlement_sites`。若车队尚未定居，从可用候选中选择地块，取得控制权并暂停，重新查询 revision，再调用 `settle_zone`。用 `settled` 和 `actual_zone_id` 复核选址结果。

## 地块生产与聚落发展（0.5.1）

在 `get_map` 查到地块 ID 后，使用 `get_zone_details(zone_id)` 查看高产、道路、防御工事、沼泽步道等改良，以及已建建筑、等级和施工项目。每座有生产组件的建筑与已定居的车队单独列出配方、运行状态、当前暂存量和游戏估算产能；`production_by_resource` 按资源汇总。这些是本地生产读数，暂存量不等于每回合入库量。

`building_actions` 和 `list_actions(zone_id, kind="building")` 都返回游戏建造候选、可用性、锁定原因、结构化费用和可供 `execute_action` 使用的凭据。暂停后取最新动作和 revision。

`get_military` 提供实际驻军、位置、生命、军队容量和招募项目。`get_technology` 提供研究等级、知识进度、已应用科技以及游戏生成的发展选项。`training_catalog` 记录训练定义与科技和人口要求，当前能否购买以 `recruitment` 的可用状态为准。招募与发展项目通过聚落界面原有按钮购买。

## 桥接协议

`POST http://127.0.0.1:17840/v1/call`，必须带 `X-IncMcp: 1` 且不带 `Origin`：

```json
{ "command": "get_state", "args": {}, "request_id": "<uuid>", "client_id": "<uuid>" }
```

- 请求体上限 64 KiB；`client_id`/`request_id` 长度 8~100。
- 写操作（`acquire_control`/`release_control`/`set_speed`/`execute_action`/`advance`/`unpause`/`step_turn`）在 10 分钟账本内按 `client_id + request_id` 去重；参数不同会拒绝。网络超时后用**相同 request_id** 重试，不要新建请求。
- 服务端 25 秒无结果会返回 `OUTCOME_PENDING`；客户端 28 秒超时，MCP 工具超时 35 秒。

## 目录

```
server.mjs                 MCP 服务入口
client.mjs                 独立 MCP 客户端示例
lib/bridge-client.mjs      桥接 HTTP 客户端
plugin/                    BepInEx IL2CPP 插件（C#, net6.0）
  Plugin.cs                HTTP 监听、去重账本、配置项
  GameBridge.cs            主线程调度、状态读取、动作执行
  CompilerCompat.cs        interop 编译兼容
tools/MiniProbe*           加载器/插件隔离实验用最小插件
tools/inspect_game.py      只读解析 global-metadata.dat
tools/dump_types.py        按类型名导出字段/方法签名
scripts/install-loader.ps1 安装 BepInEx（兼容 PowerShell 5.1）
scripts/deploy-plugin.ps1  部署插件 DLL（可 -Restart）
scripts/register-dsh.mjs   注册 DSH MCP（参考 config/dsh.patch.yml）
tests/bridge.test.mjs      MCP 契约测试
analysis/                  可行性、静态证据、构建日志、排查记录
backups/                   加载器/存档备份与 manifest
config/                    DSH 与 Codex 的 MCP 注册片段
```

## 环境与构建

- 游戏：Steam App ID `3337140`，Unity 6000.0.62f1，Windows x64 IL2CPP
- 默认游戏路径：`F:\SteamLibrary\steamapps\common\After Inc`
- .NET SDK：项目自带 `.tools\dotnet`（8.0.x，可编译 net6.0）
- Node.js：`C:\nodejs\node.exe`

```powershell
# 编译插件
$env:DOTNET_CLI_HOME='E:\incMCP\.tools\dotnet-home'
& E:\incMCP\.tools\dotnet\dotnet.exe build E:\incMCP\plugin\AfterIncBridge.csproj -c Release

# 部署（游戏需先关闭）
& E:\incMCP\scripts\deploy-plugin.ps1

# 启动（必须经 Steam，直接跑 exe 会因 Steamworks 初始化失败退出）
Start-Process 'steam://rungameid/3337140'

# 契约测试
& 'C:\nodejs\node.exe' --test tests/*.test.mjs
```

加载器安装（`.tools\bepinex` 放 BepInEx 发行包解压内容）：

```powershell
& E:\incMCP\scripts\install-loader.ps1
```

## 使用流程

1. 启动游戏 → 主菜单「继续」→ 进入聚落（或新建远征）。
2. MCP 客户端调用 `status`；`ready=false` 表示还在菜单/加载中。
3. `acquire_control` 后按上面的推荐顺序操作。
4. 结束时 `release_control`，游戏会暂停。

DSH 注册见 `config/dsh.patch.yml`；修改 `server.mjs` 后需要**重启 DSH 会话**，MCP 工具列表才会刷新。

## 已知限制

- 场内事件选择与完整战役自动化尚未接入。
- `advance` 走实时推进，20 秒上限；回合较长时会返回 `time_limit`。
- `step_turn` 只推进回合计数，不跑完整模拟（资源/民心不会变化），仅作应急。
- `unpause`（`ForceUnpause`）在聚落 UI 初始化期间调用会崩游戏，`advance` 已不再调用它。
- `get_state.resources` 只包含 `IsActive() && hasUI` 的生产项，体力/人手等不在其中。
- 动作句柄 60 秒过期，revision 不匹配会要求重新读取。

## 故障排查

- **桥接不可达**：游戏没启动、插件没部署，或端口被占用。检查 `BepInEx\LogOutput.log` 里 `After Inc shared bridge ... listening`。
- **`ready=false`**：游戏在菜单/世界地图/加载中，先通过界面进入聚落。
- **游戏在聚落加载时崩溃**：见 [debug-notes-20261028.md](analysis/debug-notes-20261028.md)。已确认与 BepInEx IL2CPP interop 有关，`BepInEx\config\BepInEx.cfg` 里 `UnityLogListening=false` 可缓解一类崩溃。
- **插件改动不生效**：本会话 MCP 工具是启动时固定的；改 `server.mjs` 需重启 DSH。

## 回滚

加载器只新增文件（安装前已备份存档并生成 `backups\before-loader-*\loader-manifest.json`，含 SHA256）。恢复原版：关闭游戏后删除清单里的文件与目录（顶层 `winhttp.dll`、`doorstop_config.ini`、`.doorstop_version`、`changelog.txt`，以及 `BepInEx\`、`dotnet\`）。

## 地图上的事件标记和气泡

每次进入聚落先调用 `get_map_markers`，或从 `get_map.markers`、`get_zone_details.markers` 获取当前标记。事件返回游戏图标、能识别的符号（`!`、`?`）、事件 ID、地块、标题、说明和非负剩余计时，资源气泡返回图标及游戏提供的寿命和进度。界面没有提供的文本或计时可以为空。不要只凭感叹号或问号推断事件结果。

推进时传 `advance({turns: 10, stop_on_marker: true})`，会在开始时及模拟换回合时检查。收到 `reason: "map_marker"` 后先读标记。需要处理时先取得控制权并暂停，再将最新的 `marker_id` 传给 `activate_map_marker`。事件可能打开决策窗口，接着用 `get_game_controls` 读取实际选项。领取气泡后重新检查标记和资源。要带着已知标记继续推进，可以省略 `stop_on_marker`。

第一关实测中，17 号地块曾出现 `icon_exclaimationmark`。接口在第 63 回合暂停，点击后游戏显示“发现旧报纸残片”。部分事件在点开前标题为空。

同一关的第 117 回合，15 号地块出现 `icon_questionmark`（集装箱事件），`advance` 在当回合停下。标记出现时可能尚不可点，查看 `interaction_blocker`：`appearing` 表示仍在显现，`menu_or_dialog` 表示被窗口挡住。游戏提供的探索进度和显现回合数一并返回。

## 战士部署、接战与前哨站

1. `get_military` 返回训练中、等待部署和已经在地图上的部队。一队战士就绪后，调用 `get_soldier_orders`，从返回的可部署地块中选一个，把地块和本次查询的 `revision` 传给 `deploy_soldier`。用等待数量、部队编号和实际驻地复核结果。
2. 需要调动时，带上部队的 `unit_id` 再查一次 `get_soldier_orders`，用返回的候选和版本调用 `move_soldier`。移动有行进时间，用 `advance` 推进并查看 `get_military`。在战士邻区的 `list_actions` 中可以查找游戏提供的「嘲讽」，执行后检查交战状态和 `get_objectives`。
3. `get_outposts` 查询前哨站库存、已放置的前哨站和地块条件。先通过正常聚落发展获得库存，选择当前允许的位置，调用 `build_outpost` 并检查库存变化和地块状态。升级与拆除以该地块 `list_actions` 的结果为准。

下令或放置需要控制权和最新返回的版本，支持模拟运行中操作。场景、库存、地块或可执行状态发生变化后重新查询。



实时游玩可以保持 Normal 或 Faster 速度。地图事件、区域动作、士兵命令和商店通过游戏主线程重新验证。遇到 STALE_STATE 时重新读取候选；若通信失败且结果不明确，使用相同请求标识重试。用 get_settlement_purchases / purchase_settlement 查询和购买本局商店项目。
