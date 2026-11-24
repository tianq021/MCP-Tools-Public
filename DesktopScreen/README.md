# 查看电脑屏幕与飞书截图

DSH 和 Codex 共用一套 Windows 主显示器截图工具，每次按需截图一次。

## 使用

在 DSH 中说：

- `看一下当前电脑屏幕，告诉我画面里是什么。`
- `截一张当前电脑画面发到飞书。`

DSH 工具：`mcp__desktop__capture_screen`、`mcp__desktop__send_screen_to_feishu`、`mcp__desktop__check_feishu_config`。

在 Codex 中可以说同样的话，或显式使用 `$desktop-screen`。Codex 技能安装于 `C:\Users\hp\.codex\skills\desktop-screen`。查看画面后会读取真实截图，只有明确要求发送时才上传飞书。

## 文件

- 工具安装：`F:\deepseekA\integrations\desktop-screen`
- 截图文件：工具目录下的 `captures`
- 配置：`config.json`，只保存现有配置文件的路径，不复制应用密钥。
- 飞书通知目标：此前已验证的群机器人 Webhook。
- 上传凭据：复用现有 OpenClaw 飞书应用，只用于取得上传凭证和上传图片；不向 OpenClaw 旧聊天发送消息。

截图使用原生显示器像素，Windows 用户会话需要保持登录、解锁和桌面可用。首版只捕获主显示器。截图保留在本地，删除前由用户决定。此功能不控制鼠标键盘，不进行录屏或定时截图。

DSH MCP 支持返回图片；能否理解图片还取决于会话实际选择的模型与接口。当前安装的默认 `deepseek-flash` 目录声明支持图片，但这不等于所有旧会话使用的模型都支持。

## 验证

```powershell
# 不截图、不联网，只检查本地配置
python -B -X utf8 'F:\deepseekA\integrations\desktop-screen\screen_tools.py' check
# 检查 MCP 工具发现与配置
node 'F:\deepseekA\integrations\desktop-screen\verify.mjs'
# 取得截图，通过 DSH 同版 MCP 客户端确认图片字节与尺寸
node 'F:\deepseekA\integrations\desktop-screen\verify.mjs' --capture
```

飞书上传与发送分别检查返回码，凭据不进入工具参数或日志。发送超时可能留下送达未知状态，不自动重试。

## 回退

从 Web profile 的 `cordis.patch.yml` 移除 `id: mcp-desktop-screen` 所在的新增 `insert` 段。安装前的配置备份名称为 `cordis.patch.yml.before-desktop-screen-时间.bak`，只有确认其后没有其他配置修改时才恢复整份备份。
