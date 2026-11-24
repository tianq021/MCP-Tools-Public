# RA2MCP 阶段一验证记录（2026-10-28）

> 2026-10-29 训练方向修正：移动步长 4→10 格、观测增加上一步动作与敌方出生点方位、
> 新增「前出推进 / 首次抵达敌方基地附近 / 首次发现敌方建筑」奖励，胜利 +100 / 战败 -50。
> 动作契约 `full_ground_v2`、奖励版本 `full_configurable_v3`，旧 Full 模型不能续训，需 `.\train.cmd -Fresh`。
> 说明见 [LOCAL_MODEL.md](LOCAL_MODEL.md)。

> 2026-10-29 多实例并行：`.\train.cmd -Instances 2` 可同时跑 2~4 个真实对局训练实例；
> 每个实例独立游戏副本（`test-game-<i>`）、端口（14521+i）、锁和 run 目录，各训各的模型。
> 红警引擎的单实例互斥体已通过 `scripts/patch_multi_instance.ps1` 按副本改名解决。
> 说明见 [LOCAL_MODEL.md](LOCAL_MODEL.md)。

> 2026-10-29 持续训练入口：`.\train.cmd` 后台启动/续训，`.\train.cmd stop` 保存并停止，
> `.\train.cmd status` 查看状态。Full 模型每局打到实际结束后保存复盘并自动开下一局，直到用户叫停。
> 可双击 `stop-training.cmd` 停止；关闭启动窗口不会停止后台训练。说明见 [LOCAL_MODEL.md](LOCAL_MODEL.md)。

> 2026-10-29 Codex 进展：本地 BC 模型与真实游戏 PPO 小规模训练已建立。
> 使用入口见 [LOCAL_MODEL.md](LOCAL_MODEL.md)，阶段验收见 [reports/codex-roadmap.md](reports/codex-roadmap.md)。
> 已发现并修正旧统计将中立建筑算入敌方伤害的问题；旧报告的战斗有效性需按新审计重判。

> 2026-10-29 奖励可配置：运行或双击 `.\settings.cmd`（奖励设置窗口），逐项启用并设置分值、
> 每步/每局上限和计分次数，保存后下一局生效；默认值保持原计分。说明见 [LOCAL_MODEL.md](LOCAL_MODEL.md)。
>
> 2026-10-29 对局可配置：同一个 `.\settings.cmd` 也会打开对局设置窗口（电脑难度、初始资金、颜色、
> 规则开关等对局设置，保存后下一局生效。说明见 [LOCAL_MODEL.md](LOCAL_MODEL.md)。

工作目录 E:\RA2MCP。原游戏 E:\hj 未写入、未启动、未改存档。初次列举工作目录为空。

## 实际产物
- vendor/ra2yrcpp: ee215f5a01f709c52b1fe4dd333d16cbe5d5f146
- vendor/pyra2yr: 05a20df628eff3cb28d9530e80addcca57a2cb0c
- test-game: 从 Yuris Revenge/Game 复制，排除 *.sav、日志及常见存档目录；复制不等于完整备份，原目录仍保留。
- downloads: 上游发布压缩包、解压 DLL 和工具。尚未执行下载的二进制。
- .venv: Python 3.12；独立安装探针依赖。
- scripts/inventory.py: 记录 726 个 exe/dll/ini 的 SHA256、大小和 PE 架构。
- scripts/probe.py: 仅 GetGameState，整体请求超时 5 秒；失败退出码 1，不把空状态视为成功。

## 游戏兼容性
hjwdzstest 与 wdzs_TEST 的 GameMD.exe 为 5943296 字节，版本资源 1.11；CnCNet Game 的 gamemd.exe 和 gamemd-spawn.exe 为 4813072 字节，同样显示 1.11。版本资源不足以证明地址布局相同或可注入。
CnCNet Game 已有 Ares、Phobos 0.4.0.2、Syringe、CnCNet-Spawner 和 cnc-ddraw。多补丁组合兼容性未验证；不能据此宣称干净 YR 1.001 基线。
现有 spawn.ini 指向单人战役 ALL02UMD.MAP，有自动存档配置；不直接沿用作验证方案。

