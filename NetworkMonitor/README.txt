网络流量监测

双击 start.cmd 开始，每 2 秒采样 WLAN 网卡并立即写入 logs/traffic-时间.csv。
双击 stop.cmd 停止后台监测；前台运行也可按 Ctrl+C。
脚本独立运行，退出 Codex 不影响已经独立启动的监测进程。

ReceiveKbps / SendKbps：接收 / 发送速率，单位 Kbps，与任务管理器常见单位一致。
ReceiveBytes / SendBytes：该采样时间段内的字节数。
ElapsedSeconds：实际采样间隔，速率按实际间隔计算。
ReceiveKiBps / SendKiBps：每秒 KiB 数。
16 Kbps = 2000 字节/秒，约 1.953 KiB/秒。

只读取网卡累计计数，不发起测速、ping 或联网查询，不记录数据包内容。
记录的是整个 WLAN 网卡的流量，不是某个程序的独立流量。
CSV 可用 Excel 打开。监测期间查看副本可避免文件占用。
logs/session-时间.json 记录启动时间、进程 PID 和输出文件位置。
持续运行直到停止；长期运行会持续增加日志文件大小。

其他网卡或限时采样示例（PowerShell）：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\monitor.ps1 -AdapterName "以太网" -DurationSeconds 600
