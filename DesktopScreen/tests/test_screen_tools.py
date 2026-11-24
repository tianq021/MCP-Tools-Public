import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

spec = importlib.util.spec_from_file_location("screen", Path(__file__).resolve().parents[1] / "screen_tools.py")
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)


class ScreenTests(unittest.TestCase):
    def test_only_capture_directory_pngs_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "captures").mkdir()
            image = root / "captures" / "screen.png"
            image.write_bytes(screen.PNG_HEADER + b"\0\0\0\rIHDR" + struct.pack(">II", 2560, 1440))
            result = screen.inspect_image(image, root)
            self.assertEqual((result["width"], result["height"]), (2560, 1440))
            for candidate in (root / "outside.png", root / "captures" / "other.txt"):
                with self.assertRaises(screen.ScreenError):
                    screen.inspect_image(candidate, root)

    def test_keyword_and_signature_are_in_image_message(self):
        result = json.loads(screen.image_post("img_test", {"keyword": "Codex", "sign_secret": "secret"}, timestamp=1234))
        self.assertIn("Codex", result["content"]["post"]["zh_cn"]["title"])
        self.assertEqual(result["content"]["post"]["zh_cn"]["content"][0][0]["image_key"], "img_test")
        self.assertEqual(result["timestamp"], "1234")
        self.assertTrue(result["sign"])
        self.assertNotIn("secret", json.dumps(result))

    def test_api_needs_explicit_success(self):
        for response in (b'{}', b'{"code":false}', b'{"code":999}', b'[]', b'html'):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = response
            with patch.object(screen, "build_opener", return_value=opener), self.assertRaises(screen.ScreenError):
                screen.request_json("https://open.feishu.cn/test", b"{}", {}, "测试")
            self.assertEqual(opener.open.call_count, 1)

    def test_network_failure_redacts_credentials_and_does_not_retry(self):
        opener = MagicMock()
        opener.open.side_effect = URLError("credential-value")
        with patch.object(screen, "build_opener", return_value=opener), self.assertRaises(screen.ScreenError) as context:
            screen.request_json("https://open.feishu.cn/test", b"{}", {}, "上传截图")
        self.assertNotIn("credential-value", str(context.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_upload_failure_prevents_group_send(self):
        image = {"path": "fake.png"}
        notify = {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/fixed"}
        app = {"appId": "app", "appSecret": "secret"}
        with patch.object(screen, "inspect_image", return_value=image), patch.object(screen, "load_feishu", return_value=(notify, app)), patch.object(screen, "request_json", return_value={"code": 0}) as request:
            with self.assertRaises(screen.ScreenError):
                screen.send_image(image, {})
            self.assertEqual(request.call_count, 1)

    def test_redirects_rejected(self):
        self.assertIsNone(screen.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
