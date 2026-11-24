# SlimeRancher2MCP · 史莱姆牧场 2 的桥接 + MCP 工具

> 目标：让 DSH/AI 通过 MCP 读写《Slime Rancher 2》的游戏状态。架构与 1 代
> [`E:\SlimeRancherMCP`](../SlimeRancherMCP) 同构，**实现层适配 Unity 6 + IL2CPP**。
> **0.5.34 已部署**，新增 F8 一键整理背包与已加载结晶收集器。保留附近材料 HUD（HUD「附近科技材料」，左上角，默认 22 号字，最多 8 行 + 「…还有 N 个」，F6 开关 / F5 调字号；距离 1 位小数 + 方位 + 高度差，0.25 秒刷新）。`ranch.py` 有 **33** 个子命令，其中 **23** 个是可直接运行的操作。MCP 目录共 **80** 项，其中 **28** 项可写。
> 实时状态、工具矩阵与已知问题见 [docs/STATUS.md](docs/STATUS.md)；版本记录见 [docs/CHANGELOG.md](docs/CHANGELOG.md)。

## 附近科技材料 HUD（0.5.33）

进游戏后屏幕**左上角**（时钟下方）显示悬浮列表，0.25 秒刷新，列 150 米内：

- **READY 野外料点**：天然蜂蜜巢、蜂蜡巢、辉光矿、细沙、深卤水、奇异钻石…（带份数 + 距离）
- **地上的 `*Craft` 科技材料**：按物品分组显示数量与最近距离

每行格式：`名称 份数/数量 水平距离 方位 高度差`，例如 `天然蜂蜜 3份 74.2m 左前 ↑14m`。距离保留 **1 位小数**（走路时连续变化），方位按玩家朝向给 **前/右前/右/右后/后/左后/左/左前** 八个扇区，高度差 ≥4 米时才显示 `↑`/`↓`。

**F6** 开关（默认开启）；**F5** 循环调字号（默认 22 号，18/21/24/27 四档），面板宽度随字号缩放。列表**最多 8 行**（`MaxRows`），超出时末尾显示 `…还有 N 个`。附近没有材料时不绘制。数据来源与 `sr2_resource_nodes`（料点）、`sr2_nearby category=craft`（地上材料）一致，只读。原版地图与 SlimeMap 都不支持自定义标记，所以用这个 HUD 代替（SlimeMap 1.0.2 仅提供地形/雷达与范围配置，没有插标记接口）。


```
DSH / MCP client ──stdio──▶ mcp\server.py ──HTTP 127.0.0.1:18762 + token──▶ 游戏内 MelonLoader 插件
                                                                     （Unity 6 / IL2CPP）
```

## 游戏里说一句话

在游戏聊天框按 Enter 或 F2，用平常的话说出请求。动作明确时，直接运行 `tool/ranch.py` 的对应子命令，脚本会自行核对条件并返回结果。需要调查或判断时，AI 用 MCP 查询游戏状态。`--reply` 会把摘要送回游戏聊天框。

