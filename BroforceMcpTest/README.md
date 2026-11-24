# Broforce AI / MCP 接口独立测试目录

阶段：`0.1-analysis`，2026-11-07。已完成当前本机版本的离线程序集、输入调用链及保存键位分析；尚未实现或注册 MCP 服务，尚未进行游戏内操作测试。

本目录独立于 `G:\Trainer\broforce_life_trainer`。所有分析工具、编译输出、报告和校验记录写入本目录。原修改器源码、成品及其备份保留原位。

## 阅读入口

- [游戏支持与操作按钮](analysis/游戏支持与操作按钮.md)：状态读取能力、按键表、控制入口、接入建议与限制。
- [测试计划](analysis/测试计划.md)：静态验证结果与后续游戏内验收步骤。
- [操作契约草案](contracts/actions.draft.json)：供后续实现参考，`implemented=false`。
- [版本指纹](evidence/game_version.json)、[保存键位](evidence/saved_keybindings.json)、[静态验证](evidence/verification.json)、[文件保留检查](evidence/preservation_check.json)。

## 当前结论

当前游戏是 Unity Mono，Steam App `274190`，构建 `12964083`。已定位读取角色状态及提供角色输入的内部入口，适合后续制作独立桥接，再将有限操作暴露给 MCP。游戏内部方法不等于现成外部 API，仍需在游戏主线程实现桥接和运行时验证。

普通玩家输入经过 `Player.GetInput → InputReader.GetInput`；NPC 可以通过虚方法 `PolymorphicAI.GetInput` 提供动作。原修改器的 `CompanionAI` 已采用后者。接管玩家和指挥 NPC 应分别实现。

当前键盘配置 1 为 A/D 左右、W 跳跃及向上、S 向下、J 射击、K 特殊技能、L 近战/交互、I 手势；Sprint 未绑定。配置 2 使用方向键及小键盘，详见报告。键盘配置编号与当前玩家槽位的对应关系需游戏运行时确认。

## 复现离线分析

依赖本机 Python、Windows 自带 .NET Framework 4.x C# 编译器和已安装的 Broforce。本工具只读取游戏文件，不启动游戏，不加载桥接，不修改安装目录。

在 PowerShell 执行：

```powershell
python -B 'G:\Trainer\broforce_mcp_test\tools\analyze.py'
python -B 'G:\Trainer\broforce_mcp_test\tools\verify.py'
python -B 'G:\Trainer\broforce_mcp_test\tools\preserve.py' verify
```

逐条确认退出码为 0。`analyze.py` 重新提取元数据、IL、键位和版本；`verify.py` 编译接口探针并核对关键证据；`preserve.py verify` 比较分析前的 SHA-256 基线。后续正常游玩、改键或更新原修改器也可能导致保留检查报差异，需要检查差异来源。

`build/InspectAssembly.exe` 是离线读取工具。`build/ApiSurfaceProbe.dll` 仅用于证明选定成员的名称、类型、访问性和签名可以对当前游戏程序集编译；没有运行入口，也不是可安装的游戏插件。编译成功不证明角色实际移动、多人隔离、特殊技能或切关恢复已通过。

## 目录

```text
analysis/   中文分析与验收计划
contracts/  待实现的接口设计
tools/      只在此目录产生产物的离线工具
build/      离线检查器和仅编译的接口探针
evidence/   原始提取、版本指纹、文件基线及检查结果
```

保留检查覆盖原修改器目录中的文件、游戏 Managed 目录 DLL、Saves 目录，以及选定的游戏根文件；不是对整个 Steam 安装库的完整备份。
