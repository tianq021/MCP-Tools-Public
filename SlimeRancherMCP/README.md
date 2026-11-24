# 史莱姆牧场 MCP（Slime Rancher MCP）

> **先读：[docs/START_HERE.md](docs/START_HERE.md)** —— 项目入口说明（游戏简介/场所/物价/史莱姆特征、工具使用规范、安全红线、文件地图）。每个新会话先读这一份。
>
> 项目路径：`E:\SlimeRancherMCP`
> 状态：**v0.9.0，共 73 个 MCP 工具**（45 个查询 + 28 个操作），与游戏内注册数一致；dll 于 **2026-11-19 22:49** 重建部署，另含两个小蜜蜂 Harmony 补丁（取消每网络数量上限、电池续航 28 → 84 游戏小时）。当前主线是 **Steam 成就（39/57，见 [docs/ACHIEVEMENTS.md](docs/ACHIEVEMENTS.md)）**。MCP 会话需重载才能看到最新工具。覆盖市场、库存、委托、地块、科技设备、钻井、小蜜蜂、探索与备份，写操作默认预演；仓储查询默认只列已解锁槽位，并可按物品逐格检查能否存入。
> 交接文档：[docs/HANDOVER.md](docs/HANDOVER.md)（阶段记录、逆向结论、API 地图、踩坑；§14 通道设计，§15 工具和验证进展）
> MCP 使用说明：[mcp/README.md](mcp/README.md)（接入、工具目录及操作示例）
> 目标：让 AI（DSH）通过 MCP 直接读写《史莱姆牧场》内部状态，完成「远程购买 / 远程交付 / 牧场管家」自动化。

> ⚠️ 本文 §2、§3、§6 中"关键内部符号"的结论来自字符串命中，**已被反编译证伪/修正**（例如不存在 `PlortMarket`/`Corral`/`AutoFeeder`/`GoldSlime` 类）。以 [docs/HANDOVER.md](docs/HANDOVER.md) §6「已验证的游戏机制」为准。

---

## 1. 目标拆解

| 需求 | 实际含义 | 需要的能力 |
| --- | --- | --- |
| 远距离购买 | 人不在商店/终端旁，也能买升级、买牧场、买俱乐部/商店物品 | 调用商店 `Purchase` 逻辑 + 扣款 + 发货，不依赖角色位置 |
| 远程交付（Range Exchange） | 不用跑回牧场终端，直接交委托 | 调用 Exchange 交付/结算方法 |
| 管家 | 自动收结晶、补喂食、自动卖、自动交委托、定时汇报 | 读状态 + 周期执行动作 + 规则引擎 |
| 顺带 | 金史莱姆太难找（Hat Trick 成就） | 查找 GoldSlime 实例 / 远程投喂，甚至直接判定掉 3 个金结晶 |

## 2. 本机实测现状

| 项目 | Slime Rancher 1 | Slime Rancher 2 |
| --- | --- | --- |
| Steam appid | 433340 | 1657630 |
| Unity 后端 | **Mono**（`MonoBleedingEdge` + `SlimeRancher_Data\Managed\Assembly-CSharp.dll` 2.9 MB） | **IL2CPP**（`GameAssembly.dll` 86 MB + `il2cpp_data\Metadata\global-metadata.dat` 22 MB） |
| 当前是否装 Mod 加载器 | 无 | 无 |
| 社区生态 | 成熟：**MelonLoader + SRML**（Unity 版本多年未大改，API 稳定） | 较小：**MelonLoader（IL2CPP 分支）**，无 SRML；Early Access，存档 schema 随版本变 |
| 存档 | `%LocalAppData%Low\Monomi Park\Slime Rancher\*.sav`，5 个存档约 270–340 KB | 本机**还没生成存档目录**（2 代 0/27 成就，基本没玩过） |
| 关键内部符号（字符串验证） | `PlortMarket`、`Purchase`(334)、`Currency`、`AddCurrency`、`PlayerState`、`Corral`、`AutoFeeder`、`Feeder`、`GoldSlime`、`LandPlot`、`Vacpack`、`Achievement`(145)、`BuyCorral`、`CreatePurchaseButton`、`CheckAchievement` 都出现 | metadata 里 `PlortMarket`、`RangeExchange`、`Purchase`(347)、`AddCurrency`、`Corral`、`GoldSlime`、`LandPlot` 都在 → 用 il2cpp dump 可定位 |

> 以上只是**字符串命中**，证明类/方法大概率存在；精确签名要用 ILSpy/dnSpy（SR1）或 Il2CppDumper（SR2）再确认。

## 3. 四条技术路线对比

