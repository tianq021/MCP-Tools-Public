# 无主之地2 改枪工具箱（Borderlands2MCP）

> 2026-11-15 更新：游戏内 Mod 已有 0.5 待安装实现，新增独立菜单/F7装备与技能操作、曲线调整和实验经验追加；MCP仅复用Mod能力。尚未部署或实机验证，不能视为稳定完成。最新职责、入口、限制见 [运行时工具开发进度](docs/运行时工具开发进度.md)。下文部分工具数量和“仅离线”描述为较早版本记录。

给《无主之地2》做的单机修改工具箱：**加技能点、同步武器、制作/导入黑枪**。
核心是离线读写角色存档（.sav），不依赖游戏运行，改完进游戏即可生效。

> 仅用于单机娱乐。联机、竞技、成就场景不要使用；改存档前工具会自动备份。

---

## 现在能做什么

| 功能 | CLI | MCP 工具 |
| --- | --- | --- |
| 列出/查看角色存档 | `bl2.cmd saves`、`bl2.cmd info 1` | `bl2_list_saves`、`bl2_character_info` |
| 加技能点 | `bl2.cmd edit 1 --skill-points 120` | `bl2_set_skill_points` |
| 升级 / 改钱 / 背包银行 / 解锁三周目 | `bl2.cmd edit 1 --level 80 --money 99999999` | `bl2_edit_character` |
| 导出物品代码（备份用） | `bl2.cmd export 1 -o 萨尔瓦多物品.txt` | `bl2_export_item_codes` |
| 导入物品代码（同步武器） | `bl2.cmd import 2 物品.txt --where bank` | `bl2_import_item_codes` |
| 角色之间同步武器 | `bl2.cmd sync 1 2 --sections weapons --where bank` | `bl2_sync_items` |
| 黑枪/特殊装备代码库（635 条） | `bl2.cmd guns list --query 哈罗德` | `bl2_search_gun_library` |
| 一键导入黑枪 | `bl2.cmd guns add 1 超级哈罗德 --count 2` | `bl2_add_gun` |
| **特殊武器总表（传说/珠光/彩色/炽天使/E科技/独特，343 条）** | `bl2.cmd specials stats`、`bl2.cmd specials list --rarity 传说` | `bl2_special_stats`、`bl2_special_weapons` |
| **按名字直接获取表里的装备** | `bl2.cmd specials add 1 不洁哈罗德` | `bl2_add_special_weapon` |
| **背包/银行空间上限检测与修改** | `bl2.cmd space exe`、`bl2.cmd space info 1`、`bl2.cmd space set 1 --backpack 39` | `bl2_exe_caps`、`bl2_space_info`、`bl2_set_space` |
| **给游戏 exe 打"100背包"补丁** | `bl2.cmd space patch-exe --backpack-max 100 --apply --yes` | （只读检测，补丁用 CLI） |
| **技能修改（等级/点满/清空/树校验）** | `bl2.cmd skill set 1 "Locked And Loaded" 5`、`bl2.cmd skill fill 1 --all` | `bl2_skill_available`、`bl2_skill_summary`、`bl2_skill_set`、`bl2_skill_fill`、`bl2_skill_reset` |
| **Claptrap 共享储藏箱（跨角色 4 格）** | `bl2.cmd stash info`、`bl2.cmd stash set 1 不洁哈罗德` | `bl2_stash_info`、`bl2_stash_set`、`bl2_stash_clear` |
| 物品结构风险检查（等级/版本/缺部件） | `bl2.cmd code risk "BL2(...)"` | `bl2_item_risk` |
| 解码 / 改配件 / 重新编码 | `bl2.cmd code describe "BL2(...)"` | `bl2_decode_item_code`、`bl2_encode_item_code` |
| 备份列表 / 回滚 | `bl2.cmd backup list`、`bl2.cmd backup restore <目录>` | `bl2_list_backups`、`bl2_restore_backup` |

### 背包 / 银行空间上限（重要）

原版上限写死在 `Borderlands2.exe` 里：**背包 12–39（9 级 SDU，每级 +3）、银行 6–24（9 级 SDU，每级 +2）**，
只改存档会被载入时的 clamp 压回上限。

