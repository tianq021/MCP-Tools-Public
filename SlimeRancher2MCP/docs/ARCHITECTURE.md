# 架构（与 1 代同构，实现层适配 IL2CPP）

```
DSH / MCP client
   │ stdio (JSON-RPC, FastMCP)
   ▼
mcp/server.py           # 只做三件事：读 tools.json 注册工具 / 转发 / 错误结构化
   │ HTTP 127.0.0.1:18762 + X-Bridge-Token
   ▼
游戏内 mod: MelonLoader 插件 SlimeRancher2Bridge.dll
   │ TcpListener（后台线程）+ 主线程任务泵（OnUpdate）
   ▼
Unity 6 / IL2CPP 游戏对象（Il2CppInterop 互操作程序集）
```

## 1. 组件职责

| 层 | 文件 | 职责 |
| --- | --- | --- |
| MCP 前端 | `mcp/server.py` | 读 `mcp/tools.json` 动态注册工具；`sr2_*` 参数校验；错误变结构化文本 |
| 通道客户端 | `mcp/bridge_client.py` | 读 `config/bridge.json`，HTTP 调用 `/health`、`/rpc` |
| 调试 CLI | `tool/bridge_cli.py` | 不经 MCP，直接 health/call，排障用 |
| 游戏内 bridge | `plugin/SlimeRancher2Bridge/BridgeServer.cs` | 原始 TCP HTTP（不用 HttpListener，规避 URL ACL）、token 鉴权、request_id 幂等、**主线程泵** |
| 游戏内工具 | `plugin/SlimeRancher2Bridge/Tools*.cs` | 每个 `sr2_xxx` 一个 handler；P0 为文件/元数据，P1 起读游戏状态 |
| 配置 | `config/bridge.json` | host/port/token/超时/日志路径（游戏 mod 与 MCP 共用同一份） |
| 构建 | `plugin/SlimeRancher2Bridge/build.ps1` | 定位 MelonLoader 的 dll + `Il2Cpp*.dll`，`dotnet build`，`-Deploy` 复制到 `<游戏>\Mods\` |

## 2. 协议（与 1 代一致）

```
GET  /health                       → 静态快照（不依赖主线程，游戏卡住也能回）
POST /rpc   {"tool":"sr2_xxx","args":{...},"request_id":"可选"}
Header: X-Bridge-Token: <token>
```

成功：

```json
{"ok":true,"bridge":"0.1.0","request_id":"...","cached":false,"elapsed_ms":3,"data":{...}}
```

失败：

```json
{"ok":false,"bridge":"0.1.0","request_id":"...","error":{"code":"UNKNOWN_TOOL","message":"..."}}
```

约定：

- 工具前缀 `sr2_`（与 1 代 `sr_` 区分；两套东西可以同时开）；
- 端口 **18762**（1 代 18760，避让）；
- 写工具必须带 `dry_run` + `request_id`，规则与 1 代相同；
- 所有 Unity API 只允许在 `Pump()`（主线程 `OnUpdate`）里调用；P0 的文件类工具在请求线程执行（标 `mainThread:false`）。

## 3. 为什么不用 HttpListener / 为什么不装 Starlight 也能跑

- `HttpListener` 绑定非管理员前缀在 Windows 上可能被 URL ACL 拒绝；1 代用 `TcpListener` + 手写 HTTP，本机已验证，故沿用。
- Starlight 是「必备 mod」（菜单/控制台），不是依赖库；本项目的桥接插件独立运行，只要 MelonLoader 在。
- 但 **MelonLoader 首次启动**会生成 `MelonLoader\Il2CppAssemblies\Il2Cpp*.dll`，这是 P1 强类型工具编译的前提。

## 4. 线程与生命周期

```
MelonLoader 加载插件
  └─ OnInitializeMelon: 读 config → new BridgeServer → Start()（后台 accept 线程）
  └─ OnUpdate（每帧）: server.Pump(8) —— 执行主线程工具任务
  └─ OnApplicationQuit: server.Stop()