| 路线 | 原理 | 能做「远程购买/管家」吗 | 稳定性 | 工作量 | 风险 |
| --- | --- | --- | --- | --- | --- |
| **A. 游戏内 Mod 桥接（推荐）** | MelonLoader/SRML 插件在游戏进程内运行，直接调用游戏 C# 方法，开本地 HTTP 接口 | ✅ 完全能做（调真实 Purchase/Exchange/Corral/Feeder 逻辑） | 高（走游戏原逻辑） | SR1 2–4 天；SR2 3–6 天 | 需随游戏版本更新；SR2 EA 变动 |
| B. 存档编辑 | 解析 `.sav`（5 个槽位）改金钱/背包/进度 | ⚠️ 只能改静态数据，不能调商店逻辑；改完要重进游戏 | 中（格式可能加密/压缩） | 1–2 天 | 存档损坏风险高；SR2 格式还在变 |
| C. 内存读写（Trainer 式） | 找指针/偏移直接改数值、远程调用函数 | ⚠️ 数值能改；调用复杂方法（Purchase）较难、易崩 | 低（版本一换就废） | 2–5 天 | 崩溃/封档；不如 Mod 稳 |
| D. 桌面 UI 自动化 | 截图 + 键鼠 MCP（现有 input/desktop 工具） | ❌ 只能"替人走路点点点"，第一人称 3D 场景导航极脆弱 | 低 | 1 天出 demo | 不可靠、不是"内部调用" |

**结论：A 为主。** 不需要反编译整个游戏，只要在插件里写：

```csharp
var player = SceneContext.Instance.PlayerState;      // 状态
player.AddCurrency(amount);                          // 加钱（改数值用，购买走商店逻辑）
Shop.Purchase(item, count);                          // 或具体商店类的购买方法（待确认签名）
PlortMarket.Sell(plortType, count);                  // 卖结晶
Exchange.CompleteDelivery(requestId);                // 远程交委托（待确认）
Corral.CollectPlorts(); Feeder.Refill();             // 管家动作
GoldSlime.Feed(item);                                // 远程喂金史莱姆
```

## 4. 推荐架构（三层）

```
┌───────────────────────────┐
│  DSH / MCP client (AI)     │
│  tools: sr_*               │
└─────────────┬─────────────┘
              │ stdio JSON-RPC (MCP)
┌─────────────▼─────────────┐
│  slime_rancher MCP server │  Python，独立进程
│  - 参数校验 / 幂等 request_id│
│  - 存档备份 / 只读默认 / 日志  │
└─────────────┬─────────────┘
              │ 127.0.0.1 HTTP+JSON（带 token）
┌─────────────▼─────────────┐
│  SlimeRancherBridge.dll    │  MelonLoader 插件（游戏进程内）
│  - 主线程调度（Update/协程） │
│  - 读: state/market/corral  │
│  - 写: buy/sell/deliver/... │
└───────────────────────────┘
```

与本机已有的游戏 MCP（after_inc / frostpunk / IFZ）同模式，可复用：
- 共享写租约（同一时间只有一个客户端能写）
- `request_id` 幂等重试（超时只重发同一请求）
- 动作前读 revision、动作后回读校验
- 只读模式默认、写前备份存档

## 5. MCP 工具草案

| 工具 | 作用 | 读/写 |
| --- | --- | --- |
| `sr_status` | 游戏是否运行、版本、桥接版本、存档槽位 | 读 |
| `sr_get_state` | 天数/时间、新币、背包、畜栏、喂食器、交易所、已解锁升级 | 读 |
| `sr_list_shop` | 当前可购买的商店/升级/地块（含价格、是否已购） | 读 |
| `sr_buy` | **远程购买**：`{shop, item, count}` → 走真实 Purchase 逻辑 | 写 |
| `sr_sell_plorts` | 远程卖结晶 `{type, count}` | 写 |
| `sr_deliver_exchange` | **远程交付** Range Exchange 委托 | 写 |
| `sr_corral` | 收结晶 / 补喂食 / 开关自动喂食器 `{plot, action}` | 写 |
| `sr_feed_slime` | 对指定史莱姆投喂（金史莱姆刷 Hat Trick 用） | 写 |
| `sr_find_gold_slime` | 查找附近/全图 Gold Slime 实例与位置 | 读 |
| `sr_teleport` | 传送到坐标（可选，作弊项，默认关） | 写 |
| `sr_autopilot` | **管家**：开关自动收/喂/卖/交委托，设置周期 | 写 |
| `sr_backup_save` | 备份当前存档槽（写前自动调用） | 写 |

管家两种实现：
1. **游戏内挂机逻辑**（推荐）：插件定时协程扫描畜栏/喂食器 → 收结晶 → 卖市场 → 补喂 →（可选）交委托；
2. **MCP 侧调度**：AI 每隔 N 分钟调 `sr_get_state` + `sr_corral` + `sr_sell_plorts`，适合低频、需要判断的策略。

## 6. 「远距离购买」「管家」具体落地

