# DSH 飞书通知

通过 DSH 原生 MCP 客户端提供两个工具：

- `mcp__feishu__send_message`：向固定飞书群发送一条文本消息，自动添加 `[DSH]` 标记。
- `mcp__feishu__check_config`：检查本地配置，不发送消息。

在 DSH 中输入：`用飞书发送消息：DSH 通知测试。`

只有你要求发送，或已授权某个任务完成后通知时，DSH 才应调用发送工具。此扩展不接收飞书消息，也不自动转发整段聊天。

## 安装位置

- MCP 服务和配置：`F:\deepseekA\integrations\feishu-notify\`
- 当前 Web profile：`F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml`
- 修改前备份：同目录下的 `cordis.patch.yml.before-feishu-时间.bak`

配置保存在本目录 `config.local.json`，字段为 `webhook_url`、`sign_secret`、`keyword`。安装时复用已测试的群机器人配置，后续修改此副本只影响 DSH。不要公开该文件。未把密钥写入 DSH profile 或工具参数。

DSH 的配置热重载可加载新工具；若已打开的会话未显示工具，新建会话检查。这里不自动重启正在运行的 DSH。

## 检查与测试

```powershell
python -B 'F:\deepseekA\integrations\feishu-notify\send_message.py' --config 'F:\deepseekA\integrations\feishu-notify\config.local.json' --check
```

协议验证（默认不发送，检查配置并对消息做 dry-run）：

```powershell
node 'F:\deepseekA\integrations\feishu-notify\verify.mjs'
```

只有明确指定 `--send-test` 时，验证程序才通过 MCP 发送一条测试消息。Webhook 超时或连接中断时送达状态可能未知，不自动重试。

## 回退

从 Web profile 的 patch 中移除 `id: mcp-feishu-notify` 所在的新增 `insert` 段即可卸载工具；如期间没有其他配置变动，也可用安装前备份恢复整个 patch。保留现有其他配置，不需要修改 DSH 程序或依赖版本。
