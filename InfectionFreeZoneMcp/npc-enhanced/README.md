# 加强版 NPC 阵营 v0.2.0（AI 接管版）

2026-11-16 0.2.0：新增 **AI 模型接管**。AI 通过 MCP 读取某一个 NPC 阵营的完整局势，并可直接下令：AI 接管/恢复自动、经营方针、小队搜刮/移动/回仓/攻击、岗位派工人数、设施换产、指定建筑改造施工。接管为阵营级租约，不能操作玩家或全局控制；模型离线时可用 `mode take_over=false` 交回本地自动经营。

同批修正：工人归属改为调用联机模组自己的 `EconomyPartition.GetOwner(Character)`（群组 → 实体 ID → 住房建筑），替代此前只按群组/ID 猜测的写法；旧写法可能把模组自己算到的工人读成 0，从而让生产、施工、派工全部静默停摆。状态报告新增接管模式，工人归属诊断会列出未归属样本的各个归属来源。

本版需要重新安装插件 DLL（已构建并校验），并重启 DSH 会话来加载新增的 `npc_*` MCP 工具；实机接管效果仍待验证。

2026-11-16 修正：实机出现 NPC 携货向玩家总部移动，原生 `AddOrderMoveToNearestStockroom` 使用全局仓库查找，首版没有覆盖该路径。新增对全部已登记 NPC 的自动回仓约束，指向各自总部；拒绝普通进楼命令进入其他阵营仓库/总部，显式贸易交换除外；对读档时已有的错误目的地进行检查。仅第一个 NPC 的完整经营仍由加强版接管，其余 NPC 保留原决策。

同批修正：联机禁用任务后，原版 AnalyticsSender 收到空任务仍访问 Id，导致严重错误窗口；只跳过空任务统计回调。反射成员读取改为缓存且直接区分字段/属性，消除 EconomyPartition.Enabled 等字段被先当属性查询导致的每帧警告。状态报告新增小队目标建筑 ID 与归属。53 项静态/策略检查通过，0.1.1 尚待重新启动实机验证。

更新：保存并退出游戏后，双击项目根目录 `启动加强NPC联机版.cmd`，自动备份并安装新 DLL。0.1.1 无需主动新建地图；已有存档恢复及错误订单纠正仍需实测。

本插件给 `ifz-coop-mod-0.3.0-rc7-p7.zip` 中的一个 NPC 阵营增加本地经营控制。日常运行不调用模型，不依赖 MCP、Node、Codex 或 DSH 保持开启。需要游戏保持运行；暂停、返回菜单或游戏不推进时，阵营不会离线发展。

0.1.0 已在用户新地图运行，观察到搜刮和回仓记录，也发现了上述回仓与初始化问题。最新快照中被接管阵营工人数为 0，尚未证明自主生产、建设、持续生存及读档恢复，不应把静态检查通过当成完整经营验证。

## 如何开始观察

1. 登录 Steam，使用项目根目录的 `启动加强NPC联机版.cmd`，或在 Steam 游戏库点击“开始游戏”。启动器使用游戏真实 AppID 1465460；Steam 正版安装保留原始 Steam 接口，不使用联机包附带的 AppID 480 配置。
2. 进入 `MULTIPLAYER → HOST CAMPAIGN → HOST`，选择地图、锁定地点，然后 `HOST GAME`，进入游戏并选择自己的总部。可设为 Friends。普通单人新游戏不会运行本控制器。
3. 新战役若没有设置 NPC，插件在建立 NPC 时请求一个；若已经设置多个，则只接管按阵营 ID 排序的第一个，其余沿用模组控制。已有存档不会因为这个设置凭空新增 NPC。
4. 游戏自动为 NPC 选择合法位置并建立总部。左侧 `Enhanced NPC` 面板显示实际阵营名称、ID、总部世界坐标，以及相对你的总部的东/北偏移；负数表示西/南。按 F8 隐藏或显示。
5. 解除暂停，派自己的队伍按面板方位寻找该阵营。默认保留模组发现规则，不自动揭开全地图。观察其小队出勤、回仓、建筑施工和生产派工。

若面板一直显示等待主机战役，检查是否使用了联机 HOST 流程。若显示 `DISABLED`，查看版本或日志，不要跳过版本检查。名称、坐标出现但不发展时，检查游戏是否暂停，并查看状态文件中的任务与资源。

## 第一版实际实现

