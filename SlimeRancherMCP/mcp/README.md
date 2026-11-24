# 史莱姆牧场 1 MCP 工具

```
DSH / MCP client ──stdio──▶ mcp/server.py ──HTTP 127.0.0.1:18760 + token──▶ 游戏内 mod（SlimeRancherBridge）
```

游戏 Mod 在 Unity 主线程执行操作，MCP 进程负责工具注册、参数校验和结果转发。桥接配置在 `config/bridge.json`，存放本地端口和凭据。

## 运行

1. 在游戏退出后运行 `plugin/SlimeRancherBridge/build.ps1 -Deploy`。仅编译时省略 `-Deploy`，输出在 `plugin/dist/SlimeRancherBridge`。
2. 从 Steam 启动游戏并进入存档。
3. 运行 `python -B tool/test_mcp_client.py` 检查 MCP 声明和基本端到端调用。
4. 用 `sr_health` 核对当前游戏进程的 `bridge_version`、`game_loaded` 和 `tools`。文件部署和进程加载分别发生在部署时和游戏启动时。

## 在 DSH 里注册（示例）

把下面的 stdio 配置加进所用 MCP 客户端，再开启或重载 MCP 会话。使用已安装 Python MCP SDK 的解释器。

```json
{
  "slime_rancher": {
    "command": "python",
    "args": ["E:\\SlimeRancherMCP\\mcp\\server.py"],
    "env": { "PYTHONIOENCODING": "utf-8" }
  }
}
```

## 查询和操作

74 个工具的参数、取值范围和默认值由 [tools.json](tools.json) 生成。各工具通过 MCP 的 `tools/list` 返回自己的参数说明。

| 场景 | 查询工具 | 操作工具 |
| --- | --- | --- |
| 通道、位置、时间 | `sr_health`、`sr_ping`、`sr_echo`、`sr_state`、`sr_player`、`sr_time`、`sr_zones` | |
| 背包、市场、仓储 | `sr_vacpack`、`sr_market`、`sr_storage`、`sr_item_catalog`、`sr_find_items`、`sr_loose_items` | `sr_sell`、`sr_move`、`sr_pickup_loose` |
| 牧场、收集、喂食 | `sr_plots`、`sr_plot_shop`、`sr_collectors`、`sr_feeders` | `sr_buy_plot`、`sr_buy_plot_upgrade`、`sr_start_collector`、`sr_set_feeder_speed` |
| 精炼、科技、钻井 | `sr_refinery`、`sr_gadgets`、`sr_gadget_sites`、`sr_extractors` | `sr_deposit_refinery`、`sr_buy_blueprint`、`sr_fabricate`、`sr_place_gadget`、`sr_harvest` |
| 小蜜蜂 | `sr_drones`、`sr_drone_options` | `sr_program_drone`、`sr_recharge_drone` |
| 委托、玩家升级、记录 | `sr_exchange`、`sr_player_shop`、`sr_progress`、`sr_mail`、`sr_pedia` | `sr_deliver`、`sr_buy_player_upgrade` |
| 存档与备份 | `sr_backup` | `sr_save` |
| 围栏、史莱姆体检 | `sr_corrals`、`sr_slimes` | `sr_collect_all`、`sr_feed` |
| 筒仓仓口可见性 | `sr_storage`（看 ports/accessible） | `sr_silo_port` |
| 一次汇总巡检 | `sr_overview` | |
| 探索：Gordo/宝箱/钥匙门 | `sr_gordos`、`sr_pods`、`sr_doors` | |
| 存档管理 | `sr_saves` | |
| 批量收获/出售/入仓 | | `sr_harvest_all`、`sr_sell_all`、`sr_store_all` |
| 史莱姆百科 | `sr_slime_info` | |
| 委托接取 | `sr_exchange_pending` | `sr_exchange_accept` |
| 巡检/卖出建议 | `sr_watch`、`sr_market_advice` | |
| 作弊/回滚 | | `sr_teleport`、`sr_restore_backup` |

### 从指定来源卖结晶

先查 `sr_find_items`，确定结晶在背包或哪个仓储格子中。背包的 `source_storage_id` 为 0。仓储、喂食器和收集器使用 `sr_storage` 的容器 ID。格号从 0 开始。场景物品需要先拾取到背包。

```powershell
python -B tool/bridge_cli.py call sr_find_items --arg item_id=PINK_PLORT
python -B tool/bridge_cli.py call sr_sell --arg item_id=PINK_PLORT --arg source_slot=0 --arg count=1
```