| 玩家说 | AI 运行的命令（项目根目录下） |
| --- | --- |
| 看看我现在的情况 | `python -B tool/ranch.py status --reply` |
| 游戏里几点了 | `python -B tool/ranch.py time --reply` |
| 现实里几点了 | `python -B tool/ranch.py clock --reply` |
| 看看包里有什么 | `python -B tool/ranch.py bag --reply` |
| 补 30 点生命会到多少 | `python -B tool/ranch.py heal 30 --reply` |
| 确认给这台喂食器投一份 | `python -B tool/ranch.py feed <control_id> --apply <请求ID> --reply` |
| 我的盐水放哪儿了？ | `python -B tool/ranch.py find 盐水 --reply` |
| 附近多少米有特殊材料？ | `python -B tool/ranch.py materials --reply` |
| 做两台这个设备还缺什么？ | `python -B tool/ranch.py recipe "<设备名称>" 2 --reply` |
| 确认制作两台，留在库存 | `python -B tool/ranch.py craft "<设备名称>" 2 --apply <请求ID> --reply` |
| 20 颗棉花结晶今天值多少？ | `python -B tool/ranch.py quote 棉花结晶 20 --reply` |
| 主仓还有几格空着，放了什么？ | `python -B tool/ranch.py stores main --reply` |
| 我想往主仓存 5 颗虎斑结晶 | `python -B tool/ranch.py store 虎斑结晶 5 --reply` |
| 确认从主仓拿 3 根胡萝卜 | `python -B tool/ranch.py take 胡萝卜 3 --apply <请求ID> --reply` |
| 把背包里的虎斑结晶全部存进主仓 | `python -B tool/ranch.py store-all 虎斑结晶 --apply <请求ID> --reply` |
| 把某座筒仓的盐水全部取回 | `python -B tool/ranch.py take-all 盐水 --storage <筒仓标识> --apply <请求ID> --reply` |
| 把一座筒仓的 20 颗虎斑结晶直接搬进主仓 | `python -B tool/ranch.py transfer 虎斑结晶 20 --from <筒仓标识> --to main --apply <请求ID> --reply` |
| 把各区仓库的重复堆合并 | `python -B tool/ranch.py consolidate --apply <请求ID> --reply` |
| 把背包里的盐水放进精炼炉 | `python -B tool/ranch.py refine-bag 盐水 --apply <请求ID> --reply` |
| 一键整理背包和结晶收集器（桥接 0.5.34） | 进入存档、关闭聊天框，轻按 **F8**。先整理背包，再把已加载收集器的结晶搬进筒仓。两步结果可在聊天记录查看。 |
| 单独整理背包 | `python -B tool/ranch.py tidy --apply <请求ID> --reply` |
| 单独整理结晶收集器 | `python -B tool/ranch.py plorts --apply <请求ID> --reply` |
| 每种结晶只留一成、其余卖掉 | `python -B tool/keep10_sell.py --apply <请求ID> --reply` |
| 所有仓库按产区归位 | `python -B tool/zone_fix.py --apply <请求ID> --reply` |
| 启动结晶收集器 | `python -B tool/ranch.py start-plort-collector [控制ID] --apply <请求ID> --reply` |
| 启动资源收集器 | `python -B tool/ranch.py start-resource-collector [控制ID] --apply <请求ID> --reply` |
| 筒仓面板切到下一槽 | `python -B tool/ranch.py silo-next [控制ID] --apply <请求ID> --reply` |
| 喂食器调到慢档 | `python -B tool/ranch.py feeder-speed 慢 [地块ID] --apply <请求ID> --reply` |
| 吸附近最多两份盐水 | `python -B tool/ranch.py collect 盐水 2 --apply <请求ID> --reply` |
| 追踪指定设备的配方 | `python -B tool/ranch.py pin-recipe "<已拥有的蓝图>" --apply <请求ID> --reply` |
| 取消配方追踪 | `python -B tool/ranch.py clear-recipe --apply <请求ID> --reply` |
| 立即保存 | `python -B tool/ranch.py save --apply <请求ID> --reply` |

`stores` 列出容器标识，`store` 和 `take` 可用 `--storage <标识>` 指定其他筒仓或已加载的场景容器。地块仓库使用存档里的稳定键，场景容器使用本次加载的实例标识。数量按所有可用槽位合计，库存或容量不足时返回可搬数量并保留原库存。运行中若转移中断，回复会给出已经核实的件数。

`store-all`、`take-all` 和 `transfer` 需要桥接 0.5.27 或更新版本。它们支持未加载的地块筒仓，容量不足时整批保留在来源。`transfer` 无需背包有空位。`refine-bag` 按精炼炉实际余量存入，剩余物品留在背包。

`consolidate --storage main` 可只整理主仓。默认归堆覆盖全部可写地块筒仓，只有物品和外观相同的已占用槽会合并。确认同区时可跨仓合并，区域未知的远程筒仓在仓内整理。

用 MCP 的 `sr2_storage_list` 查看所有可读筒仓的槽位、数量、容量和解锁状态。用 `sr2_zone_buildings` 按区域盘点已建地块、已购改良和放置设备。可传区域 ID 或完整中文名，省略时查玩家当前区域，`include_empty=true` 可包含空地。未能归属的记录会单独计数。

`find` 汇总背包、已登记的筒仓、已加载的场景容器及精炼炉，支持物品 ID、短名和部分中文别名。`recipe` 列出整批费用与缺口，并指出背包、仓库里已有的待入炉材料。两个命令都会附带附近地表材料的提示。