**本机已在 2026-11-15 打了 200/200 补丁**（阈值保持 39/24，即"背包到 39 就自动跳 200"）：

- 补丁后 exe SHA256：`923fc4eb…`，改动偏移 `0xD68925`（39→200）和 `0xD68947`（24→200）
- 原 exe 备份：`.local\backups\exe\Borderlands2.exe.1231b384aea7.20261115-010636.bak`
- 回滚：`.\bl2.cmd space restore-exe <备份文件>`
- 现有角色背包都会生效 200；银行容量原本到 24 的 3 个角色生效 200，其余保持不变（物品都放得下）

```powershell
.\bl2.cmd space exe                     # 检测当前 exe 的背包/银行上限
.\bl2.cmd space info 1                  # 看角色容量、已用格数、风险提示
.\bl2.cmd space set 1 --backpack 39 --bank 24              # 压回原版上限（安全）
.\bl2.cmd space set 5 --backpack max --bank max            # 新号/未扩容角色：直接写满当前上限
.\bl2.cmd space set 1 --backpack 100 --bank 60 --allow-oversize   # 写指定的超大容量
.\bl2.cmd space patch-exe --backpack-max 100 --bank-max 24 --out .local\tmp\bl2_100.exe   # 先出副本
.\bl2.cmd space patch-exe --backpack-max 100 --bank-max 24 --apply --yes                   # 替换游戏 exe（自动备份）
.\bl2.cmd space backups                 # exe 备份列表
.\bl2.cmd space restore-exe <备份>      # 回滚 exe
```

> **没在游戏里扩容过的角色**（背包 12 / 银行 6）不会自动变 200——因为补丁阈值是 39/24：
> 要么游戏里买满 SDU（到 39/24 后自动跳 200），要么 `space set <槽位> --backpack max --bank max` 直接写存档。
> 详细分析：[docs/背包与银行空间.md](docs/背包与银行空间.md)


### 技能修改与共享储藏箱

- **技能**：`field4` 未分配点数 + `field8` 已学技能（路径/等级）都能改；
  6 个职业的全部技能路径已补全（`data/skills/skills.json` + 存档实际路径），支持设置等级、点满树/全部、清空，
  可选的"每 5 点解锁下一层"校验。

```powershell
.\bl2.cmd skill available 1                 # 技能表（名字/树/层级/上限/内部路径）
.\bl2.cmd skill summary 1                   # 点数、各树投点
.\bl2.cmd skill set 1 "Locked And Loaded" 5 # 设置等级（0=移除）
.\bl2.cmd skill fill 1 --tree Brawn         # 点满一个树；--all 点满全部
.\bl2.cmd skill reset 1                     # 清空技能
```

- **共享储藏箱**：已核实 Claptrap 的跨角色储藏箱在 `SaveData\<SteamID>\profile.bin` 的
  条目 **130-133**（4 格，Binary，物品序列原始字节，空格子=40 个 0x00），不是角色 sav。
  跨角色转移的链路已在副本上验证（导出 → 写箱 → 另一角色取，代码字节一致）；
  但**未实机验证**，不宣称"最稳"或能防止游戏删除物品。

```powershell
.\bl2.cmd stash info                        # 只读：4 格内容/Badass/金钥匙
.\bl2.cmd stash set 1 不洁哈罗德             # 写第 1 格（名字或 BL2(...) 代码）
.\bl2.cmd stash clear 2
```

- **技能效果能力表（证据表 + 预览，不是已生效功能）**：把"回血/吸血/减伤/弹药恢复/元素伤害/
  额外弹丸/击杀触发/命中触发、调已有参数、跨职业移植"按 **L1-L4 分级**整理，每条给出原生字段/属性名/
  Hook、开源模组证据、依赖、叠加/持久化限制和可实现度。

```powershell
.\bl2.cmd skill fx summary        # 分级统计（L1 调参 / L2 追加属性 / L3 移植 / L4 新逻辑）
.\bl2.cmd skill fx list           # 10 项效果一览
.\bl2.cmd skill fx plan lifesteal # 证据 + hotfix/SDK 预览（PREVIEW ONLY）
```