第二条默认只在主线程检查来源、闭市状态和单价，返回估计收益。实际执行示例使用同一组已确认参数，并控制最低单价。

```powershell
python -B tool/bridge_cli.py call sr_sell --arg item_id=PINK_PLORT --arg source_slot=0 --arg count=1 --arg min_unit_price=6 --arg dry_run=false --request-id sell-pink-001
```

实际结果包含扣除量、新币增量和结算后余额。若操作的是仓储格子，再加入 `--arg source_storage_id=<容器 ID>`。

### 牧场设备与后期杂务

- `sr_storage` 默认只列筒仓已解锁的槽位。`ports` 给出各个已开放仓口的坐标、当前槽及可切换的槽号。排查旧版误存时设 `include_locked=true`，用 `accessible` 区分槽位。用 `sr_move` 把旧物品从锁定槽挪到同一筒仓的可用空槽，或者挪到放有相同物品的可用槽。
- 给 `sr_storage` 传 `item_id`（例如 `CRYSTAL_PLORT`）可逐格查询 `can_receive` 和 `free_space_for_item`。工具使用游戏原生的物品兼容判断和当前槽容量，适用于空仓库和已有物品的仓库。
- 从收集器取结晶、给喂食器补料时，先查 `sr_storage` 的 PLORT 或 FOOD 容器，再用 `sr_move` 指定两侧格子。容量足够才执行整笔转移。
- `sr_extractors` 返回钻井、泵、蜂箱的周期和候选产物。`sr_harvest` 触发原生吐料，随后可用 `sr_loose_items` 找到附近实体，再决定是否拾取或存入精炼机。
- 小蜜蜂程序用 `sr_drone_options` 返回的选项 ID，`sr_program_drone` 按游戏规则检查组合。充电用 `sr_recharge_drone`，以背包水格为来源。
- 购买前用 `sr_player_shop`、`sr_plot_shop` 或 `sr_gadgets` 查报价和资格。购买工具的 `max_price` 可锁定预算。
- 交付委托时传入 `sr_exchange` 的 `offer_type` 和 `offer_id`，奖励由原生交付点生成。

### 请求结果

- 写工具默认 `dry_run=true`。实际执行传入 `dry_run=false` 和独立的 `request_id`。重试同一动作时保持 ID 和参数相同，完成记录默认保留 10 分钟，游戏重启后重新检查库存、余额或目标状态。
- `IN_PROGRESS` 表示游戏线程已接手，可用原 ID 查询结果。 `TIMEOUT` 表示排队请求未开始并已取消。网络断开后也应保留原 ID。
- `NOT_READY` 表示需要进入存档，`GAME_PAUSED` 表示执行阶段需要退出游戏暂停或等待快进结束。
- 地块、设备及仓储操作依赖游戏已加载的对象。切换区域或重载存档后重新查询 `storage_id`、`instance_id`、`feeder_id` 和 `collector_id`。地面实体的位置可用来寻找物品，生成来源需要其他游戏信息佐证。


## 版本与部署（2026-11-19）

- 桥接当前：**v0.9.0**，74 个工具（45 查询 + 29 写）。`mcp/server.py` 只读 `mcp/tools.json`；仓库根目录另有一个 `tools.json`（78 条无 `sr_` 前缀的 find_/inspect_ 候选清单），**未接入**，不要当成工具定义。
- **2026-11-19 22:49 重建的 dll** 在 v0.9.0 基础上多带两个 Harmony 补丁（不影响工具清单，启动日志各有一行 `drone placement…` / `drone battery…`）：
  - `DronePlacement`：`GadgetDirector.GetPlacementError` 后置补丁，只抹掉 `w.limit_reached_drone` —— 普通/高级小蜜蜂不再受「每网络合计 N 台」限制；站点必须属于 `DroneNetwork`、必须有空站点等其它放置条件仍然生效。
  - `DroneBatteryDuration`：把 `DroneStationBattery` 的满电续航常量 28 → **84 游戏小时**（≈84 分钟现实，1 游戏小时≈60 秒）；1 单位液体仍是一次回满。
  - 两个补丁都在 mod 加载时安装，改完 C# 必须**退出游戏**再 `-Deploy`，否则旧 dll 仍在进程里。
