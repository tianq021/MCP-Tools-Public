DSH service MCP for this Windows installation

Installed location: F:\deepseekA\integrations\dsh-service
Python: C:\Program Files\Python310\python.exe (mcp 1.30.0 already installed)
Codex MCP name: dsh

Tools:
  status          Read-only loopback TCP and authenticated DSH API check.
  ensure_running  Check first; if connection is refused, run the existing
                  F:\deepseekA\start-dsh.ps1 invisibly and wait up to 45 seconds.
                  Reuse healthy service; serialize starts with a Windows mutex.

Call ensure_running before accessing DSH. Continue only when ready=true.
An occupied port with failed authentication or an unavailable API is reported,
not restarted. No sessions are submitted, stopped or restarted by these tools.
Output excludes authentication tokens, cookies, and session contents.
The wrapper discards startup script output. DSH's authenticated URL stays in
its usual stdout log under F:\deepseekA\logs.

模型使用说明

- Qwen 由学校部署，使用免费。考虑到数据安全，一般只用于普通查询和非敏感数据分析，避免发送敏感信息。
- DeepSeek 官方模型通过付费 API 调用，使用会产生费用。

Setup uses Codex config.toml with a stdio MCP command pointing at server.py.
The launcher starts a local supervisor for its DSH process. It records changes
in authenticated API health and process exits in logs\dsh-watch.log. It relaunches
an exited process with a delay of 2 to 60 seconds. API failures with a live
process are recorded for diagnosis. Use stop-dsh.cmd to stop both processes.
The supervisor runs in the current Windows session. To start DSH after logging
in again, use start-dsh.cmd or call ensure_running.

Manual checks (PowerShell):
  & 'C:\Program Files\Python310\python.exe' -B .\server.py --status
  & 'C:\Program Files\Python310\python.exe' -B .\server.py --ensure-running
  & 'C:\Program Files\Python310\python.exe' -B -m unittest discover -s . -v

After a startup timeout the wrapper leaves processes alone. Inspect existing
DSH logs if repeated calls report an unavailable API or a pending startup.
The integration depends on the existing local DSH launcher and session/list
API; update its probe when a future DSH version changes the API protocol.
