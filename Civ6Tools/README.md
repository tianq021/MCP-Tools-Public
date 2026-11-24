# Civ6 AI 控制环境（初步搭建）

搭建日期：2026-10-27
用途：让 DSH/LLM 通过 Civ6 官方 FireTuner 调试接口读写游戏状态并下达指令。

---

## 1. 已完成

| 项目 | 状态 | 说明 |
|---|---|---|
| civ6-mcp 源码 | ✅ | `E:\Civ6\civ6-mcp`，commit `dd20190`，MIT |
| Python 环境 | ✅ | `.venv` = Python 3.12.6，依赖由 `uv sync` 按 `uv.lock` 安装 |
| uv | ✅ | `C:\Python312\Scripts\uv.exe`（0.13.6，pip 装） |
| MCP 握手 | ✅ | 协议 `2024-11-05`，服务端自报 “Civilization VI v1.26.0” |
| 工具数量 | ✅ | **77 个**（上游 76 + 本次新增 `get_civ_abilities`）|
| 文明身份查询 | ✅ | `get_civ_abilities` — 特性原文 + 独特单位/区域/建筑/改良 |
| 离线验证 | ✅ | 25 项断言全通过，Lua 代码块真编译过 |
| 游戏侧 Tuner | ✅ | `AppOptions.txt` 的 `EnableTuner` 已由 0 改为 1 |
| 全屏模式 | ✅ | `FullScreen 2`（无边框窗口），不影响调试接口 |
| FireTuner 连接 | ✅ | 实机握手成功，186 个 Lua 状态（GameCore=33 / InGame=181）|
| 读 / 写实测 | ✅ | 查到中国·永乐皇帝特性；勇士真实移动并移回 |
| DSH 注册 | ✅ | 已写入 web profile，77 个工具以 `mcp__civ6__*` 可用 |

## 2. 踩到的坑：中文 Windows 的 GBK 默认编码

`src/civ_mcp/version.py` 用 `pathlib.read_text()` 读 `pyproject.toml`，没有指定编码。
中文 Windows 的默认 locale 是 cp936，碰到文件里的 `—`（em dash）直接抛
`UnicodeDecodeError`，导致 **import 阶段就崩**，整个包不可用。

两处修法，都已落地：

1. **点修**：`version.py` 第 16 行 → `read_text(encoding="utf-8")`
   （这是对第三方源码的改动，`git pull` 可能有冲突，记在这里）
2. **系统修**：启动包装器强制 UTF-8 模式
   ```
   set PYTHONUTF8=1
   set PYTHONIOENCODING=utf-8
   ```

> 注意：直接 `uv run civ-mcp` 而没设 `PYTHONUTF8=1`，仍可能在其他模块
> （`diary.py` / `telemetry.py` 的隐式编码读写）踩坑。**一律走包装器或带 env 启动。**

## 3. 启动方式

手动启动（stdio JSON-RPC，正常由 MCP 客户端拉起，不需要手工跑）：

```
E:\Civ6\脚本\start-civ-mcp.cmd
```

已验证的探测脚本（不需要游戏运行）：

```
C:\Python312\python.exe E:\Civ6\脚本\probe_mcp.py
```

期望输出：`"handshake": "OK"`、`"tool_count": 77`。

## 4. 架构与端口

```
MCP 客户端 (DSH / Claude / Codex)
   │  stdio JSON-RPC
   ▼
civ-mcp (Python)
   │  TCP 127.0.0.1:4318
   ▼
文明6  ── 两个 Lua VM ──
   ├─ GameCore：只读，Players[] / GameInfo / Map / Game
   └─ InGame  ：可写，UnitManager / CityManager / UI  ← 真正的“操作”
```

- **4318**：FireTuner 调试端口，游戏是服务端。游戏没开就是 connection refused。
- **8000**：civ6-mcp 自带的 Web 看板端口。⚠️ **这台机器上 8000 用不了**——Windows 把
  `7942–8041` 划进了保留端口区间（`netsh int ipv4 show excludedportrange protocol=tcp`
  可查），绑定会以 `winerror 10013` 失败。已改用 `CIV_MCP_WEB_PORT=8099`。
  看板绑定失败现在只降级为警告，不再拖垮 MCP 服务端（详见第 9 节）。