详细调查（技能层数规则/职业模组/非法物品风险/profile.bin 格式实测与交叉核对）：
[docs/技能修改与共享储藏箱调查.md](docs/技能修改与共享储藏箱调查.md)
技能效果扩展可行性（属性/Hook 证据、分级、预览、修正记录）：
[docs/技能效果扩展可行性.md](docs/技能效果扩展可行性.md)


### 特殊武器总表

`data\特殊武器表.md`（另附 [特殊武器表.csv](data/特殊武器表.csv)）由
`scripts\build_special_weapons.py` 从本机资料自动生成，每条都带可直接导入的代码：

| 稀有度 | 含义 | 条目 |
| --- | --- | --- |
| 传说（橙装） | Legendary，橙名带红字 | 89 |
| 珠光（青装） | Pearlescent，青名 | 12 |
| 彩色（彩虹/DLC彩） | Effervescent，彩虹名 | 23 |
| 炽天使（粉装） | Seraph，粉名 | 30 |
| E科技（品红/深红） | E-tech，"深红色"按这一档理解 | 9 |
| 独特（紫/蓝红字） | Unique，带红字效果 | 139 |
| 龙堡DLC宝石武器 | 蓝宝石/翡翠/钻石… | 29 |
| 其他 | 绿装/限时道具/未标注 | 12 |
| **合计** | | **343** |

> 稀有度颜色对照：传说=橙、珠光=青、彩色=彩虹、炽天使=粉、E科技=品红（深红）、独特=蓝/紫红字。
> 如果"深红色"指的是别的档位（比如红字独特），表里也有单独的"独特"分组。

常用命令：

```powershell
.\bl2.cmd specials stats                          # 看总数分布
.\bl2.cmd specials list --rarity 传说 --kind 武器  # 只看传说武器
.\bl2.cmd specials list --rarity 彩色              # 彩虹装备
.\bl2.cmd specials list --rarity 深红色            # E科技（品红）
.\bl2.cmd specials list 哈罗德                     # 搜名字
.\bl2.cmd specials add 1 不洁哈罗德 --count 2      # 直接进 1 号角色银行
```


### 部件查找 / 黑枪组合预览（离线）

用 Gibbed Borderlands2Dumps 重建了 `lib:asset → 部件路径` 映射（635 条代码逐条验证），
提供部件查找、槽位表、覆盖率与**组合预览**。预览只输出代码和静态警告，**不写存档**：

```powershell
.\bl2.cmd parts stats                 # 部件目录/槽位/资产库统计
.\bl2.cmd parts slots                 # 武器/物品槽位名
.\bl2.cmd parts search Barrel_Torgue  # 搜索部件（路径/类型/槽位/lib:asset）
.\bl2.cmd parts coverage              # 635/343 对 dumps 总数的覆盖与缺口
.\bl2.cmd combine "BL2(...)" --part "Barrel=6:262"   # 只预览，不写存档
```

MCP：`bl2_parts_stats`、`bl2_parts_search`、`bl2_part_slots`、`bl2_parts_coverage`、`bl2_gun_preview`。

> 武器 Body/Grip/Barrel/Stock 用武器类型 PartList 检查；护盾/手雷/模组/圣物按 Item Part Lists
> 类别并集做提示（数据可能不全）。组合预览不写存档；离线 BLCMM/热修兼容性受 OfflineHelpers 1.3
> 的 GearboxAccountData 路径限制，不能承诺所有热修在离线模式可用。

> 635 条代码、343 条特殊表都**不是全游戏总数**（dumps 有 1421 ItemBalance / 353 WeaponBalance /
> 1344 ItemDefinition）；覆盖率、缺口、清理与持久化边界见
> [docs/部件与黑枪组合调查.md](docs/部件与黑枪组合调查.md)。


### 数值平衡 / 等级膨胀（离线）

`UniversalBalanceScaler` 一手源码（ScalingAdjuster）默认 **1.13**（每级 +13%），
UCP 的 UVHM PT3 倍率、Nifsara/Aaron/Oselands 的改法都已核对并做离线曲线；
**Badass/账号加成是独立因子，不混进等级曲线**。所有命令只读/预览，不写存档、不应用到运行中游戏：

