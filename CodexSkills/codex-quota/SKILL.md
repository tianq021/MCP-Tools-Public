---
name: "codex-quota"
description: "Check remaining Codex allowance and reset times when asked about quota, limits, 额度, 剩余次数, or reset time, or before long tasks. Supports weekly-only and multiple quota windows."
metadata:
  short-description: "Check remaining Codex quota"
---

# Codex Quota

查询当前 Codex 账户剩余额度，只返回实际存在的窗口及其剩余百分比、重置时间。

## 什么时候用

- 用户问“还剩多少额度 / 额度够不够 / 什么时候重置”。
- 准备开始长时间、多轮、大批量的任务之前，确认实际返回的额度窗口是否还有余量。
- 遇到限流报错，需要查询剩余额度与重置时间。

## 怎么调

1. 调用 MCP 服务器 `codex_quota` 的 `get_codex_quota`，不需要参数。
2. 直接转述返回的额度摘要。周额度单窗口示例：

   `Codex 剩余额度：每周窗口剩余 92%（11-10 22:37 重置）`

3. 工具不可用，或刚更新查询脚本而当前 MCP 进程尚未重新连接时，使用同一脚本的 CLI：

   ```powershell
   $env:CODEX_EXE = 'C:\Users\hp\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe'
   python -B E:\DS_Agents\codex_quota_mcp.py --brief
   ```

## 怎么解读

- 不把 `primary` 固定解释为 5 小时，也不把 `secondary` 固定解释为每周。
- 查询代码优先按 `windowDurationMins` 识别：300 分钟为 5 小时，10080 分钟为每周；其他有效时长按分钟显示。
- 用户当前使用周额度单窗口。只有一个有效额度数值且没有时长信息时，按用户约定解释为每周额度；明确返回时长时，以时长为准。
- 缺少的窗口不补造，也不解释为额度已耗尽。两个窗口均存在但都缺少时长时，保留旧接口的 primary=5 小时、secondary=每周兼容规则。
- 剩余百分比越低，可用额度越少；不使用固定百分比定义“正常”。括号内为本地时间的重置时刻。

## 约束

- 对外只返回剩余百分比与重置时间，不输出账户 ID、计划类型、credit ID 或原始 JSON；不要绕过工具读取原始响应。
- 普通查询使用现成工具或 CLI，不另外写查询脚本或解析日志。
- 查询失败如实说明“额度暂时查不到”，不要猜测数字。
