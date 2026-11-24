import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import run


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reply = {'body': {'app': 'Qwen Atelier'}, 'status': 200}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps(reply['body']).encode()
                self.send_response(reply['status'])
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        cls.reply = reply
        cls.server = HTTPServer(('127.0.0.1', 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.reply.update({'body': {'app': 'Qwen Atelier'}, 'status': 200})

    def test_only_this_application_counts_as_running(self):
        url = f'http://127.0.0.1:{self.port}'
        self.assertTrue(run.service_at(url))
        # Another program on the same port must never be mistaken for this one,
        # otherwise the launcher would open a browser to the wrong page.
        self.reply['body'] = {'app': 'something else'}
        self.assertFalse(run.service_at(url))
        self.reply['body'] = {'detail': 'not found'}
        self.reply['status'] = 404
        self.assertFalse(run.service_at(url))

    def test_unreachable_port_is_not_running(self):
        self.assertFalse(run.service_at('http://127.0.0.1:1', timeout=0.4))

    def test_remembered_url_must_be_loopback(self):
        # Never touch the live address file: a running instance owns it.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'server-url.txt'
            with patch.object(run, 'URL_FILE', path):
                path.write_text('http://example.com:7860', encoding='utf-8')
                self.assertIsNone(run.remembered_url())
                path.write_text('http://127.0.0.1:8123', encoding='utf-8')
                self.assertEqual(run.remembered_url(), 'http://127.0.0.1:8123')
                path.unlink()
                self.assertIsNone(run.remembered_url())


if __name__ == '__main__':
    unittest.main()