## 5. 接入 DSH（已完成）

配置文件：`F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml`
备份：`cordis.patch.yml.before-civ6-20261027-140847.bak`

追加的条目（`- insert:` 顶层数组一项，与既有 7 项并列，共 8 项）：

```yaml
- insert:
    - id: mcp-civ6
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: civ6
        transport: stdio
        command: 'E:\Civ6\civ6-mcp\.venv\Scripts\civ-mcp.exe'
        cwd: 'E:\Civ6\civ6-mcp'
        toolCallTimeoutMs: 180000
        failOnStartupError: false
        env:
          PYTHONUTF8: '1'
          PYTHONIOENCODING: 'utf-8'
          CIV_MCP_WEB_PORT: '8099'
```

**改完立即生效**，不需要重启 DSH：运行时上下文里出现了 `civ6` 服务，
77 个工具以 `mcp__civ6__*` 形式可用。

### 两个使用上的坑

1. **游戏只允许一个 tuner 连接。** 现在这个连接由 DSH 常驻持有，
   再跑 `live_*.py` 之类的直连脚本会连不上。以后一律用 MCP 工具，不要用脚本。
2. **别用 `Get-NetTCPConnection -State Listen` 判断连接死活。** 我们踩过一次：
   它报“4318 无监听”，但实际上是一个 **ESTABLISHED 长连接**，工具调用完全正常。
   判断依据应该是**真的调一次查询看有没有数据**，不是看端口表。
3. 同理，**不要**在 DSH 已经占用连接时跑 `probe_mcp.py`——它会另起一个实例并尝试
   第二次握手。FireTuner 握手失败后经常卡死，只能重启游戏。

## 6. 下一步（需要人做的两件事）

1. 启动文明6 → 随便载入一局（建议用一个不在意的存档）
2. 确认设置里 “Tuner（会禁用成就）” 是开着的（已替你写进 AppOptions.txt）

然后验证顺序，一步一确认，别跳：

1. `test_connection.py` 握手成功
2. `get_game_overview` 能读到回合数、玩家、地图
3. **`get_civ_abilities` 能吐出「文明 + 领袖 + 特性原文 + 独特单位」** ← 新工具首测
   （重点看 `DIAG TRAITS=` 是否 > 0，以及特性描述是不是真文本而不是空）
4. `get_units` 列出单位
5. **`unit_action` 移动一个侦察兵，看见它真的动了** ← 分水岭
6. 再谈 `end_turn` 和整回合自动化

## 7. 已知副作用 / 边界

