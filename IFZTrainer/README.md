# Infection Free Zone 修改器项目

目标游戏安装目录：`F:\SteamLibrary\steamapps\common\Infection Free Zone`

当前版本提供“向总部添加资源”、四个地图显示功能，以及八个稳定开关：快速建造、快速研究、免费研究、快速搜刮、无限弹药、无饥饿、完美心情和完美天气。资源选择列表现有 45 项，覆盖基础物资、任务资源、武器和防具。可继续接入的原生方法见 `可用功能代码.md`。

## 根目录文件

- `mono_injector.py`：64 位 Unity Mono 托管桥接加载器；
- `process_memory.py`：通用 Win32 进程内存读写工具，仅作为研究/回退路径；
- `managed\InfectionFreeZoneBridge.cs`：在 Unity 主线程调用游戏资源方法；
- `trainer_gui.py`：资源选择、数量校验、连接和状态界面；
- `build.ps1`：编译 Bridge、运行测试并打包 EXE；
- `inspect_ifz.ps1`：列出当前构建公开的游戏命令和资源 ID；
- `IFZ_修改器分析.md`：技术路线、已确认入口和开发顺序。
- `可用功能代码.md`：已接入及待选的游戏原生功能入口。
- `使用说明.md`：修改器操作步骤、仓库容量限制和常见问题。

运行只读检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\inspect_ifz.ps1
```

构建修改器：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

运行生成的 `dist\Infection_Free_Zone_资源修改器.exe`，启动游戏并进入存档后点击“连接 / 重新注入”，再选择资源和数量。可以点击“添加到总部”，也可以在游戏窗口位于前台时按 `F6` 添加当前所选资源。修改器不会自行启动游戏，也不会改写 Steam 文件。

> 注意：资源添加遵守游戏原有的仓库容量限制。如果本次添加会使总部库存超过仓库上限，游戏不会添加这批资源，也不会自动只填满剩余容量。详细说明见 [`使用说明.md`](使用说明.md)。
