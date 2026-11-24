import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import apply_runtime_patches as cli


class RuntimeCliTests(unittest.TestCase):
    def run_cli(self, arguments):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(cli, 'OUTPUT', Path(temporary)), \
             patch.object(sys, 'argv', ['trainer', *arguments]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = cli.main()
            logs = [json.loads(line) for line in (Path(temporary) / 'logs/runtime_patches.jsonl').read_text().splitlines()]
            return result, json.loads(output.getvalue()), logs

    def test_invalid_xp_blocked_before_process_access_and_logged(self):
        with patch.object(cli, 'game_pids') as pids:
            result, report, logs = self.run_cli(['--xp', '101', '--apply'])
            pids.assert_not_called()
        self.assertEqual(result, 1)
        self.assertIn('between 1 and 100', report['error'])
        self.assertEqual(logs[-1]['status'], 'error')

    def test_write_requires_explicit_apply(self):
        with patch.object(cli, 'game_pids') as pids:
            result, report, logs = self.run_cli(['--skill', 'on'])
            pids.assert_not_called()
        self.assertEqual(result, 1)
        self.assertIn('--apply', report['error'])

    def test_no_game_failure_is_logged(self):
        with patch.object(cli, 'game_pids', return_value=[]):
            result, report, logs = self.run_cli(['--status'])
        self.assertEqual(result, 1)
        self.assertIn('found 0', report['error'])
        self.assertEqual(report, logs[-1])

    def test_pending_currency_rejected_before_scan_or_backup(self):
        executable = json.loads((cli.ROOT / 'trainer_config.json').read_text())['expected_executable']
        with patch.object(cli, 'game_pids', return_value=[123]), \
             patch.object(cli, 'Reader') as reader_type, \
             patch.object(cli, 'verified_hash', return_value=cli.VERIFIED_SHA256), \
             patch.object(cli, 'modules', return_value=[dict(path=executable, base=1)]), \
             patch.object(cli, 'state', return_value=dict(currency_bonus=dict(cash_pending=100))), \
             patch.object(cli, 'locate') as locate, \
             patch.object(cli, 'backup_saves') as backup:
            reader_type.return_value.__enter__.return_value.identity.return_value = dict(pid=123)
            result, report, _ = self.run_cli(['--cash-add', '100', '--apply'])
            locate.assert_not_called()
            backup.assert_not_called()
        self.assertEqual(result, 1)
        self.assertIn('already pending', report['error'])
