# Plague Inc: Evolved MCP

Windows 本地 MCP 服务，通过标准输入输出（stdio）连接客户端。项目位于 `E:\PlagueIncEvolvedMCP`，使用 Node.js 22.12 或更高版本。

## 现有工具

`list_installations {}` 同时列出 Steam 版和 E 盘本地副本的位置、游戏版本、Unity 版本、构建号与各自的进程。用 `get_status {"installation":"steam"}` 或 `get_status {"installation":"local"}` 单独查询。每次调用可重新选择。本地副本移动后，可用 `PLAGUE_LOCAL_DIR` 指向新目录。指定安装时，如遇到系统隐藏进程路径且没有明确匹配，`running` 返回 `null`。

两份游戏的实际区别、官方更新内容和文件校验结果见[双版本对照](双版本支持/README.md)。

`get_status` 读取游戏安装和运行状态，返回 Steam App ID、安装目录、构建号和运行中游戏进程的 PID 与可执行文件路径。查询可以在游戏关闭时使用。若本机有多份游戏，`directory` 表示选中的安装，`processes` 会列出所有正在运行的同名游戏进程。

默认通过 Steam 库定位应用 246620。要选用其他副本，可在客户端的 `env` 中为 `PLAGUE_GAME_DIR` 填入含 `PlagueIncEvolved.exe` 的目录。没有匹配 Steam 清单的副本会返回 `buildId: null`。变量 `PLAGUE_STEAM_ROOT` 可用于指定 Steam 客户端根目录。

### 单人游戏主菜单

`main_menu {}` 读取 Steam 版的当前页面和单人相关按钮。`options` 提供游戏界面文字和 `available`，选择时带上同一次读取的 `state_id`。

| `choice` | 对应游戏操作 |
| --- | --- |
| `single_player` | 打开单人模式选择页 |
| `load_game` | 在按钮可用时打开存档列表 |
| `back` | 从上述页面返回主菜单 |

选择成功并回读到目标页时返回 `applied: true`。超时后可以用相同参数和响应中返回的 `request_id` 重试。收到 `STALE_MENU` 时先重新读取。`GAME_THREAD_TIMEOUT` 表示游戏主线程未及时响应（游戏窗口失焦时 Unity 会停止 Update），可以切回游戏窗口再读取。

桥接 endpoint 默认写在游戏进程的临时目录。客户端进程的 `TEMP` 与之不同时，工具会同时查找 `%LOCALAPPDATA%\Temp\plague-inc-evolved-mcp`。

主菜单工具需要 Steam 版的 BepInEx 5 x64 Mono 和 `PlagueIncBridge.dll`。在加载器已经安装的情况下，运行 `dotnet build plugin/PlagueIncBridge.csproj -c Release`，把生成的 DLL 放到 Steam 版游戏目录的 `BepInEx/plugins`，再从 Steam 启动游戏。项目默认引用本机 F 盘安装，也可以在构建时用 `-p:GameDir=...` 指定。Codex 与 DSH 的任务交接见 [DSH 协作](DSH协作.md)。

### 单人模式选择

进入单人游戏后，调用 `single_player_menu {}` 获取当前选中模式、可见模式及「开始游戏」按钮状态。

| `choice` | 模式 |
| --- | --- |
| `classic` | 主游戏 |
| `outbreak` | 疫情 |
| `cure` | 解药模式 |
| `speed_run` | 急速通关 |
| `official_scenarios` | 官方场景 |
| `custom_scenarios` | 自定义场景 |

传模式 ID 和最新 `state_id` 选择模式，再用响应中的新 `state_id` 和 `choice: "start"` 进入对应的设置或场景列表。收到 `applied: true` 表示已经回读到选中模式或目标页面。返回主菜单用 `main_menu` 的 `back`。

### 单人病原体选择

在相应模式的病原体页调用 `disease_menu {}`。它返回模式 ID、当前选中项，以及游戏显示的病原体 ID、名称、锁定和可用状态。传可用病原体的 `choice` 和同次读取的 `state_id` 完成选择，游戏会停留在当前页。

主游戏、解药模式和急速通关返回各自的病原体列表。疫情模式先选疫情，`outbreaks` 列出当前条目及游戏为其分配的病原体。用条目 ID 选择后可读取 `selected_outbreak`。官方和自定义场景列表返回 `choose_scenario_first`，需要先进入具体场景。场景进入病原体选择页时，工具读取该场景实际提供的选项。

### 单人开局设置

`game_setup {}` 返回当前的 `stage`、`options`、名称和难度。模型用最新 `state_id` 调用当前页面的动作。`next` 和 `back` 操作游戏的继续和返回按钮。基因页可以选槽位、预览并插入已解锁的基因，也可直接继续。难度页按选项 ID 选择。名称页用 `set_name` 并传 `name`，或使用游戏提供的随机名称。

在最后一步调用 `next`，工具等待游戏切到地图。`in_game: true` 表示已到地图，`stage: "choose_country"` 表示该局正在等待初始国家选择。

### 初始国家选择

进入地图后调用 `start_country {}`。它返回 `popup`（游戏自带的开局说明弹窗）、`allows_choice`，以及 `countries` 列表，每项包含 `id`、本地化名称 `label`、`population`、初始 DNA 加成 `bonus`、`island` 和 `continent`。

开局弹窗需要先关闭：用最新 `state_id` 传 `choice: "dialog_close"`，读到 `allows_choice: true` 后再传国家 `id` 确认初始国家。确认后返回 `screen: "in_game"`、`stage: "in_progress"`、`applied: true`；此时不再返回国家列表。弹窗未关时确认国家会返回 `POPUP_BLOCKING`，不在选择页时返回 `WRONG_MENU`。

### DNA 点数与进化

对局中调用 `evolution {}` 读取 `dna_points`、`dna_spent`、`dna_earned`、`turn`、当前 `available` 技术（含 `id`、名称、类型 transmission/ability/symptom、层级和 `cost`）以及已进化的 `evolved` 列表。

传可进化技术的 `id` 和最新 `state_id` 执行进化，成功返回 `applied: true` 和更新后的 DNA 与列表。游戏自身规则仍会校验：DNA 不足返回 `INSUFFICIENT_DNA`，前置未满足返回 `OPTION_LOCKED`，已进化返回 `ALREADY_EVOLVED`。进化会让依赖它的下一级技术出现在 `available` 里。

## 客户端接入

支持 stdio 的 MCP 客户端可以直接运行以下配置中的命令，无需先单独启动服务。

```json
{
  "mcpServers": {
    "plague_inc_evolved": {
      "command": "C:\\nodejs\\node.exe",
      "args": ["E:\\PlagueIncEvolvedMCP\\server.mjs"]
    }
  }
}
```

## 开发与验证

```powershell
Set-Location E:\PlagueIncEvolvedMCP
npm.cmd ci
npm.cmd test
```

测试包括临时 Steam 库的安装识别、桥接 endpoint 查找，以及通过真实 stdio 客户端建立连接、列举和调用工具。直接运行 `node server.mjs` 时，服务会等待客户端从标准输入发来请求。

联调时可用 `node test/drive.mjs <tool> ['{"json":"args"}']` 在没有 MCP 客户端的情况下逐条调用工具；该脚本不属于 `npm test`。

项目使用官方 MCP SDK。游戏的 Steam App ID 为 246620。[SDK v1 源码](https://github.com/modelcontextprotocol/typescript-sdk/tree/v1.x) · [游戏商店页](https://store.steampowered.com/app/246620/)
