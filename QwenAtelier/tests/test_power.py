import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app.engine import Engine
from app.main import Generation
from app.power import SystemPowerStatus, interpret_power
from app.storage import ROOT, Store, sha256

AC = interpret_power(1, 8, 80)
BATTERY = interpret_power(0, 1, 79)


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.store = Store(Path(self.temp.name))
        self.sampler = patch('app.engine.power_snapshot', return_value=AC)
        self.sample = self.sampler.start()
        self.engine = Engine(self.store)

    def tearDown(self):
        self.engine.close()
        self.engine.worker.join(timeout=3)
        self.sampler.stop()
        self.temp.cleanup()

    def test_unknown_and_absent_battery_are_not_invented(self):
        raw = SystemPowerStatus(ac=255, flags=255, percent=255)
        self.assertEqual((raw.ac, raw.flags, raw.percent), (255, 255, 255))
        self.assertIsNone(interpret_power(raw.ac, raw.flags, raw.percent)['battery_present'])
        self.assertIsNone(interpret_power(255, 255, 255)['ac_connected'])
        self.assertIsNone(interpret_power(255, 255, 255)['battery_percent'])
        self.assertFalse(interpret_power(255, 255, 255)['on_battery'])
        self.assertFalse(interpret_power(0, 128, 255)['on_battery'])
        self.assertTrue(interpret_power(0, 255, 255)['on_battery'])

    def test_battery_blocks_all_load_and_generation_admission(self):
        self.sample.return_value = BATTERY
        with patch('app.engine.subprocess.Popen') as launch:
            for action in (lambda: self.engine.submit(Generation().model_dump()),
                           lambda: self.engine.preload(self.store.settings()),
                           lambda: self.engine.operate_component('vae', 'load')):
                with self.assertRaisesRegex(ValueError, '电池'):
                    action()
            launch.assert_not_called()

    def test_unknown_reading_keeps_stop_latched_and_ac_does_not_restart(self):
        self.sample.return_value = BATTERY
        self.engine.update_power()
        self.sample.return_value = interpret_power(255, 255, 255)
        self.engine.update_power()
        self.assertTrue(self.engine.power_blocked)
        self.sample.return_value = AC
        self.engine.update_power()
        self.assertFalse(self.engine.power_blocked)
        self.assertIsNone(self.engine.process)
        self.assertTrue(self.engine.pending.empty())
        self.sample.return_value = BATTERY
        self.store.save_settings(self.store.settings() | {'battery_protection': False})
        self.engine._check_power()
        self.assertFalse(self.engine.power_blocked)

    def test_monitor_stops_real_owned_subprocess_without_browser_polling(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.engine.process, self.engine.state = process, 'ready'
        self.engine.loaded_settings = self.store.settings()
        self.sample.return_value = BATTERY
        process.wait(timeout=5)
        deadline = time.monotonic() + 3
        while self.engine.state != 'idle' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIsNone(self.engine.process)
        self.assertEqual(self.engine.state, 'idle')
        self.assertTrue(self.engine.power_blocked)
        self.assertIsNotNone(self.engine.last_power_event)

    def test_unplug_during_preload_cancels_before_any_late_start(self):
        entered = threading.Event()
        def loading(settings, cancelled):
            entered.set()
            if not self.engine.load_cancelled.wait(3):
                raise RuntimeError('test cancellation timed out')
            if cancelled():
                raise InterruptedError('cancelled')
        with patch('app.engine.model_status', return_value={'blockers': []}), \
             patch.object(self.engine, '_load', side_effect=loading):
            self.engine.preload(self.store.settings())
            self.assertTrue(entered.wait(2))
            self.sample.return_value = BATTERY
            self.engine.update_power()
        self.assertEqual(self.engine.state, 'idle')
        self.assertTrue(self.engine.load_cancelled.is_set())
        self.assertIsNone(self.engine.process)

    def test_running_and_queued_jobs_cancel_and_remain_cancelled_after_ac(self):
        entered = threading.Event()
        process = Mock(pid=99999999)
        process.poll.return_value = None
        process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)
        def load(settings, cancelled):
            self.engine.process = process
            self.engine.state = 'ready'
            self.engine.loaded_settings = settings
        def request(method, path, **kwargs):
            if not self.engine.process:
                raise RuntimeError('owned process stopped')
            if method == 'POST':
                entered.set()
                return {'id': 'power-test'}
            return {'status': 'running'}
        with patch('app.engine.model_status', return_value={'blockers': []}), \
             patch.object(self.engine, '_load', side_effect=load), \
             patch.object(self.engine, 'model_hashes', return_value={}), \
             patch.object(self.engine, 'environment_record', return_value={}), \
             patch.object(self.engine, 'request', side_effect=request):
            first = self.engine.submit(Generation(prompt='cup').model_dump())
            self.assertTrue(entered.wait(2))
            second = self.engine.submit(Generation(prompt='vase').model_dump())
            self.sample.return_value = BATTERY
            self.engine.update_power()
            deadline = time.monotonic() + 3
            while (self.engine.active or not self.engine.pending.empty()) and time.monotonic() < deadline:
                time.sleep(.02)
            for job in (first, second):
                final = self.engine.jobs[job['id']]
                self.assertEqual(final['status'], 'cancelled')
                self.assertIn('电池', final['cancel_reason'])
                self.assertEqual(final['image_ids'], [])
            self.sample.return_value = AC
            self.engine.update_power()
            self.assertTrue(self.engine.pending.empty())
            self.assertIsNone(self.engine.process)
        process.terminate.assert_called_once()

    def test_hashing_obeys_cancellation(self):
        path = self.store.root / 'dummy-model'
        path.write_bytes(b'not an actual model')
        with self.assertRaises(InterruptedError):
            sha256(path, cancelled=lambda: True)

    def test_power_change_between_sampling_and_admission_cannot_enqueue(self):
        def unplugged():
            self.engine.power_blocked = True
        with patch.object(self.engine, '_check_power', side_effect=unplugged), \
             patch('app.engine.model_status', return_value={'blockers': []}), \
             patch('app.engine.subprocess.Popen') as launch:
            for action in (lambda: self.engine.submit(Generation().model_dump()),
                           lambda: self.engine.preload(self.store.settings()),
                           lambda: self.engine.operate_component('vae', 'load')):
                self.engine.power_blocked = False
                with self.assertRaisesRegex(ValueError, '电池'):
                    action()
            self.assertEqual(self.engine.jobs, {})
            self.assertTrue(self.engine.pending.empty())
            launch.assert_not_called()

    def test_power_interrupts_component_weight_request(self):
        entered = threading.Event()
        process = Mock(pid=99999999)
        process.poll.return_value = None
        process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)
        def load(settings, **kwargs):
            self.engine.process = process
            self.engine.loaded_settings = settings
        def request(*args, **kwargs):
            entered.set()
            self.engine.load_cancelled.wait(3)
            raise RuntimeError('connection interrupted by protection')
        with patch.object(self.engine, 'component_support', return_value=True), \
             patch('app.engine.model_status', return_value={'blockers': []}), \
             patch.object(self.engine, '_load', side_effect=load), \
             patch.object(self.engine, 'request', side_effect=request):
            self.engine.operate_component('vae', 'load')
            self.assertTrue(entered.wait(2))
            self.sample.return_value = BATTERY
            self.engine.update_power()
        self.assertIsNone(self.engine.process)
        self.assertIsNone(self.engine.component_operation)
        self.assertEqual(self.engine.state, 'idle')
        process.terminate.assert_called_once()

    def test_reconnection_cannot_start_work_until_stop_cleanup_finishes(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        self.engine.process, self.engine.state = process, 'ready'
        self.engine.operation_lock.acquire()
        stopper = None
        try:
            self.sample.return_value = BATTERY
            stopper = threading.Thread(target=self.engine.update_power)
            stopper.start()
            deadline = time.monotonic() + 2
            while not self.engine.power_stopping and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(self.engine.power_stopping)
            self.sample.return_value = AC
            self.engine.update_power()
            self.assertFalse(self.engine.power_blocked)
            self.assertTrue(self.engine.status()['power']['blocked'])
            with self.assertRaisesRegex(ValueError, '电池'):
                self.engine._check_power()
        finally:
            self.engine.operation_lock.release()
            if stopper:
                stopper.join(timeout=3)
        self.assertFalse(self.engine.power_stopping)
        self.assertFalse(self.engine.status()['power']['blocked'])
        self.assertIsNone(self.engine.process)
