# 游戏内聊天 watcher：用「游标文件」记录已处理到第几行，避免漏消息。
# 用法：pwsh -NoProfile -File tool\chat_watch.ps1 [-Seconds 14400] [-MinNew 1]
#   -MinNew 1  任意一条新消息就报警（第一层）
#   -MinNew 2  等到第二条新消息才报警（第二层兜底：我处理第一条时它还在守着）
# 行为：条件满足就打印并退出（DSH 会通知 Lead）；否则一直等到超时。
param([int]$Seconds = 14400, [int]$MinNew = 1)
if ($MinNew -lt 1) { $MinNew = 1 }

$inbox = 'E:\SlimeRancher2MCP\out\chat\inbox.jsonl'
$cursorFile = 'E:\SlimeRancher2MCP\out\chat\watcher_cursor.txt'

if (-not (Test-Path $inbox)) { 'NO_INBOX'; exit 0 }

$lines = @(Get-Content $inbox)
$total = $lines.Count

$cursor = $total
if (Test-Path $cursorFile) {
    $raw = (Get-Content $cursorFile -Raw).Trim()
    if ($raw -match '^\d+$') { $cursor = [int]$raw }
}
# 归档/被清空时重置
if ($cursor -gt $total) { $cursor = 0 }

$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    $lines = @(Get-Content $inbox)
    $n = $lines.Count
    if ($n -ge ($cursor + $MinNew)) {
        'NEW_MESSAGES'
        "cursor=$cursor total=$n min_new=$MinNew"
        $lines | Select-Object -Skip $cursor -First 12
        exit 0
    }
    Start-Sleep -Milliseconds 350
}
'TIMEOUT_NO_MESSAGE'
