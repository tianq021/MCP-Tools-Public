# 无头游戏窗口可行性测试工具（scripts/headless）

本目录是 **独立测试脚本**，只用于验证“隐藏游戏窗口 / 跳过绘制能否让原版 YR 继续跑真实对局”，
不参与训练、不写 `reports/rl`、不修改原始游戏目录 `E:\hj`，也不修改 `test-game*` 训练副本的配置。

## 测试副本

| 副本 | 用途 | 端口 | 互斥体后缀 | 说明 |
|---|---|---|---|---|
| `E:\RA2MCP\test-game-headless` | 单实例实验（阶段 1/2/4） | 14531 | `...663` / `...5B3` | 由 `test-game` 复制，`patch_multi_instance.ps1 -Index 2` |
| `E:\RA2MCP\test-game-headless-2` | 双隐藏实例（阶段 3） | 14532 | `...664` / `...5B4` | 同上 `-Index 3` |
| `E:\RA2MCP\test-game` / `test-game-1` | 原有训练副本（阶段 0 只读核对） | 14521 / 14522 | 原版 / `...662` | 不修改配置，仅启动核对 |

删除测试副本即可完全回滚；它们不含任何唯一数据。

## 脚本

| 脚本 | 作用 |
|---|---|
| `inspect_session.py` | 诊断：启动、找窗口、进 INGAME、SW_HIDE/SW_SHOW 一次，验证桥接在隐藏后仍响应 |
| `run_experiment.py` | 主实验器：单/双实例，按 `--plan "settle:6,visible:65,hidden:85,show:40"` 切换窗口，逐步记录证据 JSONL，输出 `result.json` |
| `session.py` | 单实例会话：真实游戏启动、规则策略决策、原生指令执行、隐藏/恢复窗口、奖励与状态指标采集 |
| `winapi.py` | Win32 只读封装：枚举窗口、`ShowWindow(SW_HIDE=0/SW_SHOW=5)`、`IsWindowVisible`、进程 CPU/内存 |
| `monitor.ps1` | 资源采样（固定 PID）：Get-Process CPU/WS/Private + typeperf 连续记录 GPU Engine / GPU Process Memory 计数器 |
| `analyze.py` | 从 run 目录计算每阶段资源/吞吐并给出判定，输出 `analysis.json/md` |
| `compare_runs.py` | 多 run 对比（visible / hidden / dual），输出对比 JSON/Markdown |
| `phase0_check.py` | 阶段 0：核对 test-game/test-game-1 的 mutex 补丁、DLL/exe/spawn/ddraw 哈希、端口，并短跑双实例验证 INGAME + 状态变化 |
| `phase4_norender.py` | 阶段 4：在运行中的测试游戏上开关 `DoBlit`/`Render`/`Tactical::Render` 补丁，截图+逻辑+时钟验证 |
| `render_patch.py` | 可恢复的运行时补丁（WriteProcessMemory），保存原始字节并可还原；只针对测试副本进程 |
| `disasm_analysis.py` | 静态分析 gamemd 副本：定位 GameLoopBegin 0x55DE4F 所在主循环、render 调用点、render 函数 |
| `disasm_dump.py` | 反汇编指定 VA 区间（分析用） |
'| `capture.ps1` | 截取窗口矩形到 PNG（证据） |
| `render_off_runtime.py` | 阶段 B 双实例 render-off 稳定性测试器：每实例独立进程生命周期，逐局启动→隐藏→打补丁→决策→恢复校验→杀自己进程→自动下一局；看门狗与诊断落盘 |
| `monitor_dynamic.ps1` | 动态资源采样：按 exe 名发现当前游戏进程（适配逐局重启），CPU/内存 1 Hz + typeperf GPU + 每 5 s 的按 PID GPU/显存采样（`*_gpu_by_pid.csv`） |
| `analyze_render_off.py` | 分析 render-off 稳定性 run（逐局吞吐/读取/动作/资源/清理），并与阶段 3 双隐藏基线对比 |'

## 常用命令

```powershell
# 阶段 1/2：显示→隐藏→恢复
.venv\Scripts\python.exe scripts\headless\run_experiment.py `
    --run-id phase1-aba-02 --plan "settle:6,visible:65,hidden:85,show:40" `
    --decision-seconds 0.5 --rpc-bench-seconds 3

# 阶段 2：整局 visible / hidden 各一局
.venv\Scripts\python.exe scripts\headless\run_experiment.py `
    --run-id phase2-visible-01 --plan "settle:3,visible:0" --max-phase-seconds 360
.venv\Scripts\python.exe scripts\headless\run_experiment.py `
    --run-id phase2-hidden-01 --plan "settle:3,hidden:0" --max-phase-seconds 360

# 阶段 3：两个隐藏实例（独立副本/端口），并验证杀一个不影响另一个
.venv\Scripts\python.exe scripts\headless\run_experiment.py `
    --run-id phase3-dual-01 --instances 2 `
    --game-dirs "E:\RA2MCP\test-game-headless,E:\RA2MCP\test-game-headless-2" `
    --ports "14531,14532" --plan "settle:2,hidden:0" --max-phase-seconds 360 --kill-one-test

# 分析 / 对比
.venv\Scripts\python.exe scripts\headless\analyze.py phase1-aba-02
.venv\Scripts\python.exe scripts\headless\compare_runs.py `
    --run visible=phase2-visible-01 --run hidden=phase2-hidden-01 `
    --run dual=phase3-dual-01 --out reports/headless/compare-phase23

# 阶段 0
.venv\Scripts\python.exe scripts\headless\phase0_check.py

# 阶段 4（含运行时补丁开关与恢复）
.venv\Scripts\python.exe scripts\headless\phase4_norender.py --run-id phase4-01

# 阶段 B：双实例 render-off 稳定性（先 10 分钟验收，再 1 小时连续）
.venv\Scripts\python.exe scripts\headless\render_off_runtime.py `
    --run-id render-off-dual-10min --instances 2 --duration-seconds 600
.venv\Scripts\python.exe scripts\headless\render_off_runtime.py `
    --run-id render-off-dual-1h --instances 2 --duration-seconds 3600
.venv\Scripts\python.exe scripts\headless\analyze_render_off.py render-off-dual-10min

# 正式采样流程中的可选 render-off（默认关闭，见 reports/headless-render-off-integration.md）
.\train.cmd -RenderOff          # 显式开启；不带该开关时保持正常渲染
```

## 关键约束

- 游戏进程只由本工具启动并只被本工具终止；不调用 `kill_games()` 之类会杀全部实例的清理逻辑。
- 隐藏 = `ShowWindow(SW_HIDE)`，**不销毁窗口、不杀渲染线程、不改桌面设置**；恢复 = `ShowWindow(SW_SHOW)`。
- 阶段 4 补丁只写运行中进程内存，脚本在 `finally` 中还原原始字节并在 `result.json` 里保存
  `bytes_before/bytes_after/restored`；游戏退出后补丁自然消失。绝不修改磁盘上的 exe。
- 所有结论都要看 `reports/headless/runs/<run-id>/` 下的 `result.json`、`*-events.jsonl`、
  `monitor.csv`、`gpu_counters.csv`、截图与 `run.log`；不要只读 stdout。
