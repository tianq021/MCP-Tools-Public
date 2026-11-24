"""Send one Feishu custom-bot text message without exposing credentials."""

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NotifyError(Exception):
    """A sanitized error safe to show to the caller."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_config(path):
    config = {}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise NotifyError("无法读取配置，请检查文件权限和 JSON 格式。") from None
        if not isinstance(config, dict):
            raise NotifyError("配置必须是 JSON 对象。")
    for key, env in (
        ("webhook_url", "FEISHU_WEBHOOK_URL"),
        ("sign_secret", "FEISHU_SIGN_SECRET"),
        ("keyword", "FEISHU_KEYWORD"),
    ):
        value = os.environ.get(env, config.get(key, ""))
        if not isinstance(value, str):
            raise NotifyError("配置中的 Webhook、签名密钥和关键词必须是字符串。")
        config[key] = value.strip()
    if not config["webhook_url"]:
        raise NotifyError("尚未配置飞书群机器人的 webhook_url。")
    if not re.fullmatch(
        r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+",
        config["webhook_url"],
    ):
        raise NotifyError("Webhook 必须是飞书官方 HTTPS 群机器人地址。")
    return config


def make_payload(text, config, timestamp=None):
    if not text.strip():
        raise NotifyError("消息内容不能为空。")
    keyword = config["keyword"]
    if keyword and keyword not in text:
        text = keyword + "\n" + text
    payload = {"msg_type": "text", "content": {"text": text}}
    if config["sign_secret"]:
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        key = (timestamp + "\n" + config["sign_secret"]).encode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(
            hmac.new(key, b"", hashlib.sha256).digest()
        ).decode("ascii")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Leave headroom under the custom bot's message-size limit.
    if len(body) > 18000:
        raise NotifyError("单条消息过长，请先缩短至约 18 KB 以内；不会自动拆分发送。")
    return body


def send(config, body, timeout=15):
    request = Request(
        config["webhook_url"],
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(65537)
    except HTTPError as error:
        raise NotifyError(
            "飞书 HTTP 请求失败（状态码 {}），未自动重试。".format(error.code)
        ) from None
    except (URLError, TimeoutError, OSError):
        raise NotifyError("网络连接失败或超时，送达状态未知；未自动重试。") from None
    if len(raw) > 65536:
        raise NotifyError("飞书返回异常响应，送达状态未知。")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise NotifyError("飞书未返回有效 JSON，送达状态未知。") from None
    if not isinstance(result, dict):
        raise NotifyError("飞书返回格式异常，送达状态未知。")
    code = result.get("code", result.get("StatusCode"))
    if type(code) is not int:
        raise NotifyError("飞书未返回明确的结果码，送达状态未知。")
    if code != 0:
        reason = {
            19021: "签名或时间戳校验失败",
            19022: "机器人 IP 白名单校验失败",
            19024: "机器人关键词校验失败",
        }.get(code, "请检查机器人配置和消息限制")
        raise NotifyError("飞书拒绝消息（错误码 {}）：{}。".format(code, reason))
    return {"ok": True, "message": "飞书已确认消息发送成功。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=Path(os.environ.get("FEISHU_NOTIFY_CONFIG", Path(__file__).resolve().parent.parent / "config.local.json")),
    )
    parser.add_argument("--check", action="store_true", help="Validate local configuration only")
    parser.add_argument("--dry-run", action="store_true", help="Validate without sending")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text")
    source.add_argument("--file", type=Path)
    source.add_argument("--stdin", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.check:
            result = {"ok": True, "message": "本地配置有效；尚未发送消息。", "signed": bool(config["sign_secret"])}
        else:
            if args.file:
                try:
                    text = args.file.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError):
                    raise NotifyError("无法读取 UTF-8 消息文件。") from None
            elif args.stdin:
                text = sys.stdin.read()
            elif args.text is not None:
                text = args.text
            else:
                raise NotifyError("请提供 --text、--file 或 --stdin，或使用 --check。")
            body = make_payload(text, config)
            result = (
                {"ok": True, "message": "消息校验通过；未发送。", "bytes": len(body)}
                if args.dry_run else send(config, body)
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except NotifyError as error:
        print(json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