| 范围 | 行为 |
|---|---|
| 自主运行 | 游戏进程内定期调度；本地规则决定行动，模型调用数为零 |
| 阵营控制 | 仅替换选定 NPC 的默认决策循环；保留模组建总部、经济隔离和原生模拟 |
| 派工 | 仅使用明确归属本阵营的健康工人；优先粮食、必要医疗、施工；普通单岗位初期最多两人，避免四名初始工人被一个岗位全部占用 |
| 生产 | 在已解锁且原料足够的配方间选择，沿用原生周期、工人、材料消耗和产出 |
| 建设 | 挑选总部附近无归属建筑，核对库存与解锁后排入正常改造施工；不调用立即完工；一次只做一个工程 |
| 搜刮 | 选择附近可搜、无归属、无明显感染者威胁的建筑；避免同一目标重复派队；已有真实粮食信息时增加优先级 |
| 回仓 | 空闲且携货时回自己总部；核对队员手持物资、队伍容器及车辆后备箱 |
| 装备与弹药 | 总部附近用本阵营库存给近战队员分配可供弹的枪，并消耗本阵营弹药补给 |
| 防守 | 入夜回家；感染者逼近总部时召回；在总部附近对原生可攻击的感染者下达防守攻击 |
| 撤退与恢复 | 队员血量低于 60% 时回家，恢复到 90% 才退出恢复状态；不直接改血量，治疗依赖游戏原生设施机制 |
| 失败处理 | 拒绝、无收益搜刮、路径不可达和长时间无进展触发本地冷却，避免每帧反复下单 |
| 读档 | 阵营和经济由原联机模组存档。重新接管时保留原生进行中的订单，根据现有建筑/施工重建计划；第一版的临时失败冷却与日志队列不随存档恢复 |
| AI 接管 | `mode take_over=true` 时本地调度停止下单、只报告；派工/生产锁与安全钩子保留。`false` 时恢复本地自动经营 |

## 观察数据

- 游戏日志：`BepInEx/LogOutput.log`，检索 `IFZ Enhanced NPC`、`[NPC `、`MP-NPC`。
- 实时摘要：`BepInEx/config/IfzNpcEnhanced/status.json`。正常运行每约 15 秒更新，包含阵营、位置、库存、任务人员/进度、订单、最近动作、接管模式与错误数；检查 `sampled_utc`，暂停或退出后旧文件不代表仍在运行。
- 配置：`BepInEx/config/local.ifz.npc-enhanced.cfg`。首次加载生成。`Enabled=false` 停用增强；`NpcSlot` 指定阵营序号；`EnsureOneNpcOnNewCampaign` 控制新战役默认请求一个 NPC。

建议观察：完成至少两轮搜刮回仓；一栋建筑是否经过施工后完成；生产是否实际产出；你的工人/库存是否被误用；入夜与感染者逼近时是否召回。随后保存、返回菜单、读档，检查是否恢复且没有重复建造。发现问题时保留状态文件和游戏日志，再继续修复。

## AI 接管（MCP）

模型通过 MCP 的 6 个 `npc_*` 工具接管指定阵营。工具由 MCP 服务端注册，桥接方法由本插件提供；插件未加载或不在主机战役时返回 `npc_plugin_required` / `npc_not_ready`。

| 工具 | 作用 |
|---|---|
| `npc_status` | 检查插件与联机战役是否就绪，返回当前选中的阵营 ID |
| `npc_list_factions` | 列出联机 NPC 阵营的 ID、名称、建成状态、总部和可接管状态。原版中立区域用 `get_npc_zone` 查询 |
| `npc_get_faction` | 读取资源、工人、小队、岗位、施工、生产、威胁、可用目标和接管模式。阵营隔离测试副本提供 `vehicles` |
| `npc_acquire_control` | 申请或续订该阵营的 90 秒写租约 |
| `npc_command` | `mode`、`strategy`、`workers`、`production`、`build`、`scavenge`、`move`、`return`、`auto`、`attack`。阵营隔离测试副本支持 `take_vehicle` |
| `npc_release_control` | 释放租约。恢复自治的行为取决于桥接版本 |

### 阵营隔离测试副本的 MCP 接口

- 在工作区的隔离游戏副本设置 `IFZ_MCP_TEST_COPY=1`、`IFZ_MCP_MANUAL_FOUNDING=1`，并载入一个还没有联机 NPC 的主机存档。手动模式等待 MCP 指定总部。
- `npc_found_faction` 使用 `acquire_control` 获取的全局租约。通过 `get_map_buildings` 选择 `hq_building_id` 和附属 `building_id`，填写 `name`、`workers`、`squads`，每队四人。附属建筑支持 `bld_house`、`bld_shelter`、`bld_warehouse`。这些建筑使用地图上现有房屋。
- `starting_resources` 接受 `{resource_id, crates}` 数组，传入数组时替换默认初始物资。先用 `preview=true` 检查完整计划，确认后提交同样的参数并省略 `preview`。在本次载入中用同一 `request_id` 重试会返回原结果。建成后可用 `npc_get_faction` 核对资产和异步生成的开局车。
- `npc_get_faction` 返回 `vehicles` 时，`claimed=true` 表示归本阵营，`claimed=false` 表示附近已发现的无主车。`squad_id` 记录当前使用该车的小队。
- 使用 `npc_command` 的 `action=take_vehicle` 时，从同一阵营的状态取 `squad_id` 和 `vehicle_id`，并传入 `session_id`、`faction_id`、`lease` 和一个新的 `request_id`。命令受理后，重新读取 `vehicles` 确认目标车的 `squad_id` 已变成指定小队。
- 测试副本的桥接会在租约释放或到期时恢复本地自治。使用旧版桥接时，在释放租约前通过 `mode` 和 `take_over=false` 交回控制。
- `npc_acquire_control` 支持续租。传入原来的 `session_id`、`faction_id`、`owner` 和当前 `lease`，即可续到 90 秒。

