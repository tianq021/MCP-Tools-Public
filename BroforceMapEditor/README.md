# Broforce 地图编辑器 / AI 关卡生成（E:\BroforceMapEditor）

**当前新图：断崖营救（112×144）**，采用中文命名、纵向交错路线、设施背景及救援支路。见 [地图说明](docs/断崖营救_地图说明.md)。

**最新大地图：翠幕行动 · 六区远征（384×72）**，40 个敌人布点、44 个补给箱、9 个救援囚笼、12 个检查点，已加载。参见 [地图说明](docs/06_翠幕行动.md)。

**2026-11-15 接管更新**：地图 MCP 已实现并注册；当前使用「AI 丛林前哨 05」。已验证出生角色、敌人落点、原生终点触发及直接返回主菜单（不播放片尾）。详情见 [MCP 接管与验收](docs/05_MCP接管与验收.md)。下方 M0/M3 部分为早期记录。

目标：让 AI 模型通过 **MCP 或本地工具**在《武装原型》（Broforce，Steam App `274190`）里**创建新地图并加载游玩**。

创建日期：2026-11-15。新建独立项目，不改动 `G:\Trainer` 下的修改器成品与游戏安装目录。

## 现状（2026-11-15，M0 完成）

- 游戏本机构建 `12964083`（Unity 2017.4.7f1，版本串 `1.0.3148`）。
- 游戏**自带关卡编辑器**（`LevelEditorGUI`），支持保存/加载与 Steam 创意工坊发布。
- 自定义关卡文件在 `F:\SteamLibrary\steamapps\common\Broforce\Levels\*.bfg`，本机已有一个社区样本（45 KB）。
- `.bfg` 格式已拆开：**XML `CampaignHeader` + 二进制关卡体**（`CLZF2` 压缩 → MD5 → DES 加密），二进制体是 `MapData` 的自定义序列化。
- **M0 已完成**：C# 桥接 `bridge/BroforceMapBridge.cs`（编译产物 `dist/BroforceMapBridge.dll`）可注入游戏，`ping`/`state`/`make_test_map` 已在运行中的游戏里验证通过；`save_bfg`/`load` 已实现（`save_bfg` 修复了“LoadLevelFromDisk 只收文件名”的坑，待重启游戏后用新桥接复测）。
- Python 客户端 `tools/bridge_client.py` 支持 inject / ping / state / make-test-map / save-bfg / load / list-levels。

详细调研见 [docs/01_调研_关卡格式与编辑器.md](docs/01_调研_关卡格式与编辑器.md)，路线与验收见 [docs/02_方案与路线.md](docs/02_方案与路线.md)，桥接协议见 [docs/03_桥接协议.md](docs/03_桥接协议.md)。

## 目录

```text
docs/       调研结论、方案与里程碑
research/   离线程序集分析工具与 IL dump（dumps/）
bridge/     注入游戏的 C# 桥接（生成 MapData、保存 .bfg、加载关卡）
mcp/        MCP 服务端（把桥接能力暴露给 AI 模型）
tools/      本地脚本（离线格式工具、探针、校验）
maps/       生成的关卡与样例（.bfg / 中间 JSON）
evidence/   版本指纹、校验记录
```

## 安全边界

- 不写入 Steam 安装目录（除非显式操作 `Levels/` 放置生成的关卡，且可回滚）。
- 桥接只在游戏进程内、只由本工具注入；不入库、不改存档。
- 生成的关卡先落 `maps/`，确认后再复制到游戏 `Levels/` 或由桥接直接加载。

## 地图备注规范（2026-11-15 用户要求）

**以后每张生成的地图都必须带备注**，至少包含：

1. `CampaignHeader.name`：关卡名（会显示在「自定义关卡 → 离线」列表）
2. `CampaignHeader.author`：生成方，固定 `BROFORCEMAPBRIDGE`（或后续模型名）
3. `CampaignHeader.description`：**中文备注**，写明生成日期、生成器版本、意图/参数
4. `MapData.levelDescription`：同一份备注（游戏内“地图详情”读的就是这个字段）
5. 项目侧 `maps/<文件名>.json`（后续 M2 起）：完整生成元数据（prompt、seed、尺寸、tile 图、备注）

桥接 `make_test_map` 已支持 `name` / `author` / `description` 参数；客户端：

```powershell
python tools\bridge_client.py make-test-map --width 40 --height 20 `
  --name "AI TEST MAP" --author "BROFORCEMAPBRIDGE" --note "AI 生成脚手架；2026-11-15 V0.1"
```

## 地形坐标约定（实测）

- `MapData.foregroundBlocks` 索引是 `[column, row]`，`Map.GetBlockCenterY(row)` 表明 **row 0 = 底部**，行号向上递增。
- 脚手架生成规则：地板 = `row <= 1`，左右墙 = `column == 0 / columns-1`，中部平台 = `row = rows/2`。
- 早期版本把地板填到了高行（顶部），导致角色脚下悬空、落地即死（生命数 0）——已修正。

## M3 创作能力（2026-11-15）

桥接新增命令（协议见 docs/03）：

| 命令 | 说明 |
|---|---|
| `new_map` | 新建空白地图：宽高/主题/音乐/天气/镜头/出场方式 + 名称/作者/**备注** |
| `set_tiles` | 读取地块文本（字符行，第一行=顶部；legend 映射到 `TerrainType`），自动翻转为 row 0 = 底部 |
| `add_doodads` | 读取 `x,y,DoodadType[,variation]` 行，写入 `MapData.DoodadList` |
| `inspect_level` / `clone_level` | 读磁盘关卡（.bfc/.bfg 都试）做范本/克隆 |
| `dump_triggers` | 打印触发器类型/范围/动作（研究关卡结束机制用） |

素材清单：`docs/04_可放置素材清单.md` + `research/catalog.json`（103 Doodad / 86 Terrain / 57 Hero / 116 Ground / 10 触发条件 / 22 触发动作）。

第一张原创图：`tools/make_original_map.py` → **AI ORIGINAL 01**（120×40 丛林峡谷：底板走廊 + 8 段平台与梯子 + 2 个深坑与桥 + 27 个物件：出生点/3 检查点/16 敌人/箱子/弹药箱/囚笼/路牌），备注写入 `CampaignHeader.description` 与 `MapData.levelDescription`。

## 已知教训

- `clone_level` 曾因误用 `path` 把游戏 `Levels/WWB7 ….bfg` 覆盖成 `.bfc` 内容：原始工坊关卡未备份，只能在工坊重新下载；损坏件已移到 `maps/WWB7_overwritten_by_tool_20261115.bin`。
- 已加保护：`save_bfg` 校验扩展名与格式（`.bfc`↔不加密，`.bfg`↔加密），不匹配直接报错；`clone_level` 不给路径时自动写 `maps/<名字>.bfc`。