- 开 Tuner **永久禁用该存档的成就**。想正常刷成就时把 `EnableTuner` 改回 0。
- 只能单机。联机时两个 Lua VM 不同步。
- SDK 里的图形工具 `FireTuner2.exe`（`E:\SteamLibrary\...\Civilization VI SDK\FireTuner\`）
  **不能同时开着**，游戏只允许一个 tuner 连接。
- 不要从 WSL 里连（网络桥接不可靠，握手失败后 tuner 会卡死，只能重启游戏）。
- 第三方 MIT 项目，权限等同于“能操作你的游戏进程”。要审代码就审 `src/civ_mcp/lua/`。

## 8. 回滚

| 改动 | 回滚方式 |
|---|---|
| `EnableTuner 1` | 改回 0，或还原 `AppOptions.txt.bak-before-tuner-20261027-124319` |
| `version.py` 一行改动 | `git -C E:\Civ6\civ6-mcp checkout src/civ_mcp/version.py` |
| 整个环境 | 删除 `E:\Civ6` 即可（无系统级安装） |

---

## 9. 本次改动（相对上游 v1.1.11，commit dd20190）

### 9.1 新增 `get_civ_abilities` 工具

**为什么**：上游所有查询都没碰过 `GameInfo.Traits` / `CivilizationTraits` /
`LeaderTraits`。`get_game_overview` 只返回“你是拜占庭 / 巴西尔二世”，
不说这意味什么。结果就是 devlog 里反复出现的文明身份盲区：
第 6 局甘地 9,599 信仰零支出、第 10 局旃陀罗笈多 431 回合零圣地、第 4 局拜占庭
Taxis 永久锁死。

**怎么做的**（5 个文件）：

| 文件 | 改动 |
|---|---|
| `lua/civ.py` | 新增：Lua 查询构造器 + 解析器（约 300 行）|
| `lua/models.py` | 新增 `CivTrait` / `UniqueItem` / `CivAbilities` |
| `lua/__init__.py` | 导出新符号 |
| `game_state.py` | 新增 `get_civ_abilities()`（InGame 上下文）|
| `narrate.py` | 新增 `narrate_civ_abilities()` |
| `server.py` | 注册 `@mcp.tool` |

**查询做了什么**：`CivilizationTraits` + `LeaderTraits` → `GameInfo.Traits` 取特性
名称与原文描述；再拿这些 TraitType 去 `Units` / `Districts` / `Buildings` /
`Improvements` 的 `TraitType` 列反查独特单位/区域/建筑/改良，附带战斗力、解锁
科技或市政、造价。

**防御性设计**：每一处可选表/列访问都包在 `pcall` 里，查询末尾输出 `DIAG|` 计数器。
这样运行时若某张表读不到，表现为计数器为 0 + 播报里的显式 WARNING，
而不是“空列表看起来一切正常”。**宁可报错，不可假装成功。**

### 9.2 修：Web 看板绑定失败会杀掉整个 MCP 服务端

上游 `server.py` 里 `uvicorn.Server.serve()` 一旦绑不上端口就 `sys.exit(1)`，
把 stdio 主通道一起带走。实测这台机器必然触发（10013）。

改法：绑定失败降级为 WARNING 并继续提供工具；端口/地址可用
`CIV_MCP_WEB_PORT` / `CIV_MCP_WEB_HOST` 覆盖，设为 `0` 则完全禁用看板。

> 教训通用：**仪表盘永远不该有权杀掉控制通道。**

### 9.3 依赖

- `lupa` 已装进 `.venv`，仅用于**离线校验生成的 Lua 能否编译**（`verify_civ_abilities.py`）。
  它不在 `uv.lock` 里，下次 `uv sync` 会被清掉；要用就先重装：
  `C:\Python312\Scripts\uv.exe pip install lupa`
- `uv.lock` 被 `uv sync` 更新（版本号微调），属正常。

### 9.4 验证记录

```
verify_civ_abilities.py  →  25/25 PASS
  Lua chunk compiles (5115 chars, 真 Lua VM 编译通过)
  parse: 文明/领袖/2 条特性/4 个独特项/战斗力 48/解锁科技/造价 220
  zero-trait 路径会显式 WARNING

probe_mcp.py             →  handshake OK, tool_count 77
  has_civ_abilities: true
  看板：CIV_MCP_WEB_PORT=8099 → Uvicorn running on http://0.0.0.0:8099
```

**之前担心的两点，实测都有了答案：**

| 疑问 | 实测结果 |
|---|---|
| `Traits[t].Description` 能取到本地化文本吗 | ✅ 能，返回中文原文 |
| `Units.TraitType` 覆盖独特单位吗 | ✅ 能，61 个单位带 TraitType，命中我方 1 个 |

### 9.5 实机测试记录（2026-10-27，国王难度 / 中国·永乐皇帝 / 回合 1）

```
live_civ_test.py    → 4/4 PASS
  CIV|CIVILIZATION_CHINA|中国|LEADER_YONGLE|永乐皇帝
  TRAITS=4  TRAITS_INTERNAL_SKIPPED=2  UNITS_WITH_TRAITTYPE=61
  独特单位 虎蹲炮 (CS 30 / RS 50 / 需机械 / 造价 140)
  独特改良 长城 (需砌砖)
  特性原文：朝代更替、里甲

live_state_test.py  → PASS
  Turn 1 | 中国(永乐皇帝) | 国王 | 4 单位(1 开拓者 + 3 勇士)
  Explored 10% (41/405) | Victory: Science, Domination