## 源码结论
首选 https://github.com/shmocz/ra2yrcpp 与 https://github.com/shmocz/pyra2yr 。前者实际提供 protobuf/HTTP/WebSocket 原生桥接，后者 DualClient 使用 command/poll 两条连接。GetGameState 返回快照；singleStep=true 时会解除暂停，故只读必须 false。
上游推荐 Syringe + 非 hardened yrpp-spawner；Syringe 与 legacy spawner 的组合在 README 仍为 TODO。不可凭文件名确认现有生成器兼容。
websocket_server.cpp 的 listen(tcp::v4(), port) 监听所有 IPv4 地址；allowedHostsRegex 不匹配仅在 pre-init 回调 return，未明确关闭连接。启动前应改为显式 loopback bind 并编译验证，不依赖该配置作为安全边界。
上游 issues #24 有断线挂起，#17 有重复事件误判。不可直接宣称已有可靠断线恢复/去重。
未检查 LuaAPI/OpenRA-RL 源码；优先路线尚未被排除，暂不引入额外实现。

## 测试状态
已完成目录盘点、源码下载、隔离复制、依赖安装、指纹清单生成。
probe-before-launch.json 为无服务的失败路径测试，非真实游戏连通成功。
未通过/未执行：原生状态读取、真实帧递增验证、游戏指令、标准 MCP、双客户端协作、租约恢复和人工优先。

## 下一步与架构
1. 修改 native server 仅绑定 127.0.0.1，拉取锁定版本子模块并构建 x86；确认发布 DLL 与源码/协议匹配。
2. 在副本中选择 non-hardened spawner；先禁用副本里的其他扩展，生成离线遭遇战配置并关闭自动存档，再验证 Syringe 加载、无冲突退出。
3. 连续读两份 GetGameState，验证当前阵营、帧号、单位与地图，再确认读操作不改变暂停/游戏速度。读取失败不得降级为模拟数据。
4. 一个常驻控制服务持有唯一 native 连接；Codex/DSH 共同接入同一 MCP 服务。使用官方 MCP SDK，不让两个 stdio 进程各自持有独立租约状态。
5. SQLite 持久化 command_id+参数哈希、会话 epoch、任务状态；重复 ID 参数不同拒绝。发出后断线标记 outcome_unknown，不盲目重发。
6. 单位租约采用单调时钟、过期撤销、会话 fencing token；版本校验使用 epoch+frame。执行前再次核对单位归属、租约与版本。
7. 原生游戏线程执行队列；人类输入立即撤销对应单位租约并清理待执行命令。必须原生识别手工输入才能宣称人工优先有效。
8. 逐步开放移动/停止，再生产/部署；以双方客户端竞争、过期、重连、崩溃恢复、人工抢占用例验证。

运行只读探针：
E:\RA2MCP\.venv\Scripts\python.exe E:\RA2MCP\scripts\probe.py
运行清单：
E:\RA2MCP\.venv\Scripts\python.exe E:\RA2MCP\scripts\inventory.py

## 2026-10-28 DSH 追加（最新状态以此为准）

- 编译环境已解决：Docker 网络异常恢复后，用自建 `ra2mcp-builder:local` 镜像 + 锁定 protobuf 子模块编译出**仅监听 127.0.0.1** 的 x86 `libra2yrcpp.dll`（版本/命令/依赖/产物指纹见 reports/dsh-artifacts.json）。
- 已完成两份环境的**离线只读连通验证**：
  1. `E:\RA2MCP\test-game`（CnCNet YR 包副本，legacy spawner 路线）；
  2. `E:\RA2MCP\test-game-wdzs`（`E:\hj\wdzs_TEST` 的只读副本，即「无敌之师」NPatch mod；必须经 `Yuri.exe` 启动器，副本内打在 `GameMD.exe` 的加载器补丁已验证）。
- 连续读取真实帧号/阵营/单位归属均通过，游戏持续运行，无写命令。
- 详细结论、已通过/未通过、修改文件、复现与回退命令、下一步：**reports/dsh-handoff.md**（含附录 A：wdzs_TEST mod 环境）。
- 仍未支持：写命令、MCP、战役人工/AI 切换、遭遇战小队/独立阵营分配、模型接管内置电脑阵营。