`heal` 和 `energy` 的点数省略时表示补满。`slot <index>` 按从 0 开始的编号切背包槽。设备操作可在同类型只有一个已加载设备时省略目标，有多个时返回候选。`collect` 默认在 20 米内收取最多 1 件。`[参数]` 表示可以省略，命令里不输入方括号。写操作默认预演。玩家明确要求执行时，直接在同一条命令提供 `--apply` 和本次请求 ID，重试使用原 ID 和参数。项目以外可用脚本绝对路径。

DSH 聊天监听在收到单条短消息时将原文直接交给模型，批量或较长的消息按游标补读。已有监听进程在下次加载模块时采用这一通知方式。`clock` 可在游戏关闭时直接输出北京时间；游戏内回信需要桥接在线。

## 区域设备材料

- 用 `sr2_nearby` 的 `category=craft` 查玩家附近已刷出的可吸材料。用 `sr2_resource_nodes` 的 `state=READY` 和 `loaded_only=true` 查已加载料点的待产物、坐标。`ranch.py materials` 汇总这两类读数并给出最近距离。现有料点接口按场景加载范围查询，邻区也可能同时加载，结果无法作为指定区域的精确总量。
- 用 `sr2_zone_buildings` 查某区放置的设备，再用 `sr2_gadget_catalog` 查每类设备当前配方，按设备数量汇总材料。要算再做一批还缺多少，用 `sr2_gadget_recipe` 核对精炼炉内可用量，再用 `find` 核对背包和筒仓里待入炉的数量。

## 远程购买

购买条件取决于游戏对象是否已加载。桥接的购买代码没有按距离米数设置门槛。

| 购买内容 | 工具 | 执行条件 |
| --- | --- | --- |
| 设备蓝图 | `sr2_buy_blueprint` | 进入存档，能解析蓝图和购买费用，且余额符合条件。直接扣新币并解锁蓝图，无需打开商店。买后扣款和拥有状态回读仍待实机验证。 |
| 商店商品 | `sr2_shop_list` / `sr2_shop_buy` | 商店运行时可用且加载完成时能查商品。真正购买还要打开对应商店界面，让游戏使用该界面的发货点。 |
| 空地上建筒仓、围栏等 | `sr2_plot_catalog` / `sr2_plot_buy` | 购买所需的地块终端必须已加载。购买资产可读时可先查价。 |
| 筒仓扩展槽位 | `sr2_plot_upgrades` / `sr2_plot_upgrade` | 终端不可用时，现有备用路径仍需要已加载的筒仓本体，实际购买待验证。按玩家约定，由玩家购买，工具查价格和核对解锁状态。 |

## 现状（2026-11-20）

| 项 | 状态 |
| --- | --- |
| 调研 | ✅ 见 [docs/RESEARCH.md](docs/RESEARCH.md)：SR2 = Unity 6000.0.73 + IL2CPP，MelonLoader 0.7.3+ 可用，SRML/MelonSRML 已不适用 |
| MCP 侧 | 26 个工具。新版 stdio 服务支持工具目录变更通知，DSH 可在线刷新。 |
| HTTP 通道 | ✅ 冒烟测试 21/21 通过（token / 幂等 / 中文 / 写保护 / 真实读游戏目录） |
| stdio 链路 | ✅ `tool/test_mcp_client.py` PASS |
| 插件编译 | ✅ 0 警告 0 错误 → `dist\SlimeRancher2Bridge.dll`（28 KB，MelonLoader 0.7.3 引用） |
| 游戏侧安装 | ✅ MelonLoader 0.7.3 已装 + .NET 6 运行时已预置 + 插件已部署；⏳ 待通过 Steam 启动一次验证 |

## 0.4.3 聊天框 + 背包 / 仓库 / 卖结晶（已构建，待部署验证）

仿一代做法：游戏里按 **Enter**（或 F2）呼出聊天框，输入后 Enter/F2 发送、Esc/F4 关闭。玩家消息进内存队列并落盘 `out\chat\inbox.jsonl`（超过 1 MB 自动归档），DSH 侧用 `sr2_chat_pending` 轮询取走；DSH 用 `sr2_chat_send` 推回的回复显示在面板里，聊天框关闭时弹出 15 秒提示条。面板用 IMGUI 自绘，字体会自动挑系统里的微软雅黑/黑体，中文可显示，PageUp/PageDown 可调字号。

