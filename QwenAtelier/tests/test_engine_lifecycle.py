import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app import engine as engine_module
from app.engine import Engine
from app.storage import ROOT, Store


class EngineLifecycleTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.store = Store(Path(self.temp.name))
        models = Path(self.temp.name) / 'models'
        models.mkdir()
        for name in ('diffusion.gguf', 'text.gguf', 'vae.safetensors', 'vision.gguf'):
            (models / name).write_bytes(b'\0' * 16)
        self.store.save_settings(self.store.settings() | {
            'diffusion_model': str(models / 'diffusion.gguf'),
            'text_encoder': str(models / 'text.gguf'),
            'vae': str(models / 'vae.safetensors'),
            'vision': str(models / 'vision.gguf'),
        })
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.stopping.set()
            engine.worker.join(timeout=2)
            engine.power_worker.join(timeout=2)
        self.temp.cleanup()

    def build(self):
        engine = Engine(self.store)
        self.engines.append(engine)
        return engine

    def write_registry(self, pid=4242, port=7999):
        exe = str(ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe')
        (self.store.data / 'engine.json').write_text(json.dumps(
            {'pid': pid, 'port': port, 'exe': exe, 'started': time.time(),
             'settings': self.store.settings()}), encoding='utf-8')
        return exe

    def test_restart_adopts_running_engine_and_never_kills_it_on_close(self):
        exe = self.write_registry()
        with patch.object(engine_module, 'pid_alive', return_value=True), \
             patch.object(engine_module, 'process_path', return_value=exe), \
             patch.object(Engine, 'request', return_value={'state': 'ready'}):
            engine = self.build()
            self.assertTrue(engine.adopted)
            self.assertEqual(engine.pid, 4242)
            self.assertEqual(engine.url, 'http://127.0.0.1:7999')
            status = engine.status()
            self.assertEqual(status['state'], 'ready')
            self.assertTrue(status['loaded'])
            self.assertEqual(status['pid'], 4242)
            engine.close()
            # Closing the web service must keep the engine and the model alive.
            self.assertEqual(engine.pid, 4242)
            self.assertTrue(self.store.data.joinpath('engine.json').is_file())
            self.assertTrue(json.loads(self.store.data.joinpath('engine.json').read_text(encoding='utf-8'))['pid'])

    def test_stale_registry_is_dropped_without_killing_anything(self):
        self.write_registry(pid=4242)
        with patch.object(engine_module, 'pid_alive', return_value=False), \
             patch.object(engine_module, 'terminate_pid') as kill:
            engine = self.build()
            self.assertIsNone(engine.pid)
            self.assertFalse(engine.adopted)
            kill.assert_not_called()
        self.assertIsNone(json.loads(self.store.data.joinpath('engine.json').read_text(encoding='utf-8')).get('pid'))

    def test_unload_ends_an_adopted_engine_by_pid(self):
        exe = self.write_registry()
        with patch.object(engine_module, 'pid_alive', return_value=True), \
             patch.object(engine_module, 'process_path', return_value=exe), \
             patch.object(engine_module, 'resource_snapshot', return_value={'memory': {'process_rss': 1, 'process_private': 2}, 'gpu': {'process_used': 3}}), \
             patch.object(engine_module, 'terminate_pid', return_value=True) as kill, \
             patch.object(Engine, 'request', return_value={'state': 'ready'}):
            engine = self.build()
            engine.unload()
            kill.assert_called_with(4242, timeout=10)
            self.assertIsNone(engine.pid)
            self.assertEqual(engine.state, 'idle')
            self.assertTrue(engine.last_release['pid'] == 4242)

    def test_preload_reuses_loaded_context_instead_of_restarting(self):
        exe = self.write_registry()
        with patch.object(engine_module, 'pid_alive', return_value=True), \
             patch.object(engine_module, 'process_path', return_value=exe), \
             patch.object(Engine, 'request', return_value={'state': 'ready'}), \
             patch.object(Engine, '_load') as load:
            engine = self.build()
            result = engine.preload(self.store.settings())
            self.assertTrue(result['reused'])
            load.assert_not_called()
            self.assertEqual(engine.state, 'ready')

    def test_spawn_is_detached_and_recorded(self):
        process = Mock(pid=5150)
        process.poll.return_value = None
        with patch('app.engine.ROOT', self.store.root), patch('app.engine.subprocess.Popen', return_value=process) as launch, \
             patch.object(Engine, 'request', return_value={'state': 'ready'}):
            engine = self.build()
            engine._load(self.store.settings())
            flags = launch.call_args.kwargs['creationflags']
            for required in (engine_module.spawn_flags(), 0x01000000):
                self.assertTrue(flags & required, f'missing flag {required:#x}')
            record = json.loads(self.store.data.joinpath('engine.json').read_text(encoding='utf-8'))
            self.assertEqual(record['pid'], 5150)
            self.assertEqual(record['port'], int(engine.url.rsplit(':', 1)[-1]))
            self.assertTrue(record['settings']['diffusion_model'])

    def test_resume_closes_an_engine_that_does_not_answer(self):
        exe = self.write_registry()
        calls = {'n': 0}

        def request(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                return {'state': 'ready'}
            raise RuntimeError('no response after resume')

        with patch.object(engine_module, 'pid_alive', return_value=True), \
             patch.object(engine_module, 'process_path', return_value=exe), \
             patch.object(engine_module, 'terminate_pid', return_value=True) as kill, \
             patch.object(Engine, 'request', side_effect=request):
            engine = self.build()
            engine.after_resume()
            kill.assert_called_with(4242, timeout=10)
            self.assertTrue(engine.resume_lost)
            self.assertEqual(engine.state, 'error')
            self.assertIn('休眠', engine.error)

    def test_unresponsive_existing_engine_keeps_ownership_and_blocks_new_work(self):
        exe = self.write_registry()
        with patch.object(engine_module, 'pid_alive', return_value=True), \
             patch.object(engine_module, 'process_path', return_value=exe), \
             patch.object(Engine, 'request', side_effect=RuntimeError('disconnected')):
            engine = self.build()
            self.assertEqual(engine.pid, 4242)
            self.assertTrue(engine.resume_lost)
            self.assertIsNotNone(engine.registry.read())
            with self.assertRaises(ValueError):
                engine.submit_image_tool('upscale', 'id', {}, {})

    def test_restart_waits_for_cancelled_but_still_running_native_job(self):
        exe = self.write_registry()
        self.store.save_job(dict(id='interrupted', status='cancelled', native_id='native-1', native_pending=True,
                                 config={}, image_ids=[], created=time.time()))
        entered, finished = threading.Event(), threading.Event()
        def request(method, path, **kwargs):
            if path.endswith('/models/status'):
                return {'state':'ready'}
            entered.set()
            return {'status': 'completed' if finished.is_set() else 'running'}
        with patch.object(engine_module, 'pid_alive', return_value=True), \
             patch.object(engine_module, 'process_path', return_value=exe), \
             patch.object(Engine, 'request', side_effect=request):
            engine = self.build()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(engine.draining, 'recovery')
                with self.assertRaises(ValueError):
                    engine.submit_image_tool('upscale', 'id', {}, {})
                with self.assertRaises(ValueError):
                    engine.preload(self.store.settings())
            finally:
                finished.set()
            deadline = time.monotonic() + 3
            while engine.draining and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertIsNone(engine.draining)
            self.assertFalse(self.store.jobs()[0]['native_pending'])

    def test_image_tool_history_is_filtered_before_the_50_job_window(self):
        engine = self.build()
        now = time.time()
        self.store.save_job(dict(id='upscale-old', status='completed', created=now - 100, task_type='upscale'))
        for index in range(55):
            self.store.save_job(dict(id=f'gen-{index}', status='completed', created=now + index, task_type='generation'))
        tools = engine.list_jobs(('upscale', 'outpaint'))
        self.assertEqual([job['id'] for job in tools], ['upscale-old'])
        # The main generation list still applies its own 50-item window.
        self.assertNotIn('upscale-old', [job['id'] for job in engine.list_jobs()])

    def test_temporary_status_error_does_not_release_gpu_drain(self):
        engine = self.build()
        entered, release = threading.Event(), threading.Event()
        job = {'native_id':'native-1', 'native_pending': True}
        def status(*args, **kwargs):
            entered.set()
            if not release.is_set():
                raise RuntimeError('network interrupted')
            return {'status':'completed'}
        with patch.object(engine, '_engine_alive', return_value=(True, 4242)), \
             patch.object(engine, 'request', side_effect=status):
            thread = threading.Thread(target=engine._drain_job, args=(job,))
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(job['native_pending'])
                self.assertEqual(engine.draining, 'native-1')
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(job['native_pending'])
            self.assertIsNone(engine.draining)


if __name__ == '__main__':
    unittest.main()
