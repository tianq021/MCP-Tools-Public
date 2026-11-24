import sys
from pathlib import Path
import threading
import time
import tkinter as tk
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer_gui import TrainerApp, request_for, readable_error


class DesktopTests(unittest.TestCase):
    def test_custom_xp_and_independent_restore(self):
        for amount in ('1', '2', '3', '4', '5', '7', '100'):
            args = request_for('xp', amount)
            self.assertEqual(args.xp, int(amount))
            self.assertTrue(args.apply)
            self.assertIsNone(args.skill)
            self.assertEqual((args.backpack_add, args.cash_add, args.eridium_add), (0, 0, 0))
            self.assertFalse(args.cancel_currency)
        for amount in ('', '0', '-2', '101', '2.5', '*3', 'abc'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                request_for('xp', amount)
        args = request_for('xp_off', 'invalid input is ignored')
        self.assertEqual(args.xp, 1)
        self.assertIsNone(args.skill)
        self.assertFalse(args.cancel_currency)

    def test_xp_ui_applies_custom_value_and_restores(self):
        root = tk.Tk()
        root.withdraw()
        calls = []
        def runner(args):
            calls.append(args)
            return dict(status='success', after=dict(kill_xp_multiplier=dict(variant=args.xp)))
        app = TrainerApp(root, runner=runner, auto_connect=False)
        try:
            app.xp_amount.set('7')
            for action, expected in [('xp', 7), ('xp_off', 1)]:
                app.start(action)
                deadline = time.monotonic() + 4
                while app.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                self.assertFalse(app.busy)
                self.assertEqual(calls[-1].xp, expected)
                self.assertIn(f'{expected} 倍', app.xp_status.get())
            app.xp_amount.set('101')
            app.start('xp')
            self.assertFalse(app.busy)
            self.assertEqual(len(calls), 2)
        finally:
            root.destroy()

    def test_explicit_actions(self):
        self.assertFalse(request_for('status').apply)
        self.assertEqual(request_for('backpack', '25').backpack_add, 25)
        self.assertEqual(request_for('restore').xp, 1)
        for amount in ('', '1.5', '-1', '0', '1001'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                request_for('backpack', amount)

    def test_incomplete_restore_has_priority(self):
        text = readable_error(dict(error='found 0', rollback=[dict(restored=False)]))
        self.assertIn('恢复未完成', text)

    def test_worker_blocks_duplicate_actions_and_close(self):
        root = tk.Tk()
        root.withdraw()
        release, calls = threading.Event(), []
        def runner(args):
            calls.append(args)
            release.wait(3)
            return dict(status='success', process=dict(pid=123),
                        after=dict(skill_no_spend=dict(variant='on')))
        app = TrainerApp(root, runner=runner, auto_connect=False)
        try:
            app.start('skill_on')
            app.start('backpack')
            app.close()
            self.assertTrue(root.winfo_exists())
            self.assertTrue(app.busy)
            self.assertTrue(all(str(c['state']) == 'disabled' for c in app.controls))
            release.set()
            deadline = time.monotonic() + 4
            while app.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            self.assertFalse(app.busy)
            self.assertEqual(len(calls), 1)
            self.assertIn('已开启', app.skill.get())
            self.assertTrue(all(str(c['state']) == 'normal' for c in app.controls))
        finally:
            release.set()
            root.destroy()
