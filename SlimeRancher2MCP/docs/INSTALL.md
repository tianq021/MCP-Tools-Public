# 安装 / 部署 / 回滚

> 所有写操作前先读；本文命令**不自动启动游戏**。游戏必须通过 **Steam** 启动（appid 1657630）。

## 当前部署（2026-11-21）

二代桥接 **0.4.2** 和 SlimeMap 1.0.2 已部署在游戏的 `Mods` 根目录，两个 DLL 均已校验（桥接 41984 B，SHA256 `C917A7B4…`，与 `dist` 一致）。MelonLoader 已恢复为 `version.dll`，首启所需的 Cpp2IL 与 Unity 依赖包已补齐。旧版桥接备份：`backups/SlimeRancher2Bridge-0.4.1.dll`（0.4.2 之前正在使用）、`backups/SlimeRancher2Bridge-0.4.0.dll`、`backups/SlimeRancher2Bridge-before-0.4.0.dll`。下一次 Steam 启动后验证游戏内连通性（重点看 `sr2_health.save_dir_exists` 是否变为 true）。

## 先前安装记录（2026-11-20）

| 项 | 状态 |
| --- | --- |
| 游戏 | `H:\SteamLibrary\steamapps\common\Slime Rancher 2`，buildid 24131940（Unity 6.0.73f1，IL2CPP，**游戏版本 1.3.0**） |
| MelonLoader | ✅ 已安装 0.7.3（`MelonLoader/` + `version.dll`）；⚠️ **2026-11-20 23:00 起临时停用**：`version.dll` 改名为 `version.dll.ml-off`，游戏按原版启动（因为首启 Il2CppAssemblyGenerator 要离线下载 Cpp2IL/Unity 依赖，本机 GitHub 仅 ~3.5 KB/s） |
| .NET 6 运行时 | ✅ 已预置 `<游戏>\dotnet`（Microsoft.NETCore.App **6.0.36**） |
| 桥接插件 | ✅ 已部署 `<游戏>\Mods\SlimeRancher2Bridge\SlimeRancher2Bridge.dll`（28 KB；ML 恢复后才加载） |
| 互操作程序集 | ⏳ 未生成：`Il2CppAssemblyGenerator` 需要 `Cpp2IL.exe`（GitHub 下载，本机极慢）+ Unity 依赖包 |
| 本机存档 | 实际路径 `%USERPROFILE%\AppData\LocalLow\MonomiPark\SlimeRancher2\Steam\00000000000000000\*.sav`（2026-11-21 实测，按 `app.info` 的 company/product 推导；不是 `Monomi Park\Slime Rancher 2`） |

### 恢复/停用 MelonLoader（一行命令）

```powershell
$g='H:\SteamLibrary\steamapps\common\Slime Rancher 2'
# 停用（原版启动）
Rename-Item "$g\version.dll" 'version.dll.ml-off' -Force
# 恢复（启用 mod）
Rename-Item "$g\version.dll.ml-off" 'version.dll' -Force
```

> 恢复 ML 前建议先解决 Cpp2IL 下载：从镜像（如 `https://ghfast.top/https://github.com/SamboyCoding/Cpp2IL/releases/download/2022.1.0-pre-release.21/Cpp2IL-2022.1.0-pre-release.21-Windows.exe`）预下到
> `<游戏>\MelonLoader\Dependencies\Il2CppAssemblyGenerator\Cpp2IL\Cpp2IL.exe`，再启动；ML 还会下载 Unity 依赖（`Unity Dependencies Version = 6000.0.73`），同样可能慢。

## 1. 安装 MelonLoader（必需，写游戏目录）

两种方式，任选其一。安装前先备份：

```powershell
# 备份 game manifest（回滚依据；ML 只增文件、不改 GameAssembly.dll）
Copy-Item 'H:\SteamLibrary\steamapps\appmanifest_1657630.acf' 'E:\SlimeRancher2MCP\backups\appmanifest_1657630.before-ml.acf'
```

### 方式 A：官方安装器（推荐，有 GUI）

1. 下载 https://github.com/LavaGang/MelonLoader/releases/latest/download/MelonLoader.Installer.exe
2. 运行 → Select `Slime Rancher 2`（找不到就手动选 `SlimeRancher2.exe`）→ 版本选 **0.7.3 或更高** → Install
3. 关掉安装器

### 方式 B：手动解压（脚本，可预演）