- **2026-11-20 新增开发工具** sr_spawn_slime（作弊类：在玩家面前生成指定史莱姆，用于测试/稀有史莱姆成就），随 01:51 dll 部署；MCP 会话需重载才看得到，脚本可用 	ool/bridge_cli.py 直调。
- 游戏内额外 overlay（不是 MCP 工具）：`F2/F4` 聊天框、`F8` 小地图开关、右上角系统时钟。详见 [docs/HANDOVER.md](../docs/HANDOVER.md) §6.7。
- v0.9.0 新增游戏内隐藏聊天框（回车呼出）：`sr_chat_pending` / `sr_chat_send` / `sr_chat_status`，
  用法见 [docs/CHAT.md](../docs/CHAT.md)。聊天工具只操作内存队列和 UI，不触发 dry_run 规则。
- v0.9.0 另加 `sr_map_data`：定位各区域「地图数据」全息装置（找不到开地图的蓝色数据块时用）。
- v0.9.0 另加跨区域仓库工具（数据模型层，未加载区域也能用）：
  - `sr_model_storage`：读任意地块仓库（含未加载），返回 storage_type/slot/item/count；
  - `sr_model_move`：在仓库之间搬运（含跨未加载区域），写入需 `dry_run=false` + 唯一 `request_id`；
    已加载的一侧走游戏正常接口同步场景对象，未加载的一侧直接改 `LandPlotModel.siloAmmo`。
- v0.8.0 已在游戏内实测（66 工具全部注册、只读/写工具 dry_run 验证通过）；v0.8.1 修
  `sr_slimes` 性能、野外史莱姆围栏归属、`sr_saves` 当前槽标记、`sr_silo_port` 支持休眠筒仓。
- v0.8.2 修巡检发现的 4 个问题：`sr_saves` 的 `current` 改用 Harmony 捕获的真实存档名、
  `day` 改成 1 基、`saved_at` 改本地时区（另给 `saved_at_utc`）、`sr_pods`/`sr_doors` 带筛选时
  `total` 与 `*_total` 口径统一（新增 `all_total`/`loaded_total`）。细节见 `docs/HANDOVER.md` §21。
- 部署：**游戏退出后**执行 `plugin/SlimeRancherBridge/build.ps1 -Deploy`。
  游戏运行中覆盖 dll 会失败：`file with a user-mapped section open`。
- MCP server 只在进程启动时读取 `tools.json`：新增/修改工具后需要**重载会话**；
  未重载时可先用 `python -B tool/bridge_cli.py call <tool>` 直连 HTTP 验证。
- 版本核对：`sr_health` 的 `bridge_version` 与 `tools` 列表长度。`sr_saves` 用
  `slot_tracking` 字段确认当前槽追踪是否生效（`harmony` / `unavailable`）。

## v0.5–v0.8 新增工具速查（相对 v0.4 的 44 个）

| 工具 | 一句话 |
| --- | --- |
| `sr_backup` | 备份 .sav 到 `backups/` 并回传 SHA256（主菜单也能用） |
| `sr_save` | 调游戏原生保存并回读文件哈希（默认预演） |
| `sr_saves` | 列磁盘存档摘要（天数/新币/百科/时间/当前槽） |
| `sr_restore_backup` | 从备份还原 .sav（仅主菜单；先自动再备份当前档） |
| `sr_silo_port` | 切换筒仓仓口显示槽（含休眠筒仓） |
| `sr_slimes` | 内存史莱姆清单（情绪/feral/最近围栏） |
| `sr_slime_info` | 史莱姆饮食、最爱食物、产出 |
| `sr_corrals` | 围栏体检（史莱姆/地上结晶/收集器/喂食器/告警） |
| `sr_feed` | 从 FOOD 仓搬食物进喂食器 |
| `sr_collect_all` | 批量触发收集器 |
| `sr_harvest_all` | 批量触发钻井/泵机/蜂房产出 |
| `sr_sell_all` | 按来源批量卖同一种结晶（最低单价/上限/逐槽回执） |
| `sr_store_all` | 背包批量入仓或入精炼机 |
| `sr_gordos` | 全图 Gordo 状态（已吃/是否爆/是否掉钥匙） |
| `sr_pods` | 全图宝箱开/锁与蓝图 |
| `sr_doors` | 全图钥匙门状态与目标区域 |
| `sr_exchange_pending` | 待选委托人列表 |
| `sr_exchange_accept` | 接受/选择当期委托 |
| `sr_watch` | 一次返回全部告警（喂食/结晶/饥饿/电量/委托/市场） |
| `sr_market_advice` | 价格/基准/饱和度 + SELL_NOW / HOLD / WAIT 建议 |
| `sr_teleport` | 传送到坐标或已加载地块（作弊类，需显式执行） |
| `sr_overview` | 一次汇总：玩家/时间/市场/委托/史莱姆/仓储/设备/告警 |