典型流程：`get_state` 取 `session_id` → `npc_list_factions` → `npc_get_faction` 选一个阵营 → `npc_acquire_control` → `npc_command action=mode take_over=true`（或保留本地自动、只用 `hold_hours` 覆盖个别决策）→ `npc_command` 逐个下达指令 → 结束时 `npc_release_control`。

命令语义要点：

- `mode take_over=true` 后本地调度只报告不再下单；`take_over=false` 时清空接管状态并恢复本地自动经营。
- 小队指令带 `hold_hours`（0.25–12 游戏小时），期间本地调度不会覆盖。
- `workers` / `production` 会锁定该建筑 `hold_hours`，期间本地调度保留模型的选择。
- 命令必须带新的 `request_id`；同一 `request_id` 只用于重试同一命令，结果不确定时先读状态，不要重复下单。
- 校验失败（目标不属于本阵营、材料不足、配方未解锁、已有施工等）返回具体错误码，先读状态再决定。

## 明确边界

- 选址由游戏自动完成，本版没有模型选址；接管只针对已建立的 NPC 聚落。
- 经济隔离沿用 RC7-P7，并增加派工归属防护；底层模组的生产、食物、住房、士气和研究机制仍需实机审查。没有宣称所有游戏系统已经彻底隔离。
- 本版建设覆盖既有建筑改造，不包括从零造田、城墙与新地基。地图、解锁或初始资源不足时，自给生产可能无法建立，仍会依赖搜刮。
- 不自动创造人口、小队、武器或资源；人口增长与招募、独立科研、车辆获取、主动外交交易和人类敌对阵营作战不属于本版新增能力。
- 需要能供给枪械/弹药的小队才能安全出勤；库存不足时保留在总部，状态报告可用于诊断。
- 接管后不再执行原 NPC 的立即建造、直接生成小队等默认决策。已有原生自动战斗、休息和治疗行为继续运行。
- `mode take_over=true` 时本地调度不再兜底：模型断线、额度耗尽或不发指令，阵营会停在原地等待（小队仍有原生战斗/休息行为）。需要无人值守时用 `take_over=false`。
- 长期生存能力尚未实测，不能把离线单元测试解释为实机经营验证。

## 编译、检查、安装与回退

目标游戏 Steam build `26104777`；仅接受本地检查过的 RC7-P7 GameIntegration DLL 的 SHA256。与其他模组版本不兼容时停用控制器并显示错误。

```powershell
.local\repair-dotnet\dotnet.exe build npc-enhanced\IfzNpcEnhanced.csproj -c Release -p:NuGetAudit=false
.local\repair-dotnet\dotnet.exe run --project npc-enhanced\tests\NpcTests.csproj -c Release -- .
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Install-NpcEnhanced.ps1 -GameRoot 'F:\SteamLibrary\steamapps\common\Infection Free Zone' -InstallCoop -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Install-NpcEnhanced.ps1 -GameRoot 'F:\SteamLibrary\steamapps\common\Infection Free Zone' -InstallCoop
```

安装器先核对版本、进程和重复插件，再备份所有将覆盖的文件到游戏目录 `_npc_enhanced_backup_<时间>-<随机后缀>`，记录新增文件清单；安装原压缩包中的联机 DLL、兼容桥以及本插件，不替换 Steam API、不写 AppID 480、不自动启动游戏、不修改存档。

2026-11-15 启动修正：最初按压缩包说明使用 AppID 480，实机出现 `Application load error V:0000065432`，Steam 日志记录以 480 跟踪游戏，且未产生新的 BepInEx 插件日志。已将启动方式调整为 Steam 正常启动，并提供 `scripts/Repair-NpcSteamLaunch.ps1` 从首次安装备份恢复原版 Steam 接口。错误是否完全消除、联机大厅兼容性需重启实测；本地 NPC 不依赖另一名玩家加入。

关闭游戏后可以使用 `scripts/Restore-NpcEnhanced.ps1 -GameRoot '<游戏目录>' -BackupDirectory '<本次备份目录>'` 恢复安装前文件。恢复会删除清单中的本次新增文件，保留游戏存档和备份。停用增强或回退后，已运行过的地图状态仍保留此前发生的游戏变化。

插件源码为本项目新增实现；第三方联机组件没有被修改或打入源码。构建引用来自本机游戏及 BepInEx，测试的接口元数据来自用户提供的压缩包。安装包只分发增强 DLL 与说明，联机包仍需单独提供。