- **商铺远程购买**：找到各商店的 `Purchase`/`CreatePurchaseButton`（SR1 已确认有 `BuyCorral` 等符号），暴露购买入口；人不用走过去，钱和物品正常结算（写前备份存档）。
- **远程交付**：找 Range Exchange 的交付方法（SR1 有 `Exchange` 相关符号 65 处，SR2 metadata 有 `RangeExchange`），从背包扣除请求物品并发奖励。
- **管家**：`Corral`/`Feeder`/`AutoFeeder` 都有符号；定时 `CollectPlorts` + `Refill` + `PlortMarket.Sell` 形成闭环。默认只做"收 + 卖 + 补喂"三件低风险动作。
- **金史莱姆**：`GoldSlime` 存在（SR1 2 处 / SR2 7 处），可做 `find_gold_slime` + 远程 `feed`；是否直接解锁 Hat Trick 需实测。

## 7. 风险与约束

| 风险 | 说明 | 对策 |
| --- | --- | --- |
| 成就/作弊观感 | Mod 一般不会屏蔽 Steam 成就，用桥接拿成就算作弊 | 默认只做"管家/远程购买"；成就类动作要显式确认 |
| 存档损坏 | 写存档/内存可能弄坏槽位 | 写前自动备份 5 个 `.sav`；动作后回读校验 |
| 版本漂移 | SR2 在 Early Access，更新后 il2cpp 偏移/类名可能变 | 固定游戏版本/分支；桥接启动时校验版本号 |
| SR2 联机 | SR2 支持最多 4 人合作（主机状态权威） | 只在主机侧装桥接；联机前所有玩家 mod 集合一致 |
| 官方态度 | Monomi Park 对 mod 宽容但非官方支持 | 只本地用、不分发、不碰付费内容 |
| 多客户端冲突 | DSH/Codex/其他工具同时写 | 复用「写租约 + request_id」机制 |
| 游戏暂停/焦点 | 失焦不一定暂停 | 读写都在游戏主线程执行，不依赖焦点 |

## 8. 目录规划（待创建）

```
E:\SlimeRancherMCP\
├─ README.md              ← 本说明
├─ docs\                  ← 逆向笔记、接口签名、测试记录
│   ├─ sr1_symbols.md
│   └─ sr2_il2cpp_notes.md
├─ plugin\                ← MelonLoader 插件（SlimeRancherBridge，C#）
│   └─ SlimeRancherBridge\
├─ mcp\                   ← Python MCP server（slime_rancher）
│   ├─ server.py
│   └─ pyproject.toml
├─ tool\                  ← 一次性脚本（存档备份/对比/测试）
└─ backups\               ← 自动备份的 .sav
```

## 9. 工作量与里程碑

| 阶段 | 内容 | 预计 |
| --- | --- | --- |
| 0 · 逆向确认 | ILSpy/dnSpy 打开 SR1 `Assembly-CSharp.dll`，确认 `PlayerState`/`PlortMarket`/商店/Exchange/Corral/Feeder/GoldSlime 真实签名 | 0.5–1 天 |
| 1 · 只读探针 | 装 MelonLoader+SRML，最小插件输出天数/新币/背包/畜栏 | 0.5–1 天 |
| 2 · 写动作 | 卖结晶、收畜栏、补喂、远程购买、交委托 | 1–2 天 |
| 3 · MCP 封装 | `slime_rancher` MCP server（stdio），工具表 + 租约 + 备份 | 0.5–1 天 |
| 4 · 管家模式 | 自动收/喂/卖循环、定时汇报、异常停止 | 1–2 天 |
| **SR1 MVP** | 阶段 1–3 | **2–4 天** |
| **SR2 追加** | il2cpp dump + 插件适配 + EA 版本校验 | **3–6 天** |

## 10. 替代方案（降级）

1. **现成 QoL/作弊 Mod**：Nexus 上已有 Slime Rancher cheat menu / QoL 类 mod（SR1 生态成熟），可先验证覆盖度。
2. **存档编辑器**：SR1 有社区存档编辑工具，可离线改钱/物品，但做不了"买东西"这种需要游戏逻辑的动作。
3. **桌面自动化**：现有 input/desktop MCP 能截图+键鼠，但第一人称 3D 导航不可靠。

## 11. 下一步

1. 先做 **SR1 只读探针**（装 MelonLoader，确认能读到 `PlayerState`/`Corral`/`PlortMarket` 真实对象和数值）——最便宜、最能验证可行性；
2. 探针跑通后按工具表逐步加写动作，优先 **收/喂/卖管家** + **远程购买**；
3. SR2 等 1 代桥接跑通、且确实开始玩 2 代时再移植（本机 2 代还没存档）。

> 外部资料：SR1 的 MelonLoader + SRML 生态见 [OpenMods Slime Rancher 页](https://openmods.net/games/slime-rancher)；SR2 的 IL2CPP 加载方式见 [SR2 入门指南](https://openmods.net/guides/getting-started-slime-rancher-2)。