同一版新增背包 / 仓库 / 卖结晶三组工具：

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `sr2_vacpack` | 读 | 背包槽位：编号/id/名称/数量/上限/是否选中 |
| `sr2_vacpack_clear` | 写 | 清空背包（原生 `AmmoSlot.Clear()`，**销毁性**，默认预演） |
| `sr2_storage_list` | 读 | 当前已加载场景的筒仓/传送仓（`SiloStorage`、`LinkedSiloStorage`）：位置、距离、每槽内容与剩余空间 |
| `sr2_store` | 写 | 背包转存已加载仓库或远处的牧场筒仓，按原生槽位规则分配。可用列表里的 `storage_key` 指定地块；预演显示每槽数量与放不下的物品，执行后回读两边。 |
| `sr2_sell_plorts` | 写 | 快速卖结晶（原生 `PlortEconomyDirector.TrySell`），预演给单价/预计收入，成功才扣背包，执行后回读新币 |
| `sr2_shop_list` | 读 | 商店（看护人商店/立购坊那类）→ 分类 → 物品：新币价格、其他成本、已拥有、剩余可买、是否售罄。**只读**，不购买 |
| `sr2_refinery` | 读 | 精炼炉全局储量：每种可精炼物品的现有数量 / 上限 / 剩余空间 |
| `sr2_refine` | 写 | 背包→精炼炉（原生 `AcquireRefineryResource`），全局共享、无距离概念，按实际存入量回扣背包 |

桥接工具总数 **23**。新币读取路径：`CurrencyList.GetDefaultCurrency()` → `PlayerState.GetCurrency()`；拿不到时该字段返回 null 而不影响卖出。

已知限制：① 聊天时游戏本体会继续收到按键（可能移动角色/触发快捷操作），当前不拦截游戏输入；② 若游戏禁用了旧输入类（`UnityEngine.Input` 抛异常），则只能靠 IMGUI 事件兜底，诊断计数写在 `sr2_chat_status` 和 `out\chat\chat.log`；③ 仓库工具只能操作**当前已加载场景组**里的筒仓，未加载区域/未建成的不在列表里；④ 与一代一样，聊天框状态不跨游戏重启（历史消息看 inbox.jsonl）。

构建变化：0.4.3 起 `csproj` 引用 MelonLoader 首启生成的 `MelonLoader\Il2CppAssemblies\UnityEngine.*.dll`（IMGUI 强类型）以及 `Il2Cppmscorlib` / `Il2CppSystem` / `Il2CppInterop.Runtime`。因为 `GUIStyle` 在互操作程序集里没有拷贝构造，界面改为 `new GUIStyle()` 自建样式 + 字符宽度估算行高。新机器需先启动过一次游戏生成互操作程序集才能编译。

## 0.4.2 存档目录修复（已部署，待下次启动验证）

`sr2_saves` 原来把存档目录硬编码成 `%USERPROFILE%\AppData\LocalLow\Monomi Park\Slime Rancher 2`，本机实际是 `LocalLow\MonomiPark\SlimeRancher2`（Unity persistentDataPath = LocalLow\<company>\<product>，以游戏 `SlimeRancher2_Data\app.info` 为准），导致 `sr2_saves` 恒返回 0 个存档、`sr2_health` 恒报 `save_dir_exists=false`。现在改为按 app.info 推导 + 两条兜底候选（`MonomiPark\SlimeRancher2`、`Monomi Park\Slime Rancher 2`），取第一个存在者。

- 部署：2026-11-21 01:44，`dist\SlimeRancher2Bridge.dll`（41984 B，SHA256 `C917A7B4…`），已校验与 Dist 一致
- 回滚：旧版 0.4.1 备份在 `backups\SlimeRancher2Bridge-0.4.1.dll`
- 验证预期：进档后 `sr2_health.save_dir_exists=true`、`saves_count=5`，`sr2_saves` 能列出 `Steam\<steamid>\*.sav`

## 0.4.0 结晶市场（已构建，待游戏内验证）

进档后调用只读工具 `sr2_market`，或运行 `python -B tool/bridge_cli.py call sr2_market`。每行的 `price` 是游戏返回的当前单价，`change` 是带正负号的涨跌额；两者均为新币／颗。另列出游戏配置的基础价和完全饱和阈值。休市时检查 `market_closed`；单个品种缺报价时保留该行并返回空值。桥接 0.4.0 共 9 项工具。

