"""Capture the current primary desktop, or send an approved capture to Feishu."""

import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


ROOT = Path(__file__).resolve().parent
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


class ScreenError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError, UnicodeError):
        raise ScreenError("无法读取本地配置，请检查路径和 JSON 格式。") from None


def inspect_image(path, root=ROOT):
    path = Path(path).resolve()
    captures = (root / "captures").resolve()
    if path.parent != captures or path.suffix.lower() != ".png":
        raise ScreenError("只能读取截图目录中的 PNG 文件。")
    try:
        data = path.read_bytes()
    except OSError:
        raise ScreenError("无法读取截图文件。") from None
    if len(data) > 10 * 1024 * 1024:
        raise ScreenError("截图超过 10 MiB，暂不发送。")
    if len(data) < 24 or data[:8] != PNG_HEADER or data[12:16] != b"IHDR":
        raise ScreenError("截图不是有效的 PNG 文件。")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= 32768 and 0 < height <= 32768):
        raise ScreenError("截图尺寸异常。")
    return {"path": str(path), "width": width, "height": height, "mime_type": "image/png"}


def capture():
    try:
        process = subprocess.run(
            [str(ROOT / "CaptureScreen.exe")], capture_output=True,
            encoding="utf-8-sig", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result = json.loads(process.stdout)
        if process.returncode != 0 or result.get("ok") is not True:
            raise ValueError()
        checked = inspect_image(result["path"])
        return {"ok": True, **checked, "captured_at": result["captured_at"], "monitor": "primary", "sent": False}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise ScreenError("截屏失败，请保持 Windows 会话已登录、解锁且桌面可用。") from None


def request_json(url, data, headers, stage):
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
    except HTTPError as error:
        raise ScreenError("{}失败（HTTP {}），未自动重试。".format(stage, error.code)) from None
    except (URLError, OSError, TimeoutError):
        raise ScreenError("{}连接中断或超时，结果未知；未自动重试。".format(stage)) from None
    except (ValueError, UnicodeError):
        raise ScreenError("{}未返回有效结果，结果未知。".format(stage)) from None
    code = result.get("code", result.get("StatusCode"))
    if type(code) is not int or code != 0:
        safe_code = code if type(code) is int else "unknown"
        raise ScreenError("{}被飞书拒绝（错误码 {}）。".format(stage, safe_code))
    return result


def load_feishu(config):
    notify = read_json(config["feishu_notify_config"])
    app = read_json(config["feishu_app_config"]).get("channels", {}).get("feishu", {})
    if not isinstance(app.get("appId"), str) or not isinstance(app.get("appSecret"), str):
        raise ScreenError("现有飞书应用缺少 App ID 或 App Secret。")
    if not app["appId"].strip() or not app["appSecret"].strip():
        raise ScreenError("现有飞书应用凭据为空。")
    import re
    if not isinstance(notify.get("webhook_url"), str) or not re.fullmatch(
        r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+", notify["webhook_url"]
    ):
        raise ScreenError("必须配置飞书官方群机器人 HTTPS Webhook。")
    if any(not isinstance(notify.get(field, ""), str) for field in ("keyword", "sign_secret")):
        raise ScreenError("签名密钥和关键词必须是字符串。")
    return notify, app


def image_post(image_key, notify, timestamp=None):
    title = "电脑屏幕截图"
    if notify.get("keyword"):
        title = notify["keyword"] + " · " + title
    payload = {"msg_type": "post", "content": {"post": {"zh_cn": {
        "title": title, "content": [[{"tag": "img", "image_key": image_key}]]
    }}}}
    if notify.get("sign_secret"):
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        key = (timestamp + "\n" + notify["sign_secret"]).encode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def send_image(image, config):
    checked = inspect_image(image["path"])
    notify, app = load_feishu(config)
    auth = request_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json.dumps({"app_id": app["appId"], "app_secret": app["appSecret"]}).encode("utf-8"),
        {"Content-Type": "application/json"}, "获取图片上传凭证",
    )
    token = auth.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise ScreenError("飞书未返回图片上传凭证。")
    boundary = "screen-" + uuid.uuid4().hex
    body = (
        "--{}\r\nContent-Disposition: form-data; name=\"image_type\"\r\n\r\nmessage\r\n"
        "--{}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"screen.png\"\r\n"
        "Content-Type: image/png\r\n\r\n"
    ).format(boundary, boundary).encode("ascii")
    body += Path(checked["path"]).read_bytes()
    body += ("\r\n--{}--\r\n".format(boundary)).encode("ascii")
    uploaded = request_json(
        "https://open.feishu.cn/open-apis/im/v1/images", body,
        {"Authorization": "Bearer " + token, "Content-Type": "multipart/form-data; boundary=" + boundary},
        "上传截图",
    )
    image_key = uploaded.get("data", {}).get("image_key")
    if not isinstance(image_key, str) or not image_key.startswith("img_"):
        raise ScreenError("飞书未返回有效图片标识，未发送群消息。")
    request_json(notify["webhook_url"], image_post(image_key, notify),
                 {"Content-Type": "application/json; charset=utf-8"}, "发送截图")
    return {**image, "ok": True, "sent": True, "message": "飞书已确认截图消息发送成功。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "send", "check"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--image", type=Path, help="Send an existing capture from this tool's captures directory")
    args = parser.parse_args()
    try:
        if args.action == "check":
            load_feishu(read_json(args.config))
            result = {"ok": True, "sent": False, "message": "截图发送配置有效；未截图、未上传、未发送。"}
        elif args.action == "capture":
            result = capture()
        else:
            # Check credentials before capturing or uploading.
            config = read_json(args.config)
            load_feishu(config)
            image = {"ok": True, **inspect_image(args.image), "sent": False} if args.image else capture()
            result = send_image(image, config)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ScreenError, KeyError) as error:
        message = str(error) if isinstance(error, ScreenError) else "配置缺少必要字段。"
        print(json.dumps({"ok": False, "sent": False, "message": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