live_move_test.py   → PASS ← 决定性证据
  勇士 idx=1 (39,16) -> (38,15)，移动力 3.0 -> 2.0
  引擎回执：MOVING_TO|38,15|from:39,16|now_at:38,15|(moved dx:-1 dy:-1)
```

### 9.6 实测才暴露的两个问题（已修）

1. **特性文本里带游戏内标记** — 原文是 `[ICON_TechBoosted] 尤里卡和
   [ICON_CivicBoosted] 鼓舞…`，直接给模型会变成噪音。已在 `clean()` 里剥掉
   `[ICON_*]` / `[Icon_*]`。
2. **内部伪特性混入** — `TRAIT_GOLD_PURCHASE`、`TRAIT_LEADER_SCIENCE_MAJOR_CIV`
   这类 AI 行为管道既无名称也无描述，却会被当成“能力”让模型去推理。
   现在凡名字与描述皆空的特性直接丢弃，并计入 `DIAG|TRAITS_INTERNAL_SKIPPED`。

> 这两条都是**离线测不出来的**——只有真游戏数据才会暴露。`DIAG` 计数器就是为此
> 埋的，这次直接发挥了作用（6 条 → 剔除 2 条 → 4 条真实能力）。

---

## 10. 经验文档与工具索引（2026-10-27 第二局后补）

三局对局：局1 T57（未打完）、局2 T37 **统治胜利**、局3 T88 **文化胜利**。经验已整理成独立文档，新接手请按顺序读（2026-10-28 已按「对局 / 文档 / 脚本 / 配置」分类归档，见第 11 节）：

> 2026-10-28 追加 **局4（中国永乐 / 不朽 / SmartAI 强化15档 / 随机科技市政）**：T15 前见 `开局复盘_T1-T15.md`，T59 接手后 **T68 攻占波斯原始首都达成统治胜利**，见 `对局\局4_中国永乐_强化15_开局_20261028\战报_统治胜利_T68.md`（含弩手攻城硬数据、开放边境前置战术、随机模式阻塞清理法）。

| 文档 | 内容 |
|---|---|
| `文档\文明6AI经验总结.md` | **总纲**：战略/战术/工具/环境四层经验 + 报错对照表 + 速查卡 |
| `文档\回合清单.md` | 每回合 4 轮模板 + 预检规则表（含第二局补充：攻城数值、ZOC、升级、收尾） |
| `对局\局2_统治胜利_T37\战报_T19-T37_统治胜利.md` | 第二局全过程 + 4 个关键机制（平民挡城、异步读数、ID 变化、假阻塞） |
| `对局\局3_文化胜利_T88\战报_第三局_文化胜利_T88.md` | 第三局（11 城 / 112 人口 / 未败一城）文化胜利全过程 |
| `对局\局1_中国永乐_国王_T57\AI游玩经验_中国永乐_T57.md` | 第一局细节（地图 x 轴环绕、外交弹窗吞指令等） |
| `文档\模组游玩指南.md` | 本机 25 个模组的能力与注意事项 |

### 10.1 客户端脚本（`E:\Civ6\脚本\`）

| 脚本 | 作用 |
|---|---|
| `脚本\civc.py` | HTTP 桥（8766）的 UTF-8 安全批量客户端，`-j plan.json` |
| `脚本\civlua.py` | 执行 Lua 文件（`gamecore` 只读 / `ingame` 可写） |
| `脚本\civloop.py` | 例行回合驱动器（自动补生产/科技/市政、走位、清阻塞、end_turn 兜底） |
| `脚本\civadv.py` | 单回合推进 + 卡死自救 |
| `脚本\play_mcp.py` | 本地 HTTP 桥（8766）：一条常驻 stdio 会话 + **事件哨兵后台轮询**，日志写 `logs\play_mcp_server.log` |
| `脚本\civwatch.py` | **事件哨兵 CLI**：`--status` / `--inbox` / `--wait`，靠退出码唤醒 agent（见第 12 节） |
| `脚本\watch_core.py` | 哨兵核心：探针解析 + 事件差分/分级（`selftest_watch.py` 离线自测） |

### 10.2 Lua 模板（`E:\Civ6\lua\`）

`turn.lua`（真实回合号）、`recon_enemy.lua`（敌城防御+射击位）、`volley_city.lua`（对城市格齐射，绕过平民挡城）、`dismiss_blockers.lua`（清残留阻塞通知）、`force_endturn.lua`（绕过 MCP 结束回合）、`builder_refit.lua`（模组建造者整备）。

> 历史临时输出在 `E:\Civ6\logs\`，历史临时脚本在 `E:\Civ6\lua\_session\`，仅作参考，可随时清理。

---

## 11. 目录结构（2026-10-28 分类整理）

原先 44 个文件全堆在根目录，现按「对局 / 文档 / 脚本 / 配置 / 日志」归档；`civ6-mcp\`、`lua\`、`logs\` 保持原名不动（DSH 配置、脚本与文档里已有大量路径引用）。

```
E:\Civ6\
├── README.md                      ← 唯一保留在根目录的索引文件（本文）
├── civ6-mcp\                      ← MCP 服务端源码（DSH 配置引用 .venv\Scripts\civ-mcp.exe，禁止挪动）
├── lua\                           ← 22 个 Lua 模板 + _session\（历史临时脚本）
├── logs\                          ← 全部历史运行输出（532+ 文件；play_mcp_server.log / play_mcp_calls.jsonl 也在这里）
├── 文明百科\                      ← civilopedia.net 全量离线副本（18 分区 1923 条 + 本地浏览页面，见第 13 节）
├── 对局\                          ← 每个对局一个文件夹
│   ├── 局1_中国永乐_国王_T57\      AI游玩经验_中国永乐_T57.md
│   ├── 局2_统治胜利_T37\           战报_T19-T37_统治胜利.md、DSH_协作指令_T27.md、DSH_协作回报_T29.md
│   ├── 局3_文化胜利_T88\           战报_第三局_文化胜利_T88.md
│   └── 局4_中国永乐_强化15_开局_20261028\  开局复盘_T1-T15.md、战报_统治胜利_T68.md、操作记录.jsonl
├── 文档\                          ← 跨对局的通用经验/规则
│   ├── 文明6AI经验总结.md（总纲）、回合清单.md、交战规则.md、单位分工.md
│   ├── 事件哨兵.md（外交/阻塞/弹窗主动通知的机制与验证清单）
│   ├── 模组游玩指南.md、学习文档.txt、给AI游玩的建议.txt、测试遇到的问题.txt
├── 脚本\                          ← 全部客户端脚本（python / powershell / cmd）
│   ├── civc.py、civlua.py、civloop.py、civadv.py、play_mcp.py
│   ├── civwatch.py、watch_core.py、civwatch_probe.lua、selftest_watch.py（事件哨兵）
│   ├── advance_civ.py、auto_combat.py、peace_driver.py、sector_dsh.py
│   ├── probe_mcp.py、live_*_test.py、verify_civ_abilities.py、dsh_contact.py
│   ├── civ.ps1、civq.ps1、start-civ-mcp.cmd
│   └── requests\                  ← dsh_contact.py 的请求样例 JSON
├── 配置\                          ← mcp-tools.json、mcp-registration.yml、dsh-civ6-before-codex.yml、desktop-action.json
└── __pycache__\                   ← 旧位置残留的字节码缓存（可删，Python 会在 脚本\ 下自动重建）
```

**迁移时同步修改过的地方（以后别再改回去）：**

1. `脚本\play_mcp.py`：`ROOT` 改为 `Path(__file__).parent.parent`（即 `E:\Civ6`），`play_mcp_server.log` / `play_mcp_calls.jsonl` 写入 `logs\`。
2. `脚本\start-civ-mcp.cmd`：exe 路径改为 `%~dp0..\civ6-mcp\...`（本文件已下移一层）。
3. `脚本\civq.ps1`：dot-source 改为 `$PSScriptRoot\civ.ps1`，默认输出改为 `E:\Civ6\logs\_civ_out.txt`。
4. 所有文档/脚本里的命令示例统一写成 `python 脚本\civc.py ...` 形式；`lua\` 与 `logs\` 名字未变，原有 `lua/xxx.lua` 用法照旧有效。
5. `auto_combat.py` / `peace_driver.py` / `sector_dsh.py` 里的 `E:\Civ6\logs\*.txt`、`E:\Civ6\lua\*.lua` 绝对路径本来就指向这两个目录，未改动。
6. DSH 的 `cordis.patch.yml` 只引用 `E:\Civ6\civ6-mcp\...`，未受本次整理影响；桥已用新路径重启并实测 `/tools` 返回 77 个工具。

---

## 12. 事件哨兵：外交 / 阻塞 / 弹窗的主动通知（2026-10-28）

**问题**：外交会话、交易报价、EndTurn 阻塞、弹窗都是游戏里的**持久状态**，而 MCP 是调用-返回、
DSH 的 MCP 客户端也不消费 server notification——不主动查就漏。漏外交=回合被吞，漏交易=白丢钱，
漏阻塞=`end_turn` 永远 BLOCKED。

**解法**：桥（`脚本\play_mcp.py`）里内置一个后台哨兵，每 1.5s 用一条 InGame Lua 探针
（`脚本\civwatch_probe.lua`）抓状态，`脚本\watch_core.py` 差分成去重后的事件并分级；
`脚本\civwatch.py` 从桥的 `/inbox` `/wait` 取事件。**推送靠 DSH 后台 job 的 wakeup 通知**
（`dsh-tool-jobs` 的 `completionDelivery` 默认 `wakeup`：job 结束会唤醒空闲 agent）。

```
游戏 ──run_lua──▶ 8766 桥（1.5s 轮询 + EventBox）──/inbox /wait──▶ civwatch.py ──后台 job──▶ 唤醒 agent
```

| 级别 | 事件 | 处理 |
|---|---|---|
| P0 | 外交会话、贸易报价、宣战 | 立刻 `get_pending_diplomacy` / `get_pending_trades` / 查战况 |
| P1 | EndTurn 阻塞、游戏离线 | 下一动作前补齐 |
| P2 | 新通知、普通弹窗、回合开始 | 攒到回合开始一次性看 |

**每回合固定动作**（详见 `文档\回合清单.md` 第 11 节）：

```powershell
python 脚本\civwatch.py --inbox                    # 回合开始：代替 R1 里的四条查询
python 脚本\civwatch.py --wait 600 --min P0        # end_turn 后：后台 job 守夜（run_in_background）
```

退出码：`0` 有事件（唤醒）、`75` 守夜超时（不唤醒）、`2` 桥/游戏不在线。

完整机制、去重规则、边界与上线验证清单：**`文档\事件哨兵.md`**。
离线自测：`E:\Civ6\civ6-mcp\.venv\Scripts\python.exe 脚本\selftest_watch.py`（31 项断言，无需游戏）。

---

## 13. 文明百科离线版（2026-10-28）

把 [civilopedia.net](https://www.civilopedia.net/zh-CN/gathering-storm/concepts/intro/)
整站抓到本地：**风云变幻规则集 / 简体中文 / 1923 条 / 18 个分区**，两种打开方式：

1. **`文明百科\index.html`（默认）** → 跳转 `原站镜像\`，**和官网一模一样**：原 HTML + 原 CSS + 原图标，
   分类图标、左侧条目树、正文链接、搜索框（改用本地数据）都能离线用。
2. **`文明百科\快速检索.html`** → 自绘暗色单页：全文搜索 + 分区/分组筛选 + 深链，3.5MB 秒开。

```
文明百科\
├── index.html       入口 → 原站镜像
├── 快速检索.html     暗色快速检索单页
├── data.js          全量数据（搜索用）
├── 原站镜像\        官网原样克隆（1945 个页面 + 1332 个静态资源，165 MB）
│   ├── zh-CN\gathering-storm\<分区>\<条目>\index.html
│   ├── static\{assets,images}\   原站 CSS/JS/图标（本地化）
│   └── search-shim.js / _serve.py  离线搜索补丁 / 可选本地服务器
├── md\              1923 个 Markdown（按分区）
├── images\          1305 张图标
├── _data\           pages.jsonl（结构化数据）+ sections.json
├── _scrape.py      抓正文数据（断点续抓、支持 --ruleset）
├── _mirror.py      抓整站镜像（路径重写 + 资源本地化）
├── _render.py      重建快速检索页 / data.js / md
└── _verify.py      镜像链接抽查
```

| 分区 | 条 | 分区 | 条 | 分区 | 条 |
|---|---:|---|---:|---|---:|
| 概念 | 147 | 文明/领袖 | 129 | 城邦 | 49 |
| 区域 | 36 | 建筑 | 86 | 奇观和项目 | 137 |
| 单位 | 162 | 单位强化 | 151 | 伟人 | 213 |
| 科技 | 78 | 市政 | 62 | 政体和政策 | 171 |
| 宗教 | 72 | 地形和地貌 | 65 | 资源 | 56 |
| 改良设施和路线 | 69 | 总督 | 77 | 历史时刻 | 163 |

细节见 **`文明百科\README.md`**（两种入口、重建命令、已知问题：部分图标原站本身就 404、其他语言未镜像等）。

---

## 14. 2026-10-28 事故：自动恢复把健康的游戏杀掉（已修）

**现象**：打开文明6 后 20–40 秒内游戏自己消失，连续两次。看起来像"agent 关了游戏"。

**真实原因（不是人手、也不是 agent 操作，是 civ6-mcp 自己的自动恢复逻辑）**：

1. `脚本\play_mcp.py` 的事件哨兵每 1.5s 跑一次 `run_lua(context="ingame")` 探针。
   **主菜单没有 InGame 状态**，探针每次抛 `ConnectionError`。
2. `server.py` 的 `_logged()` 把连续连接失败计数，**≥5 次**就触发
   `CONNECTION RECOVERY → game_launcher.restart_and_load()`，而它的**第一步是
   `taskkill /IM CivilizationVI*.exe /F`**。
   于是"游戏刚打开、还没载入存档"= 必被杀。
3. 它自己的重启还**永远失败**：`game_launcher.is_game_running()` 用
   `subprocess.run(["tasklist",...], text=True)`；本机 `PYTHONUTF8=1` 强制按 UTF-8
   解码 tasklist 的 GBK 输出 → reader 线程 `UnicodeDecodeError` → `r.stdout` 为
   `None` → `AttributeError: 'NoneType' object has no attribute 'lower'`。
   （和 README 第 2 节的 GBK 坑同源，只是这次出现在子进程解码上。）
4. 哨兵无限重试，所以约每 54s 循环一次：只要游戏在，就有机会被杀。

日志证据：`logs\play_mcp_server.log` 里 `Killed Civ 6` 只有两条，时间戳
**16:37:55 / 16:38:51**，紧随其后每条都是 `CONNECTION RECOVERY: restart failed`。

**修复（`civ6-mcp\src\`，两处）**：

| 文件 | 改动 |
|---|---|
| `civ_mcp\server.py` | 自动重启改为 **opt-in**，默认关闭；连续失败 5 次只记 `CONNECTION RECOVERY SKIPPED` 警告，**不再 kill**。要恢复上游行为设 `CIV_MCP_AUTO_RESTART=1` |
| `civ_mcp\game_launcher.py` | `is_game_running()` 去掉 `text=True`，改 `(r.stdout or b"").decode("utf-8", "replace")`；匹配的 IMAGENAME 是 ASCII，有损解码无影响 |

**验证（2026-10-28 16:45）**：停桥 → 打补丁 → `py_compile` 通过 → 重启桥（78 工具）
→ 通过 Steam 启动游戏 → **主菜单停留 80s+**：进程存活、日志出现 2 次
`CONNECTION RECOVERY SKIPPED`、**0 次 `Killed Civ 6`**。修复前同样的场景必被杀。

**经验**：
- 自动 kill 类逻辑的触发条件**绝不能是"没有进行中的对局"**——离线、主菜单、
  载入中都是合法状态，不是故障。
- 凡是 Windows 子进程带 `text=True` + `PYTHONUTF8=1`，都要当心 GBK 输出解码失败，
  结果是 `stdout=None` 而不是报错；用 `.decode(errors="replace")` 更稳。
- 恢复游戏请用**显式工具**：`restart_and_load` / `load_game_save`，不要交给启发式。
- 主菜单期间哨兵仍会每 ~8s 重连一次（日志噪音，不影响游戏；载入存档后自动转为正常轮询）。

---

## 15. 2026-11-13 接管对局（T79→T97）与 civ6 MCP 重启

**背景**：用户开着《文明6》（DX12，中国·永乐，不朽，随机科技和市政）让 DSH 接管，从 T79 打到 T97 共 18 回合。完整记录见 **`对局\局6_接管_中国永乐_T79起_20261113\接管记录_T79-T97.md`**（含政策/信仰买兵/海军/大炮的决策线和 6 类坑的解法）。

### 15.1 重新启用 DSH 内置 civ6 MCP（不再用 8766 桥）

之前 profile 里 `mcp-civ6` 被置为 `disabled: true`，所以工具列表里没有 `mcp__civ6__*`。本次改回：

```
配置：F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml
改动：- id: mcp-civ6 / disabled: false
备份：cordis.patch.yml.before-enable-civ6-20261113-*.bak
```

改完 DSH 热加载，78 个工具以 `mcp__civ6__*` 直接可用，无需重启 DSH。

> ⚠ **二选一**：游戏的 FireTuner 4318 只允许一个连接。`脚本\play_mcp.py` 的 HTTP 桥（8766）
> 和 DSH 内置 civ6 MCP 抢同一个连接，只能用一个。用 MCP 时不要再起桥（本局已停桥）。

### 15.2 随机科技和市政 → 每回合假阻塞（今后固定开销）

开了「随机科技和市政」后，即使研究/市政/政策都已选好，每回合 `end_turn` 仍会被
`RESEARCH` / `CIVIC` / `FILL_CIVIC_SLOT` / `GOVERNOR_IDLE` / `CLAIM_GREAT_PERSON` /
`UNIT_PROMOTION` 等**假阻塞通知**挡住。解法：每回合 end_turn 前跑一次 Lua
`SendActivated + Dismiss` 批量清（代码见 15 节记录文档 3.1）。这是本局最大的操作开销。

### 15.3 攻城必须走 Lua

`unit_action(attack)` 只认敌方单位，目标是城市时返回 `NO_ENEMY`。攻城要用 `run_lua`（ingame）：
远程 `UnitOperationTypes.RANGE_ATTACK`；近战 `MOVE_TO` + `PARAM_MODIFIERS=ATTACK`，
把城市 HP 打到 0 后近战单位进城即占领（随后用 `city_action keep` 保留）。

### 15.4 战略资源门槛（本局实测）

- **战舰吃煤**：煤要**工业化**科技才显示/开采；本局科技线跳过工业化，导致精炼完成后仍造不了战舰。
- **大炮**要**钢铁**；火枪手/升级要**硝石**（模组规则：每次升级耗 15 硝石，军事课程政策可减半）。
- **单位升级必须在友军领土内**。
- 溢出到上限的马/铁可以拿去和 AI 换钱。

### 15.5 政体与军事卡

- 神圣委任制（QHT 模组政体）有 3 个野卡槽，可以放军事政策（本局放了义务兵制+骑攻侦联训+军事课程）。
- 法西斯需「总体战」市政（本局前置没凑齐）；`政治冒险` 解锁 **CORPORATE_LIBERTARIANISM**
  （3军/1经/1外/**5野卡=10 槽**），是最优后期政体。
- **义务兵制**（野卡）可用信仰买非平民单位：投石机 600f、骑士 660f、战舰 1290f，
  信仰 3 万+ 时等于无限军队——本局靠这个滚出 90+ 单位。