本机 1.3.0 元数据已确认报价来自 `PlortEconomyDirector.TryGetMarketValues(id, out currentValue, out delta)`，品种来自 `PlortEconomySettings.PlortsTable.Plorts`。对照估值和历史样本可查看[结晶手册](docs/结晶/README.md)。

## 0.3.0 坐标传送与现实时间（已构建，待游戏内验证）

进档后调用 `sr2_player_position` 获取当前位置。向 `sr2_teleport` 传入目标的 `x`、`y`、`z` 即可预览；确认后使用同一组坐标，加上 `dry_run=false` 和新的 `request_id` 执行。重试同一次执行时复用该 ID。目标应是当前已加载区域中已知可站立的位置。执行结果含起点、目标、即时位置与 `reached`；移动后的物理状态可用位置工具再次读取。

从 0.4.1 起，插件在左上角 FPS 下方以 28 号白字显示北京时间，例如 `11/21/ 00:51:50.37`，末两位为百分秒。查询工具 `sr2_real_time` 返回带 UTC+08:00 偏移的日期时间与 Unix 时间戳。

## 0.2.0 导航工具（已构建，待游戏内验证）

桥接新增两个只读 MCP 工具。用 `sr2_player_position` 查玩家的世界坐标和朝向，用 `sr2_minimap` 查 SlimeMap 是否已加载、版本及已保存的显示模式和菜单键。两个工具共用游戏主线程，查询玩家位置可以独立于 SlimeMap 工作。

小地图采用适配本机游戏 1.3.0 的 SlimeMap 1.0.2。退出游戏后，运行下方的桥接部署命令；另将项目 `dist/SlimeMap-1.0.2.zip` 中的 `SlimeMap.dll` 放入游戏的 `Mods` 根目录。通过 Steam 启动前确认 MelonLoader 已恢复启用。进档按 **F7 → Terrain** 显示地形，或选 **Radar** 显示雷达。MCP 客户端重载工具表后共可发现 6 项工具。[作者发布说明和校验值](https://github.com/slitvin12/SlimeMap/releases/tag/v1.0.2)

## 快速开始

```powershell
# 1) 装 MelonLoader（预演 -> 真装）
& E:\SlimeRancher2MCP\tool\install_melonloader.ps1
& E:\SlimeRancher2MCP\tool\install_melonloader.ps1 -Apply

# 2) 编插件并部署（游戏必须退出）
& E:\SlimeRancher2MCP\plugin\SlimeRancher2Bridge\build.ps1 -Deploy

# 3) 通过 Steam 启动游戏，然后验证
python -B E:\SlimeRancher2MCP\tool\bridge_cli.py health
python -B E:\SlimeRancher2MCP\tool\bridge_cli.py call sr2_gameinfo
```

详细步骤见 [docs/INSTALL.md](docs/INSTALL.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `docs/START_HERE.md` | **新会话先读这个**（入口：现状、规则、验证） |
| `docs/RESEARCH.md` | 可行性调研：SR2 vs SR1 差异、候选方案对比、来源 |
| `docs/ARCHITECTURE.md` | 架构、协议、线程模型、加工具的方法 |
| `docs/INSTALL.md` | 安装/部署/回滚/排障 |
| `docs/ROADMAP.md` | P0→P4 阶段计划与工具清单 |
| `docs/DSH_MCP_ENTRY.yml` | 接入 DSH 的 MCP 配置片段 |
| `mcp/tools.json` | **工具定义的唯一清单** |
| `plugin/SlimeRancher2Bridge/` | 游戏内 C# 插件（MelonLoader / net6.0） |
| `tool/bridge_smoketest/` | 不依赖游戏的 HTTP 协议自测 |
| `toolchain/` | 项目内 .NET SDK 6 + MelonLoader 安装包缓存（勿外传） |

## 铁规则（与 1 代相同）

1. 不擅自启动/关闭游戏；只通过 Steam 启动；
2. 不擅自写存档：写工具默认 `dry_run=true`，执行需明确同意 + `request_id`；
3. 写前先读、写后回读；
4. 不随便传送玩家；
5. `config/bridge.json` 的 token 是凭据，不写进文档/日志/提交。








