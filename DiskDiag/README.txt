磁盘占用取证与修复工具（本目录 = G:\Codex_mmx\DiskDiag）
==========================================================

2026-11-04 排查结论
--------------------
“电脑在没人操作时大量使用磁盘”的东西已经找到：
  Windows 自带的“优化驱动器/碎片整理”计划任务 ScheduledDefrag
  （C:\Windows\System32\defrag.exe，SYSTEM 身份，空闲时启动、一有操作就中止）
它把 C: 盘当成机械盘反复做碎片整理，每次你回来就被取消，今天已经循环 10 次。
已执行：禁用该计划任务 + 对所有固定盘做一次 TRIM（详见 fix_log.txt / 修复日志）。

本目录文件
----------
DiskWatchOneShot.ps1       一次性磁盘取证程序（占用>50% 触发，采集 5 分钟全部信息）
启动监视.cmd                双击即可后台启动上面的监视程序（输出到本目录 run_*）
修复-停止空闲碎片整理.cmd     手动一键修复（禁用碎片整理任务 + TRIM，需 UAC）
Fix-DisableDefrag.ps1      上面修复脚本实际执行的 PowerShell 版（会被一键脚本调用/也可直接运行）
README.txt                 本说明
run_20261104_155740\       2026-11-04 16:08 抓到“Defrag 导致磁盘忙”的完整现场数据
  - SUMMARY.txt              中文取证报告（先看这个）
  - counter_samples.csv      采集窗口内每秒的全部性能计数器
  - process_samples.csv      每秒的每进程资源/IO
  - processes_T0/mid/end.csv 触发时/中/末的进程快照（含命令行）
  - services_T0/end.csv      服务与 svchost PID 映射
  - cbs_window.log           Windows 组件安装日志片段
  - events_*.csv             采集窗口内的事件日志
  - files_written.csv        窗口内写入过的文件
  - watch_phase.csv          从启动到触发前的磁盘占用记录
  - trigger.json / pre_trigger.csv  触发时刻信息 + 触发前采样
LAST_SUMMARY.txt           最近一次报告副本
LATEST_RUN.txt             最近一次数据目录路径

以后怎么用
----------
1) 想再抓一次“磁盘被谁占用”：双击 启动监视.cmd（默认监视 26 小时，占用>50% 触发），
   采集完成后结果仍会生成在本目录的 run_日期_时间\ 里。
   参数可改：-Threshold 50 -CollectSeconds 300 -WatchHours 26
2) 想恢复 Windows 自动碎片整理（一般不需要）：
   管理员命令行执行
     schtasks /Change /TN "\Microsoft\Windows\Defrag\ScheduledDefrag" /Enable
3) 想手动做 SSD 维护（TRIM，推荐偶尔一次即可）：
   管理员命令行执行
     Optimize-Volume -DriveLetter C -ReTrim -Verbose
4) 目前 ScheduledDefrag 任务已被禁用；Windows 其他自动维护/更新/清理不受影响。

说明
----
- defrag.exe 以 SYSTEM 运行，普通权限的任务管理器看不到它的磁盘 IO，所以当初“找不到是谁”。
- 本目录所有脚本不需要联网；监视程序本身占用极小。