```powershell
.\bl2.cmd balance sources          # 来源/对象/参数
.\bl2.cmd balance compare          # 55/60/72/80+OP 对比（模型指数）
.\bl2.cmd balance badass           # 账号加成独立因子（1.282*1.257 只是简化示意）
.\bl2.cmd balance preview --const 1.08  # PREVIEW ONLY：不写文件/不应用
```

MCP：`bl2_balance_sources`、`bl2_balance_curve`、`bl2_balance_compare`、`bl2_balance_badass`、
`bl2_balance_op`、`bl2_balance_equipment`、`bl2_balance_preview`。

> 详细模型、局限、Slag/OP/装备等级差/技能叠加边界见
> [docs/数值平衡与等级膨胀.md](docs/数值平衡与等级膨胀.md)。
> 全局缩放会同时影响玩家/敌人/物品，**不等于只削弱敌人**。


### 经验加成 / 快速升级（离线）

原版 XP 不变，只额外发 bonus：

```
bonus = floor(base_xp * extra_percent / 100)
total = base_xp + bonus
```

`base_xp` 必须是游戏最终结算 XP，**不是** `AwardCombatExperience.TotalExpPoints` 这类中间量。
只读计算/边界/能力查询，不写真实 XP/等级/存档：

```powershell
.\bl2.cmd xp calc --base 1000 --percent 50
.\bl2.cmd xp calc --base 1000 --percent 50 --current-level 80   # 满级默认不应用
.\bl2.cmd xp table --bases 100,500,1000 --percent 50
.\bl2.cmd xp capabilities     # hook/base 语义/杀敌/任务/挑战覆盖/满级/递归防护
.\bl2.cmd xp preview --base 1000 --percent 50   # PREVIEW ONLY
```

MCP：`bl2_xp_calc`、`bl2_xp_table`、`bl2_xp_capabilities`、`bl2_xp_preview`；
另有 Codex 维护的只读研究工具 `bl2_research_mod_search`、`bl2_research_feature_plan`
（`runtime_available=False`，不是运行时执行工具）。

> ExpAdjuster 改的是原版基础率和等级差曲线（本机 settings base=50 且非原始），
> **不符合“原 XP + bonus”，必须保持禁用**。
> 详细证据/覆盖/递归防护/阻塞项见 [docs/经验加成与快速升级.md](docs/经验加成与快速升级.md)。

## 本机环境（2026-11-15 实测）

- 游戏：`F:\SteamLibrary\steamapps\common\Borderlands 2`（Steam 版，已装 PythonSDK 与 Gibbed CN 存档编辑器）
- 已打背包/银行 200 上限补丁（exe 备份在 `.local\backups\exe\`，可一键回滚）
- 存档：`C:\Users\hp\Documents\My Games\Borderlands 2\WillowGame\SaveData\00000000000000000`
- 现有 10 个角色存档全部可正常解析（80 级萨尔瓦多/玛雅/零/艾克斯顿、盖姬、克里格等）
- Python：`C:\Program Files\Python310\python.exe`（带 `mcp` 1.30）
- 代码库：635 条黑枪/特殊装备代码，来自 `无主之地2改黑枪\Bi\SE改武相关教学以及黑武代码` 里的资料
  （这是本机收藏库，不是全游戏总数；见 `parts coverage`）

---

## 快速开始

在 `E:\Borderlands2MCP` 下打开 PowerShell：

```powershell
# 看所有角色
.\bl2.cmd saves

# 给 1 号角色 120 点技能点（写前自动备份）
.\bl2.cmd edit 1 --skill-points 120

# 把 1 号的武器同步到 2 号角色的银行（先 --dry-run 看结果）
.\bl2.cmd sync 1 2 --sections weapons --where bank --dry-run
.\bl2.cmd sync 1 2 --sections weapons --where bank

# 查黑枪代码库 + 导入 2 把超级哈罗德到 1 号银行
.\bl2.cmd guns list --query 哈罗德
.\bl2.cmd guns add 1 超级哈罗德 --count 2

