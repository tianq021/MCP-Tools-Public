# After Inc: Revival 修改器

当前修改器版本 **26049821.16**，Steam App ID **3337140**，主要对应游戏构建 **26049821**。

在「远征与地图」添加关卡时，可以设定每关最终的特性数量、雄心和危险度，或生成带河流和桥的地图。特性选择窗口显示 118 项游戏图标和简体中文效果说明，支持按效果搜索、即时互斥提示和手动检查冲突。详见[本版说明](versions/26049821.16_game26049821/README.md)。

## 自动日志

启动即监控，关闭即停止；每次运行在 EXE 旁 logs 中生成独立目录。正常关闭等待正在进行的修改完成。详见 [日志与自动监控说明](docs/日志与自动监控说明.md)。

## 启动

直接运行 [dist/AfterIncRevivalTrainer_CN.exe](dist/AfterIncRevivalTrainer_CN.exe)。配套游戏组件为 [AfterIncLevelTraits.dll 1.6.2](dist/AfterIncLevelTraits.dll)。首次配置新关卡功能时，将组件放入游戏的 `BepInEx/plugins/AfterIncLevelTraits/` 并重新启动游戏。

经验问题沿用 26049821.3 的修复：通过“升级机会 +1”生成游戏原生升级图标，再由玩家在游戏里选择奖励。详细操作、拒绝码及限制见[战士升级说明](versions/26049821.3_game26049821/README.md)。

## 文件校验

默认 EXE 和配套 DLL 的 SHA-256 均已记录，见[成品校验清单](SHA256SUMS.txt)。PowerShell 校验：`Get-FileHash .\dist\AfterIncRevivalTrainer_CN.exe -Algorithm SHA256`。

## 当前功能

- 资源、耐心、权威修改与锁定，战士快速移动。
- 探索与建设队伍增量、队伍数量保持。
- 战士升级机会、武器 +0.25、小队 +0.25。
- 远征日期刷新、随机任务扩展、自定义雄心和危险度、特性总数与同批去重、随机地块与跨河桥。
- 资源、队伍与升级、远征与地图三页签，以及支持搜索和按图标勾选的特性窗口。

各功能依照 GameAssembly.dll 精确哈希校验支持范围；战士升级仅支持 26049821。Steam App ID、游戏构建号和修改器版本号是不同概念。

## 文件布局

| 位置 | 用途 |
| --- | --- |
| 根目录 Python 文件、native/、level_traits_plugin/、trait_icons/、tests/ | 已合并的 26049821.16 源码、图标与测试 |
| dist/ | 当前默认应用 |
| versions/26049821.16_game26049821/ | 当前版本快照 |
| [versions/archives/](versions/archives/README.md) | 旧版程序 ZIP、版本清单与校验值 |
| versions/history/ | 早期历史应用，每版附功能、游戏号和 SHA-256 |
| versions/3337140_任务扩展版_20261014/ | 先前合并时保留的旧源码，原名称保留 |
| soldier_upgrade_work/ | 经验修复前开发版源码与应用，保留原位置供追溯 |
| docs/history/before_organization_20261015/ | 整理前文档、应用清单和整理后清单 |
| build/、il2cpp_dump/、tools/、scans/、structure_scans/、write_tests/、backups/ | 构建、分析及备份资料，保留原位置 |

完整版本登记见 [versions/README.md](versions/README.md)。根目录其他技术说明及历史复盘保留作背景资料；其中旧路径按版本索引查找，当前状态以本文件及“下一步.txt”为准。

## 开发与打包

使用系统 Python 3.10，在项目根目录执行：

```powershell
python -m unittest discover -s tests
python -m PyInstaller --noconfirm --distpath dist --workpath build/build_26049821_16 AfterIncRevivalTrainer_CN.spec
```

2026-11-23 已将 26049821.16 的源码、图标、EXE 和配套组件合并为默认版本。

历史验证记录（2026-10-24）：26049821.4 修正写入后校验的容错（重试 + 抢占归为瞬时错误），74 项测试通过、打包成功。上一版实机验收记录见 [26049821.3 版本说明](versions/26049821.3_game26049821/README.md)；本版仍需在运行中的游戏里复验人力锁定。旧版 25936576.1 完整保留。
