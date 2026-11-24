import base64
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError


spec = importlib.util.spec_from_file_location("sender", Path(__file__).resolve().parents[1] / "scripts" / "send_message.py")
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)
CONFIG = {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-token", "sign_secret": "", "keyword": ""}


class SenderTests(unittest.TestCase):
    def test_signed_utf8_payload_and_keyword(self):
        config = dict(CONFIG, sign_secret="test-secret", keyword="Codex")
        body = sender.make_payload("测试完成", config, timestamp=1599360473)
        payload = json.loads(body)
        self.assertEqual(payload["content"]["text"], "Codex\n测试完成")
        expected = base64.b64encode(hmac.new(b"1599360473\ntest-secret", b"", hashlib.sha256).digest()).decode("ascii")
        self.assertEqual(payload["sign"], expected)
        self.assertEqual(payload["timestamp"], "1599360473")

    def test_empty_and_oversized_messages_rejected(self):
        for text in (" \n", "中" * 6500):
            with self.assertRaises(sender.NotifyError):
                sender.make_payload(text, CONFIG)

    def test_config_environment_override_and_no_secret_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(CONFIG), encoding="utf-8-sig")
            with patch.dict(os.environ, {"FEISHU_SIGN_SECRET": "secret"}, clear=True):
                self.assertEqual(sender.load_config(path)["sign_secret"], "secret")
                output = io.StringIO()
                with patch("sys.stdout", output), patch.object(sender, "send") as send:
                    self.assertEqual(sender.main(["--config", str(path), "--check"]), 0)
                self.assertNotIn("secret", output.getvalue())
                self.assertNotIn("test-token", output.getvalue())
                send.assert_not_called()

    def test_untrusted_urls_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for url in ("http://open.feishu.cn/open-apis/bot/v2/hook/x", "https://open.feishu.cn.evil.example/open-apis/bot/v2/hook/x", CONFIG["webhook_url"] + "?token=x"):
                path.write_text(json.dumps(dict(CONFIG, webhook_url=url)), encoding="utf-8")
                with patch.dict(os.environ, {}, clear=True), self.assertRaises(sender.NotifyError):
                    sender.load_config(path)

    def mocked_send(self, response_body):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = response_body
        with patch.object(sender, "build_opener", return_value=opener):
            result = sender.send(CONFIG, b"{}")
        self.assertEqual(opener.open.call_count, 1)
        return result

    def test_only_explicit_success_is_accepted(self):
        for body in (b'{"code":0}', b'{"StatusCode":0}'):
            self.assertTrue(self.mocked_send(body)["ok"])
        for body in (b'{}', b'{"code":false}', b'{"code":19024}', b'[]', b'not json'):
            with self.assertRaises(sender.NotifyError):
                self.mocked_send(body)

    def test_network_errors_do_not_leak_url_or_retry(self):
        for error in (URLError(CONFIG["webhook_url"]), TimeoutError(), HTTPError(CONFIG["webhook_url"], 302, "redirect", {}, None)):
            opener = MagicMock()
            opener.open.side_effect = error
            with patch.object(sender, "build_opener", return_value=opener):
                with self.assertRaises(sender.NotifyError) as context:
                    sender.send(CONFIG, b"{}")
            self.assertNotIn("test-token", str(context.exception))
            self.assertEqual(opener.open.call_count, 1)

    def test_redirects_are_blocked(self):
        self.assertIsNone(sender.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
