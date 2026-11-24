---
name: feishu-notify
description: Send a user-requested text notification or task result to the configured Feishu group through its custom bot Webhook. Use when the user asks to notify them in Feishu, including a previously authorized notification at task completion. This skill sends messages only; it does not receive remote input or switch ChatGPT or Codex interface modes.
---

# Feishu Notify

Send text with the bundled Python standard-library script. No MCP server or background service is required for this outbound-only workflow.

## Configuration

The local configuration is `G:\Codex_mmx\feishu-notify\config.local.json`:

- `webhook_url`: the custom group bot's Feishu Webhook URL.
- `sign_secret`: its signature secret, if signature verification is enabled; otherwise empty.
- `keyword`: one required bot keyword, if keyword filtering is enabled; otherwise empty. The sender adds it before the message when needed.

Environment variables `FEISHU_WEBHOOK_URL`, `FEISHU_SIGN_SECRET`, and `FEISHU_KEYWORD` override individual configuration values when present.

Do not print the configuration or include the Webhook URL or signing secret in messages, command arguments, or source files. Read credentials only inside the sender. If configuration is missing, ask the user to fill the local file or identify an existing credential file.

## Send

Use the `scripts/send_message.py` beside this skill. Check configuration without sending:

```powershell
python -B scripts/send_message.py --config 'G:\Codex_mmx\feishu-notify\config.local.json' --check
```

Send an approved text file as a single message:

```powershell
python -B scripts/send_message.py --config 'G:\Codex_mmx\feishu-notify\config.local.json' --file 'G:\Codex_mmx\feishu-notify\message.txt'
```

For a short message, `--text 'message'` is also supported. Use an absolute script path when the shell is in another directory. Prefer a UTF-8 file for multiline text rather than interpolating it into shell code.

## Delivery rules

- Send when the user requests it, or when an earlier instruction covers this task's completion notice. Do not forward unrelated chats or enable recurring notifications.
- Summarize the authorized result; include private file contents only when they are part of the requested notification.
- `--dry-run` checks the payload without contacting Feishu. `--check` verifies local configuration only. Neither proves delivery.
- Report delivery only after Feishu returns an explicit success code. A timeout or connection loss can mean delivery is unknown; do not retry automatically and risk a duplicate.
- The sender performs one HTTP request, rejects redirects, and returns sanitized errors. If the bot rejects a keyword, signature, or IP restriction, fix that configuration rather than changing the recipient.

API reference: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