# 解码黑枪看看它用了哪些配件
.\bl2.cmd code describe "BL2(hwAAAABc0wCE5wRABgGF6sNohWEQowQKQgyEGB4wEGIcxNjEkgnl)"
```

也可以直接用 `python -m bl2toolkit ...`（记得设 `PYTHONUTF8=1`）。

### 通过 DSH 使用（MCP）

`bl2` MCP 服务已注册进 DSH（`F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml`），
包含 15 个工具。DSH 重启后在新会话里直接说"给我 1 号角色加 200 技能点""把 1 号的武器同步给 2 号"
即可，AI 会调用对应工具。

---

## 安全机制

- **写前自动备份**：复制到 `.local\backups\<时间戳>-<文件名>\`，带 `meta.json`
- **原子写**：先写 `.tmp` 再 `os.replace`，避免写一半损坏存档
- **游戏运行检测**：检测到 `Borderlands2.exe` 在跑会拒绝写入（游戏退出时会覆盖存档），可用 `--force` 强制
- **去重**：同步/导入默认跳过目标角色已有的相同物品（按代码忽略随机 key 比较）
- **dry-run**：MCP 的 `bl2_sync_items` 默认只演示，所有写工具都支持 `dry_run=true`
- **副本验证**：项目测试全部在临时副本上做，不改真实存档

---

## 目录结构

```
E:\Borderlands2MCP\
├─ bl2.cmd                     # CLI 启动器
├─ bl2toolkit\                 # 自研工具包（Python）
│  ├─ paths.py                 # 存档路径发现、备份/恢复、原子写
│  ├─ saves.py                 # 存档读写、摘要、技能点、物品导入导出
│  ├─ itemcodes.py             # BL2(...) 代码解码/编码/改配件
│  ├─ library.py               # 黑枪代码库构建与检索
│  ├─ specials.py              # 特殊武器总表查询 / 直接获取
│  ├─ space.py                 # 背包/银行上限检测、存档扩容、exe clamp 补丁
│  ├─ skills.py                # 技能等级修改、点满树/全部、层级校验
│  ├─ skill_effects.py         # 技能效果能力表（L1-L4）与补丁/模组预览生成
│  ├─ parts.py                 # 离线部件解析/查找/组合预览（Gibbed dumps）
│  ├─ balance.py               # 等级膨胀/UVHM/OP/Badass 离线模型与预览
│  ├─ xp.py                    # XP bonus 离线计算/边界/能力
│  ├─ profile.py               # profile.bin 读写（Badass/金钥匙/Claptrap 共享储藏箱）
│  ├─ cli.py                   # 命令行
│  └─ mcp_server.py            # MCP 服务端（stdio，50 个工具）
├─ vendor\borderlands2\        # 第三方存档解析库（apocalyptech / loot-midget，已固定版本）
├─ data\
│  ├─ code_library.json        # 635 条黑枪/装备代码库
│  ├─ special_weapons.json     # 343 条特殊装备总表（结构化）
│  ├─ skill_effect_capabilities.json # 技能效果能力表（证据+分级+预览）
│  ├─ balance_models.json      # 数值平衡模型（来源/参数/场景）
│  ├─ xp_models.json           # XP 能力/来源/覆盖/等级上限证据
│  ├─ parts\                   # 离线部件/资产库数据（Gibbed dumps 派生）
│  ├─ 特殊武器表.md             # 特殊装备总表（可读版）
│  └─ 特殊武器表.csv            # 特殊装备总表（Excel 版）
├─ scripts\
│  ├─ build_code_library.py    # 扫描参考资料重建黑枪代码库
│  ├─ build_special_weapons.py # 从 xlsx/docx 资料生成特殊武器总表
│  ├─ build_parts_data.py      # 从 Gibbed dumps 生成 data/parts
│  ├─ smoke_test.py            # 端到端冒烟测试（副本）
│  └─ test_mcp.py              # MCP 协议层测试
├─ tests\                      # 110 个单元测试（unittest）
├─ docs\                       # 调研与方案文档
├─ .local\backups\             # 自动备份
└─ 无主之地2改黑枪\             # 原始参考资料（黑枪代码、教程、配件图、工具，只读）
```

---

## 测试

```powershell
cd E:\Borderlands2MCP
$env:PYTHONUTF8=1
& 'C:\Program Files\Python310\python.exe' -m unittest discover -s tests -v
& 'C:\Program Files\Python310\python.exe' scripts\smoke_test.py
& 'C:\Program Files\Python310\python.exe' scripts\test_mcp.py
```

当前状态：110 个单元测试通过，冒烟测试 7 步通过，MCP 50 个工具握手/调用正常。
技能效果能力表/部件组合预览/数值平衡模型/XP 计算都是证据/预览，未实机验证；不要把"改存档等级"当成"添加技能效果"，
也不要把"组合预览/数值指数/XP bonus"当成游戏一定接受或实际 DPS/已发 XP。
写入保护审计见 [docs/写入保护审计.md](docs/写入保护审计.md)：备份恢复也已纳入运行中阻止。

---

## 协作边界（和 Codex 一起做时）

- **写入范围**：只改 `E:\Borderlands2MCP` 内的代码/数据/文档（`bl2toolkit\`、`scripts\`、`tests\`、`data\`、`docs\`、`README.md`、`bl2.cmd`）。
- **只读、不碰**：`无主之地2改黑枪\` 参考资料、游戏安装目录（`F:\SteamLibrary\...\Borderlands 2`）、
  存档目录、以及 DSH 的其它配置。改真实存档前会先备份，并且只在用户明确要求时执行。
- 文件冲突：如果 Codex 也要改同一批文件，建议在会话里先说清分工（例如它做配件表/黑枪构建器，我做存档/MCP 侧），
  避免同时写同一文件；本仓库的 `data\` 全部可由 `scripts\` 重新生成，冲突时以脚本重跑结果为准。
- 已做过的一次外部改动（可回滚）：把 `bl2` MCP 注册进了 DSH 配置
  `F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml`，备份在同名 `.local\backups\cordis.patch.yml.*.bak`；
  不需要的话删掉配置里 `mcp-borderlands2` 那一段即可。

---

## 已知限制 / 待办

1. **配件还是 `lib:asset` 编号**，没有中文配件名。`bl2.cmd guns decode` 能看到 `6:250` 这种编号，
   但要"选枪管造黑枪"还需要一张配件名表（见 `docs\技术调研与方案.md` 的 Phase 2）。
2. **背包/银行上限由 exe 硬编码**（原版 39/24），只改存档不生效；要扩容见 [docs/背包与银行空间.md](docs/背包与银行空间.md)。
   本机部分旧存档容量是 200/250，在当前 exe 下会被压回上限，建议先 `space info` 检查。
3. 特殊武器总表来自本机资料（xlsx/docx），**不是全游戏完整清单**；缺的条目需要补资料或后续用配件表生成合法代码。
4. 等级上限按原版 80 级；OP 等级（8/10）还没做编辑入口。
5. 修改器只改存档，**不提供游戏内实时修改**（游戏内修改需要 PythonSDK mod，见 Phase 3）。
6. 云存档：如果 Steam 开了云同步，建议改前退出游戏、改完让 Steam 同步一次。

---

## 数据来源与致谢

- 存档格式解析：[apocalyptech/borderlands2](https://github.com/apocalyptech/borderlands2) 与
  [loot-midget/borderlands2](https://github.com/loot-midget/borderlands2)（本仓库 `vendor\` 内置，MIT 风格个人项目）
- 物品代码格式参考：[gibbed/Gibbed.Borderlands2](https://github.com/gibbed/Gibbed.Borderlands2)
- 黑枪/装备代码与教程：`无主之地2改黑枪\Bi\` 下的社区资料（作者见各文件）
- 本机游戏内 mod 框架：[apple1417/bl-sdk](https://github.com/apple1417/bl-sdk)（PythonSDK，已安装在游戏里）

## 免责声明

这是单机游戏修改工具。请勿用于联机作弊、交易诈骗或影响他人游戏体验；
存档损坏风险由使用者承担（工具已尽量做备份与原子写，但不保证 100% 安全）。