```powershell
# 预演：只打印将写入的文件
& E:\SlimeRancher2MCP\tool\install_melonloader.ps1

# 真装（解压 toolchain\MelonLoader.x64.zip 到游戏目录）
& E:\SlimeRancher2MCP\tool\install_melonloader.ps1 -Apply
```

安装后游戏目录应出现：`MelonLoader\`、`Mods\`、`dotnet\`、`winhttp.dll`（不同 ML 版本文件名略有差异）。

## 2. 首次启动（用户操作）

**必须由用户通过 Steam 启动**：库 → Slime Rancher 2 → 开始游戏。

首启目标：

1. 确认 MelonLoader 控制台出现、游戏进到主菜单（`MelonLoader\Latest.log` 有日志）；
2. 让 ML 生成互操作程序集：`<游戏>\MelonLoader\Il2CppAssemblies\`（P1 强类型工具需要）；
3. 若本机还没有档，可顺手开一个新档（之后 P1 工具才有数据可读）。

> 桥接插件在首启时若已部署，会在日志里打印 `[SR2Bridge] listening ...`，端口 **18762**。

## 3. 编译 + 部署桥接插件

```powershell
# 编译（-Deploy 会复制 dll 到 <游戏>\Mods\；游戏需退出）
& E:\SlimeRancher2MCP\plugin\SlimeRancher2Bridge\build.ps1 -Deploy
```

部署后**重启游戏**生效（MelonLoader 在启动时加载 `Mods`）。

## 4. 验证通道

```powershell
# 不需要 MCP，直接打 HTTP
python -B E:\SlimeRancher2MCP\tool\bridge_cli.py health
python -B E:\SlimeRancher2MCP\tool\bridge_cli.py call sr2_gameinfo
python -B E:\SlimeRancher2MCP\tool\bridge_cli.py call sr2_saves
```

`BRIDGE_UNAVAILABLE` = 游戏没开 / mod 没加载（正常，不是故障）。

## 5. 接进 DSH（MCP 注册）

在 DSH profile 的 `cordis.patch.yml` 追加（示例见 `docs\DSH_MCP_ENTRY.yml`）：

```yaml
- insert:
    - id: mcp-slime-rancher-2
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: slime_rancher2
        transport: stdio
        command: 'C:\Program Files\Python310\python.exe'
        args: ['-B', 'E:\SlimeRancher2MCP\mcp\server.py']
        cwd: 'E:\SlimeRancher2MCP\mcp'
        toolCallTimeoutMs: 30000
        failOnStartupError: false
        env: { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
```

MCP 工具表在会话启动时读取，**改完要重开 DSH 会话**。

## 6. 回滚

```powershell
# 1) 移除插件
Remove-Item -LiteralPath 'H:\SteamLibrary\steamapps\common\Slime Rancher 2\Mods\SlimeRancher2Bridge.dll'

# 2) 彻底移除 MelonLoader（游戏还原为干净状态）
Remove-Item 'H:\SteamLibrary\steamapps\common\Slime Rancher 2\MelonLoader' -Recurse -Force
Remove-Item 'H:\SteamLibrary\steamapps\common\Slime Rancher 2\dotnet' -Recurse -Force
Remove-Item 'H:\SteamLibrary\steamapps\common\Slime Rancher 2\Mods' -Recurse -Force
Remove-Item 'H:\SteamLibrary\steamapps\common\Slime Rancher 2\winhttp.dll' -Force
# 还原 manifest（Steam 校验后会把游戏文件恢复到该 buildid）
Copy-Item 'E:\SlimeRancher2MCP\backups\appmanifest_1657630.before-ml.acf' 'H:\SteamLibrary\steamapps\appmanifest_1657630.acf' -Force
# Steam → 右键游戏 → 属性 → 已安装文件 → 验证完整性
```

> MelonLoader 不改 `GameAssembly.dll`、不 patch 游戏代码文件（IL2CPP 走运行时注入），回滚风险低；
> 但**存档格式**在装了 mod 后仍由游戏自己写，和原版一致（本项目不碰存档二进制，除非做离线工具）。

## 7. 已知注意

- **不要**双击 `SlimeRancher2.exe` 启动（Steam 未初始化会导致 DLC/成就异常），一律走 Steam；
- 游戏更新（Steam 自动更新）会改 `GameAssembly.dll`/元数据：更新后先确认 ML 仍能启动，再重编插件；
- 同一端口只允许一个监听者：SR1（18760）与 SR2（18762）互不影响。
