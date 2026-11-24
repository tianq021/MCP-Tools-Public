# 无主之地 4 修改器

更新：2026-10-03。统一运行入口：[BL4Trainer.exe](dist/BL4Trainer.exe)。已合并自选经验倍率和扫描提速。

**当前结论：经验、背包用户确认正常；技能有错误；现金和镒矿没有效果，待修复。**

- [使用说明与完整功能状态](docs/EXE使用说明.md)：操作、限制、配置、备份和日志。
- [开发交接](docs/当前状态与复查交接.md)：实现、验证证据与后续工作。
- [本机环境与存档](docs/本机环境与存档.md)：安装位置和存档基线。
- [只读工具使用](docs/只读工具使用.md)：开发探测工具。
- [货币与经验实现研究](docs/货币与经验实验实现.md)、[掉落物机制研究](docs/掉落物机制分析.md)：历史技术证据，以当前使用说明判定功能状态。

## 构建

使用 Windows x64 Python，在项目目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

构建先执行测试，再输出 `dist/BL4Trainer.exe` 和使用说明；已有发布配置保留。构建验证：`python tools/verify_desktop_build.py`，仅只读连接游戏。

`src/` 为内存读取与补丁实现，`tools/` 为开发工具，`tests/` 为自动测试，`research/` 为研究与验证记录。旧发布版及合并前文档保存在 `archives/2026-10-03-consolidation/`。
