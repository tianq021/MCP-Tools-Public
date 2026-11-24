# Frostpunk 1 原生控制基础版

2026-11-10 在用户通过“继续游戏”加载的城市中完成实机验证。直接读取游戏内存，通过游戏主线程调用原生引擎函数；不依赖截图、鼠标或键盘。

## 已提供的 MCP 工具

| 工具 | 用途 |
| --- | --- |
| `get_state(include_catalog=false)` | 库存、暂停状态、研究项目、工坊数量和原始模拟时钟 |
| `get_tasks(include_completed=false, include_hidden=false)` | 当前任务、中文目标、进度/激活状态、截止时间；无需判断模式 |
| `pause()` | 原生暂停并核对时钟停止 |
| `advance(seconds)` | 推进最多 10 秒现实时间，finally 中重新暂停；检查 `advanced` |
| `start_research(technology)` | 使用原生资格检查、成本处理和研究开始函数 |
| `get_city()` | 建筑稳定 ID、活动开关、活动/工作人数及能量塔原始设置 |
| `get_abilities(entity_id)` | 能力是否可见、解锁、当前启用状态 |
| `set_ability_enabled(entity_id, ability, enabled)` | 通过正常引擎规则切换能力，如 `Generator Overdrive` |
| `set_building_enabled(entity_id, enabled)` | 建筑活动开关，写后核对 |
| `get_workforce(building_id=null)` | 指定建筑的分配人员，或所有工作岗位人员/自动机 ID；要求暂停 |
| `set_workplace(agent_id, building_id=null)` | 分配岗位，或以 null 卸下；使用游戏原生资格/容量检查和事件 |

先调用 `get_state`，确认 `ready=true`，再 `pause`。用 `get_city` 找建筑 ID，用 `get_workforce` 找人员 ID。所有写操作先暂停；只有 `advance` 暂时恢复模拟。ID 只对当前加载城市有效，加载后重新查询。

活动人数、实际工作人数和分配人数含义不同。`activity_state_code` 和 `requested_steam` 是原始字段，不能当作工人数或直接推断能量塔是否开启。`set_workplace` 遵从正常游戏逻辑，分配自动机可能替换建筑原有工人，应先读取岗位状态。能力切换不代表任何能力都无成本或可逆。

## 启动与使用范围

仅支持已核验的 Steam build 20303936：Frostpunk.exe SHA256
`031dbfe529256b7bf532aa11bfaf8ef75ef0618aa0492a970e532566342ba19d`。
游戏路径 `F:\SteamLibrary\steamapps\common\Frostpunk`。版本或对象布局不符合即拒绝访问。

基础版强制要求测试进程内的禁止存档保护，拒绝普通可存档进程。当前验证不会写回用户存档。主菜单、自动读档、正常持久保存、建造选址和完整自主经营不在本轮已实现范围。

关闭普通游戏后，在 PowerShell 启动监督进程，再由用户在游戏里点“继续游戏”：

```powershell
& 'C:\Program Files\Python310\python.exe' -B 'E:\冰汽时代\.fp1-test-city.py' --duration 0
```

默认不传 `--duration` 时 30 分钟后关闭测试游戏并恢复由本次监督进程安装的归档；`--duration 0` 持续到输入 `quit`、stdin 关闭或游戏退出。支持 `status` 和 `extend`（延长 30 分钟）。该选项不改变已启动旧监督进程的期限。监督进程的控制台必须保持运行。

MCP 服务入口：`C:\Program Files\Python310\python.exe -B E:\冰汽时代\frostpunk_mcp.py`，环境 `PYTHONUTF8=1`。Codex 已注册 `frostpunk`。旧连接可能仍缓存旧工具列表，重连后加载 11 个；新的 stdio 客户端已验证。任务接口详情见 [QUEST_INTERFACE.md](QUEST_INTERFACE.md)。

DSH 的 web profile 已添加 `mcp-frostpunk`，2026-11-10 API 查询返回 enabled=true、fiberPhase=active。与 Codex 共用服务代码和跨进程操作锁，避免两个客户端同时执行一条控制操作。当前没有跨多条工具调用的独占租约，经营时应由一个客户端负责决策。

## 实机验证证据

- `test_mcp_live.py`：实际 MCP 协议初始化、10 个工具注册、库存读取、非法推进/人员输入拒绝、0.25 秒推进后暂停通过。
- `test_abilities_live.py`：暂停且禁止存档时，能量塔过载 false → true → false，通过；资源和时钟不变。
- `test_building_live.py`：工坊活动开关往返恢复，通过；资源和时钟不变。
- `test_workforce_live.py`：工坊自动机卸下 → 原岗位重新分配，通过；人员列表恢复，资源和时钟不变。
- `test_dsh_bridge.mjs`：使用 DSH 已安装的 MCP 客户端和真实工具适配器读取当前任务，11 工具通过；不调用模型、不产生模型费用。
- `test_tasks_live.py`：任务/目标状态与原生 getter 逐项比对及实际 MCP 调用通过；详见 QUEST_INTERFACE.md。
- 研究接口此前验证了无工坊时的引擎拒绝路径；本次未验证研究成功后的完整研发流程。

上述可逆控制测试需要用户已授权，不能把脚本当作普通只读测试自动运行。测试脚本会尝试 finally 恢复，但进程退出或崩溃时恢复只能依靠重新载入原存档。

原生调用支持最多 8 个整数/指针参数，不支持任意浮点参数。执行页暂保留到游戏进程退出，以免延迟回调访问已释放地址。无通用任意内存写入工具对外暴露。