```

- 请求超时 `request_timeout_ms`（默认 9000）；超时不取消任务，可用同一 `request_id` 回读结果（幂等缓存 TTL 600s）。
- 游戏未进入存档时，MCP 侧应看到 `game_loaded=false`（P1 起），P0 的 health 只反映加载器/文件层。

## 5. 加一个工具要改两处（与 1 代相同）

1. `plugin/.../Tools*.cs`：写 handler + 在 `RegisterTools()` 里 `srv.Register("sr2_xxx", Handler, mainThread:true/false)`；
2. `mcp/tools.json`：加一条定义（name/title/description/args/kind）。

改完 `build.ps1 -Deploy`（游戏需退出），重启游戏。

## 6. 写工具的返回语义与核对清单（2026-11-22 实战补充）

桥接的写工具**「没做成」时大多仍返回 `ok=true`**。只看有没有抛异常一定会误报成功。实测踩到三类：

| 现象 | 根因 | 正确做法 |
| --- | --- | --- |
| 响应 `ok=true`，实际一件没动 | 目标满格时 `sr2_store` 仍返回 `available=true`，只是 `moved` 为空、`unplaced` 非空 | 核对 `moved` / `deposited` 是否非空 |
| 报 `REQUEST_ID_CONFLICT` | 多笔动作复用了同一个 `request_id`，命中幂等缓存后判定「同 id 不同参数」 | **每一笔动作生成独立 `request_id`**（如 `<批次>-<物品>`） |
| 报「余 7 放不下」但实际全放进去了 | `moved_count` / `deposited_count` 是**条目数**，不是件数 | 件数要累加 `moved[]` / `deposited[]` 里每行的 `moved` / `deposited` |

由此形成的核对清单：

1. **写前读**：先读来源与目标（背包、容器槽位、容量）。
2. **预演校验身份**：`dry_run` 不只看 `available`，还要看 `plan` 里的物品 id/名称是否就是目标物品；为空时区分原因（见下）。
3. **执行后回读**：确认实际件数、来源扣减、目标增加三者一致，再向玩家报告。
4. **报告用实际件数**，不是计划件数或条目数。

预演失败要区分四类原因，不能用一句「编号漂移」糊过去：

| 原因 | 含义 | 处理 |
| --- | --- | --- |
| `bad_from` | 编号指向的容器不对 | 重读快照后重试 |
| `wrong_container` | 容器对了但计划里不是这个物品 | 重读快照后重试 |
| `no_space` | 容器身份正确，目标仓库没有兼容空间 | **重读没用**，换目标容器或报告玩家 |
| `empty` | 没有可搬内容 | 直接结束 |

## 7. 容器类型决定「能不能远程操作」

| 容器 | 数据来源 | 远程读 | 远程写 | 远程卖 |
| --- | --- | --- | --- | --- |
| 地块筒仓 `plot_silo` | 存档模型 `GameModel.landPlots` | ✅ | ✅ | ✅ |
| 结晶收集器等 `scene_storage` | 场景对象 `FindObjectsByType<SiloStorage>` | ❌ | ❌ | ❌ |

推论：

- 想「之后随时随地远程卖」，必须在收集器**已加载时**先把结晶搬进地块筒仓（`ranch.py plorts`）。
- `sr2_transfer` 的 `from`/`to` 编号按「到玩家距离」排序，玩家走动会翻转；收集器又没有 `storage_key` 可用来稳定定位。所以脚本必须在同一进程内「读快照 → 逐笔预演校验 → 执行」，不要分两次调用。
- 画面区域未加载时，`sr2_landing_check` 返回 `ground_found=false`，传送会被拒绝 —— 不能拿传送当「回到加载区」的绕行手段。

## 8. 目录

```
E:\SlimeRancher2MCP
├─ config\bridge.json          # 共享配置（token 是凭据，不外发）
├─ mcp\server.py tools.json bridge_client.py
├─ plugin\SlimeRancher2Bridge\ # C# (net6.0) MelonLoader 插件
├─ tool\bridge_cli.py install_melonloader.ps1 init_config.ps1
├─ toolchain\                  # 项目内 .NET SDK + MelonLoader 安装包缓存（不进 git）
├─ out\bridge\                 # mod.log / server.log
└─ docs\
```
