import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.engine import Engine
from app.power import interpret_power
from app.storage import ROOT, Store


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.store = Store(Path(self.temp.name))
        runtime = self.store.root / 'runtime/stable-diffusion.cpp'
        runtime.mkdir(parents=True)
        (runtime / 'component-build.json').write_text(json.dumps({'individual_preload_supported': True}))
        settings = self.store.settings()
        for key in ('diffusion_model', 'text_encoder', 'vae', 'vision'):
            file = self.store.root / (key + '.fake')
            file.write_bytes(b'fixture only')
            settings[key] = str(file)
        self.store.save_settings(settings)
        self.sampler = patch('app.engine.power_snapshot', return_value=interpret_power(1, 8, 90))
        self.sampler.start()
        self.engine = Engine(self.store)

    def tearDown(self):
        self.engine.close()
        self.engine.worker.join(timeout=3)
        self.sampler.stop()
        self.temp.cleanup()

    def wait_operation(self):
        deadline = time.monotonic() + 3
        while self.engine.component_operation and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNone(self.engine.component_operation)

    def test_component_bootstraps_lazy_context_and_only_posts_selected_component(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        def load(settings, **kwargs):
            self.assertIs(kwargs['eager_override'], False)
            self.engine.process = process
            self.engine.loaded_settings = settings
        info = {'vae': {'tensors': 10, 'loaded_tensors': 10}, 'diffusion_model': {'tensors': 20, 'loaded_tensors': 0}}
        with patch.object(self.engine, '_load', side_effect=load), \
             patch.object(self.engine, 'request', return_value={'components': info}) as request:
            self.engine.operate_component('vae', 'load')
            self.wait_operation()
            request.assert_called_once_with('POST', '/sdcpp/v1/models/components/vae/load', timeout=1800)
            self.assertEqual(self.engine.component_info, info)
            self.assertEqual(self.engine.state, 'ready')

    def test_existing_context_is_reused_without_stopping_other_components(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        self.engine.process = process
        self.engine.loaded_settings = self.store.settings()
        self.engine.state = 'loading'
        with patch('app.engine.subprocess.Popen') as launch:
            self.engine._load(self.store.settings(), eager_override=False)
            launch.assert_not_called()
        process.terminate.assert_not_called()
        self.assertIs(self.engine.process, process)

    def test_context_ready_during_component_bootstrap_still_blocks_other_operations(self):
        from app.main import Generation
        entered, release = threading.Event(), threading.Event()
        process = Mock(pid=99999999)
        process.poll.return_value = None
        def load(settings, **kwargs):
            self.engine.process = process
            self.engine.loaded_settings = settings
            self.engine.state = 'ready'
            entered.set()
            if not release.wait(3):
                raise AssertionError('bootstrap test timed out')
        with patch.object(self.engine, '_load', side_effect=load), \
             patch.object(self.engine, 'request', return_value={'components': {'vae': {'loaded_tensors': 1}}}), \
             patch('app.engine.model_status', return_value={'blockers': []}):
            self.engine.operate_component('vae', 'load')
            try:
                self.assertTrue(entered.wait(2))
                status = self.engine.status()
                self.assertEqual(status['state'], 'loading')
                self.assertEqual(status['component_operation'], {'key': 'vae', 'action': 'load'})
                for action in (lambda: self.engine.operate_component('diffusion_model', 'load'),
                               lambda: self.engine.preload(self.store.settings()),
                               lambda: self.engine.submit(Generation().model_dump())):
                    with self.assertRaises(ValueError):
                        action()
            finally:
                release.set()
            self.wait_operation()
        self.assertEqual(self.engine.status()['state'], 'ready')
        self.assertIsNone(self.engine.component_operation)
        process.terminate.assert_not_called()

    def test_unload_interrupts_component_http_before_waiting_for_worker(self):
        entered, stopped = threading.Event(), threading.Event()
        process = Mock(pid=99999999)
        process.poll.side_effect = lambda: 0 if stopped.is_set() else None
        process.terminate.side_effect = stopped.set
        self.engine.process = process
        self.engine.loaded_settings = self.store.settings()
        self.engine.state = 'ready'
        def request(*args, **kwargs):
            entered.set()
            if not stopped.wait(3):
                raise AssertionError('unload waited on the HTTP operation before stopping the process')
            raise RuntimeError('native process stopped')
        with patch.object(self.engine, '_load'), patch.object(self.engine, 'request', side_effect=request):
            self.engine.operate_component('vae', 'load')
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            self.engine.unload()
            self.assertLess(time.monotonic() - started, 2)
            self.wait_operation()
        self.assertIsNone(self.engine.process)
        self.assertEqual(self.engine.state, 'idle')
        self.assertIsNone(self.engine.error)
        process.terminate.assert_called_once()

    def test_busy_admission_and_unsupported_runtime_do_not_start_any_process(self):
        with patch('app.engine.subprocess.Popen') as launch:
            self.engine.active = 'existing-generation'
            with self.assertRaises(ValueError):
                self.engine.operate_component('vae', 'load')
            self.engine.active = None
            with patch.object(self.engine, 'component_support', return_value=False):
                with self.assertRaisesRegex(ValueError, '运行库'):
                    self.engine.operate_component('vae', 'load')
            launch.assert_not_called()

    def test_release_uses_existing_context_even_after_settings_changed(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        original = self.store.settings()
        self.engine.process = process
        self.engine.loaded_settings = original
        self.engine.state = 'ready'
        self.store.save_settings(original | {'diffusion_model': 'not-the-loaded-model.gguf'})
        info = {'vae': {'tensors': 10, 'loaded_tensors': 0},
                'diffusion_model': {'tensors': 20, 'loaded_tensors': 20}}
        with patch.object(self.engine, '_load', side_effect=AssertionError('release must not load')), \
             patch.object(self.engine, 'request', return_value={'components': info}) as request:
            self.engine.operate_component('vae', 'release')
            self.wait_operation()
            request.assert_called_once_with('POST', '/sdcpp/v1/models/components/vae/release', timeout=1800)
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.engine.component_info, info)
        self.assertEqual(self.engine.loaded_settings, original)
        self.assertIs(self.engine.process, process)
        process.terminate.assert_not_called()

    def test_generation_reuses_retained_context_after_unrelated_settings_changed(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        original = self.store.settings()
        self.engine.process = process
        self.engine.loaded_settings = original
        self.engine.state = 'ready'
        settings = original | {'output_directory': 'outputs/another', 'assistant_model': 'changed',
                               'battery_protection': False, 'keep_loaded': False, 'eager_load': True}
        with patch('app.engine.subprocess.Popen') as launch:
            self.engine._load(settings)
            launch.assert_not_called()
        self.assertIs(self.engine.process, process)
        process.terminate.assert_not_called()

    def test_component_load_refuses_to_replace_retained_context(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        original = self.store.settings()
        self.engine.process = process
        self.engine.loaded_settings = original
        self.engine.state = 'ready'
        self.store.save_settings(original | {'vae_cpu': not original['vae_cpu']})
        with patch('app.engine.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, '不会重建整套上下文'):
                self.engine.operate_component('vae', 'load')
            launch.assert_not_called()
        self.assertIsNone(self.engine.component_operation)
        self.assertIs(self.engine.process, process)
        process.terminate.assert_not_called()

    def test_assistant_snapshot_uses_actual_component_residency(self):
        from app.assistant import parameter_block
        self.engine.loaded_settings = self.store.settings()
        info = {'vae': {'loaded_tensors': 10}, 'diffusion_model': {'loaded_tensors': 0},
                'text_encoder': {'loaded_tensors': 0}, 'vision': {'loaded_tensors': 0}}
        with patch.object(self.engine, 'components_status', return_value=info):
            snapshot = parameter_block(self.store, self.engine)
        self.assertIn('分别预加载和释放权重', snapshot)
        self.assertIn('保留的组件会复用', snapshot)
        self.assertNotIn('不支持主模型组件独立卸载', snapshot)
        self.assertIn('未加载', next(line for line in snapshot.splitlines() if line.startswith('- 扩散模型：')))
        self.assertNotIn('未加载', next(line for line in snapshot.splitlines() if line.startswith('- VAE：')))

    def test_failed_component_operation_keeps_existing_context(self):
        process = Mock(pid=99999999)
        process.poll.return_value = None
        self.engine.process = process
        self.engine.loaded_settings = self.store.settings()
        self.engine.state = 'ready'
        with patch.object(self.engine, 'request', side_effect=RuntimeError('component load failed')):
            self.engine.operate_component('vae', 'load')
            self.wait_operation()
        self.assertIs(self.engine.process, process)
        process.terminate.assert_not_called()
        self.assertEqual(self.engine.state, 'ready')
        self.assertIn('component load failed', self.engine.error)
