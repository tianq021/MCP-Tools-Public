import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.image_tools import (alpha_profile, flatten_alpha, geometry, compose_preserved, verify_preserved,
                             image8, OutpaintParams)
from app.storage import ROOT
from app.power import interpret_power


def png(size=(32, 32), color='red', mode='RGB'):
    stream = io.BytesIO()
    Image.new(mode, size, color).save(stream, 'PNG')
    return stream.getvalue()


class ImageToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {'X-Studio-Token': self.client.get('/api/session').json()['token']}
        self.tools = self.app.state.image_tools
        self.engine = self.app.state.engine

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.engine.worker.join(timeout=3)
        self.temp.cleanup()

    def post(self, path, **kwargs):
        return self.client.post('/api/image-tools' + path, headers=self.headers, **kwargs)

    def upload(self):
        result = self.post('/inputs', files={'file': ('sample.png', png(), 'image/png')})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def wait_job(self, identity):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.engine.public(self.engine.jobs[identity])
            if job['status'] in ('completed', 'failed', 'cancelled') and self.engine.active != identity:
                return job
            time.sleep(.02)
        self.fail('worker did not finish')

    @contextmanager
    def available(self):
        with patch.object(self.tools, 'capabilities', return_value={mode: {'available': True, 'verified': True} for mode in ('upscale', 'outpaint')}), \
             patch.object(self.tools, 'require_resources', return_value={'allowed': True}):
            yield

    def request(self, identity, mode='upscale', params=None):
        return self.post('/jobs', json=dict(task_type=mode, input_id=identity, params=params or {}, content_confirmed=True))

    def test_upload_auth_original_and_unverified_submission(self):
        self.assertEqual(self.client.post('/api/image-tools/inputs').status_code, 403)
        item = self.upload()
        self.assertEqual(self.client.get(item['download_url']).content, png())
        self.assertEqual(self.request(item['id']).status_code, 400)
        self.assertEqual(len(self.engine.jobs), 0)
        self.assertEqual(self.post('/inputs', files={'file': ('bad.png', b'bad')}).status_code, 400)

    def test_defaults_and_geometry_do_not_submit_or_load(self):
        item = self.upload()
        with patch.object(self.engine, '_load') as load:
            params = OutpaintParams(extend_left=1, extend_right=0).model_dump()
            result = self.post('/outpaint/preview', json={'input_id': item['id'], 'params': params})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['width'], 64)
            self.assertEqual(result.json()['offset_x'], 1)
            self.assertTrue(result.json()['adjusted'])
            saved = self.client.put('/api/image-tools/defaults/outpaint', json=params, headers=self.headers)
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()['keep_original_pixels'])
            load.assert_not_called()
        self.assertFalse(self.engine.jobs)

    def test_rejects_16bit_and_pixel_limits(self):
        stream = io.BytesIO()
        Image.new('I;16', (2, 2)).save(stream, 'PNG')
        with self.assertRaisesRegex(ValueError, '16 bit'):
            image8(stream.getvalue())
        with self.assertRaises(ValueError):
            geometry((4096, 4096), OutpaintParams().model_dump())
        item = self.upload()
        with self.available():
            self.assertEqual(self.request(item['id'], params={'tile_size': 65}).status_code, 400)
            self.assertEqual(self.request(item['id'], mode='outpaint').status_code, 400)
            self.assertEqual(self.request('../escape').status_code, 422)

    def test_gallery_reference_is_not_copied_and_revoked_after_trash(self):
        folder = self.root / '图片'
        folder.mkdir()
        source = folder / 'sample.png'
        source.write_bytes(png())
        identity = self.app.state.store.register(source)
        result = self.post('/inputs/from-image', json={'image_id': identity})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse((self.tools.data / (result.json()['id'] + '.png')).exists())
        self.app.state.store.trash(identity)
        self.assertEqual(self.client.get(result.json()['preview_url']).status_code, 400)

    def test_output_manifest_and_existing_generation_list_are_separate(self):
        item = self.upload()
        with self.available(), patch.object(self.tools, 'run_upscale', return_value=(Image.new('RGB', (128, 128), 'blue'), {'exit_code': 0})):
            response = self.request(item['id'])
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        manifest = self.client.get(job['result']['manifest_url'])
        self.assertEqual(manifest.status_code, 200)
        self.assertIn('attachment', manifest.headers['content-disposition'])
        self.assertEqual(manifest.json()['input']['sha256'], self.tools.input_path(item['id'])[1]['sha256'])
        self.assertEqual(self.client.get('/api/jobs').json(), [])
        self.assertEqual(len(self.client.get('/api/image-tools/jobs').json()), 1)

    def test_wrong_output_size_fails_without_registering_image(self):
        item = self.upload()
        with self.available(), patch.object(self.tools, 'run_upscale', return_value=(Image.new('RGB', (127, 128)), {})):
            response = self.request(item['id'])
            job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertIsNone(job['result'])
        self.assertFalse(self.app.state.store.images())
        self.assertFalse(list((self.root / 'outputs').rglob('*.png')))

    def test_queue_serializes_and_queued_cancel_never_runs(self):
        item = self.upload()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def execute(job):
            calls.append(job['id'])
            entered.set()
            release.wait(3)
            job['status'] = 'completed'
        with self.available(), patch.object(self.tools, 'run', side_effect=execute):
            first = self.request(item['id']).json()
            try:
                self.assertTrue(entered.wait(2))
                second = self.request(item['id']).json()
                self.assertEqual(self.post('/jobs/' + second['id'] + '/cancel').status_code, 200)
                with self.assertRaises(ValueError):
                    self.engine.preload(self.app.state.store.settings())
                self.assertEqual(calls, [first['id']])
            finally:
                release.set()
            self.wait_job(first['id'])
            self.wait_job(second['id'])
        self.assertEqual(calls, [first['id']])

    def test_exact_rgba_preservation_checks_rgb_channels_even_with_equal_alpha(self):
        original = Image.new('RGBA', (3, 2), (100, 120, 140, 255))
        params = OutpaintParams(extend_left=1, extend_right=1, extend_top=1, extend_bottom=1).model_dump()
        canvas = geometry(original.size, params)
        result = compose_preserved(original, Image.new('RGBA', (canvas['width'], canvas['height'])), canvas)
        result.putpixel((1, 1), (99, 120, 140, 255))
        with self.assertRaisesRegex(ValueError, '1 个像素'):
            verify_preserved(original, result, canvas)

    def test_alpha_noise_is_flattened_instead_of_rejected(self):
        raw = png((64, 64), (255, 0, 0, 254), mode='RGBA')
        item = self.post('/inputs', files={'file': ('qwen.png', raw, 'image/png')}).json()
        self.assertTrue(item['has_transparency'])
        self.assertEqual(item['alpha']['policy'], 'drop-alpha')
        with self.available(), patch.object(self.tools, 'run_upscale',
                                            return_value=(Image.new('RGB', (256, 256), 'blue'), {'exit_code': 0})):
            response = self.request(item['id'])
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        record = self.client.get(job['result']['manifest_url']).json()
        self.assertEqual(record['alpha']['policy'], 'drop-alpha')
        self.assertIsNone(record['alpha']['background'])
        with Image.open(io.BytesIO(self.client.get(job['result']['download_url']).content)) as result:
            self.assertEqual(result.mode, 'RGB')

    def test_real_transparency_is_composited_onto_white(self):
        image = Image.new('RGBA', (40, 40), (0, 0, 0, 0))
        image.paste((255, 0, 0, 255), (0, 0, 10, 40))
        stream = io.BytesIO()
        image.save(stream, 'PNG')
        item = self.post('/inputs', files={'file': ('cutout.png', stream.getvalue(), 'image/png')}).json()
        self.assertEqual(item['alpha']['policy'], 'composite-white')
        flattened, profile = flatten_alpha(Image.open(io.BytesIO(stream.getvalue())).convert('RGBA'))
        self.assertEqual(flattened.mode, 'RGB')
        self.assertEqual(flattened.getpixel((30, 20)), (255, 255, 255))
        self.assertEqual(flattened.getpixel((5, 20)), (255, 0, 0))
        self.assertEqual(profile['background'], '#ffffff')
        self.assertIsNone(alpha_profile(Image.new('RGB', (8, 8))))

    def test_outpaint_accepts_alpha_input_and_preserves_pixels(self):
        image = Image.new('RGBA', (256, 256), (255, 0, 0, 254))
        stream = io.BytesIO()
        image.save(stream, 'PNG')
        item = self.post('/inputs', files={'file': ('qwen.png', stream.getvalue(), 'image/png')}).json()
        params = OutpaintParams(extend_left=32, extend_right=0, prompt='extend').model_dump()
        candidate = Image.new('RGB', (288, 256), 'blue')
        with self.available(), patch.object(self.tools, 'run_outpaint', return_value=(candidate, {'method': 'fixture'})):
            response = self.request(item['id'], 'outpaint', params)
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['result']['preservation'], {'verified': True, 'different_pixels': 0})
        with Image.open(io.BytesIO(self.client.get(job['result']['download_url']).content)) as result:
            self.assertEqual(result.mode, 'RGB')
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(result.getpixel((32, 0)), (255, 0, 0))
        record = self.client.get(job['result']['manifest_url']).json()
        self.assertEqual(record['alpha']['policy'], 'drop-alpha')

    def test_limits_are_mode_specific(self):
        from app.image_tools import check_size
        check_size((7680, 4320), 'upscale')
        # 放大不再有固定上限，交给提交时的资源预算。
        check_size((16384, 16384), 'upscale')
        with self.assertRaises(ValueError):
            check_size((0, 100), 'upscale')
        check_size((3840, 2160), 'outpaint')
        with self.assertRaises(ValueError):
            check_size((8192, 8192), 'outpaint')

    def test_upscale_output_above_old_8192_cap_is_submitted(self):
        raw = png((2049, 64), 'red')
        item = self.post('/inputs', files={'file': ('wide.png', raw, 'image/png')}).json()
        candidate = Image.new('RGB', (8196, 256), 'blue')
        with self.available(), patch.object(self.tools, 'run_upscale', return_value=(candidate, {'exit_code': 0})):
            response = self.request(item['id'])
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['result']['width'], 8196)

    def test_encrypt_removes_exact_uploaded_duplicate_and_input_descriptor(self):
        folder = self.root / '图片'
        folder.mkdir()
        source = folder / 'sample.png'
        source.write_bytes(png())
        identity = self.app.state.store.register(source)
        uploaded = self.upload()
        from app.vault import Vault
        Vault(self.app.state.store).encrypt(identity, 'fixture-password-12345')
        self.assertFalse((self.tools.data / (uploaded['id'] + '.png')).exists())
        self.assertFalse((self.tools.data / (uploaded['id'] + '.json')).exists())
        self.assertEqual(self.client.get(uploaded['preview_url']).status_code, 404)

    def test_outpaint_task_saves_verified_pixels_and_snapshot(self):
        raw = png((256, 256), 'red')
        item = self.post('/inputs', files={'file': ('sample.png', raw, 'image/png')}).json()
        params = OutpaintParams(extend_left=32, extend_right=0, prompt='extend').model_dump()
        candidate = Image.new('RGB', (288, 256), 'blue')
        with self.available(), patch.object(self.tools, 'run_outpaint', return_value=(candidate, {'method':'fixture'})):
            response = self.request(item['id'], 'outpaint', params)
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['result']['preservation'], {'verified': True, 'different_pixels': 0})
        with Image.open(io.BytesIO(self.client.get(job['result']['download_url']).content)) as result:
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(result.getpixel((32, 0)), (255, 0, 0))
        record = self.client.get(job['result']['manifest_url']).json()
        self.assertGreaterEqual(record['params']['seed'], 0)
        self.assertEqual(record['canvas']['offset_x'], 32)

    def exercise_cli_stop(self, reason):
        item = self.upload()
        started = threading.Event()
        spawned = []
        real_popen = subprocess.Popen
        def fake_start():
            from app.process import process_birth
            from app.storage import atomic_json
            process = real_popen([self.tools.upscaler.executable, '-c', 'import time; time.sleep(30)'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            spawned.append(process)
            directory = self.root / 'worker-test'
            directory.mkdir()
            self.tools.upscaler.record = {'pid':process.pid, 'birth':process_birth(process.pid), 'directory':str(directory),'session':'fixture'}
            atomic_json(directory / 'status.json', {'session':'fixture','state':'ready','loaded':True})
            started.set()
        resources = {'gpu': {'free': 8*1024**3, 'utilization': 0}, 'memory': {'available': 20*1024**3}}
        with self.available(), patch('app.image_tools.resource_snapshot', return_value=resources), \
             patch.object(self.tools.upscaler, 'start', side_effect=fake_start), \
             patch('app.image_tools.TASK_TIMEOUT_SECONDS', .05 if reason == 'timeout' else 1800):
            response = self.request(item['id'])
            self.assertEqual(response.status_code, 202, response.text)
            identity = response.json()['id']
            try:
                self.assertTrue(started.wait(3))
                if reason == 'cancel':
                    self.assertEqual(self.post('/jobs/' + identity + '/cancel').status_code, 200)
                elif reason == 'battery':
                    with patch('app.engine.power_snapshot', return_value=interpret_power(0, 1, 80)):
                        self.engine.update_power()
                job = self.wait_job(identity)
                self.assertEqual(job['status'], 'failed' if reason == 'timeout' else 'cancelled', job)
                self.assertIsNone(job['result'])
                self.assertIsNotNone(spawned[0].poll())
                self.assertFalse(self.app.state.store.images())
            finally:
                for process in spawned:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(5)

    def test_running_resident_cancel_reaps_only_its_process(self):
        self.exercise_cli_stop('cancel')

    def test_running_resident_battery_protection_reaps_process(self):
        self.exercise_cli_stop('battery')

    def test_resident_timeout_reaps_process_and_keeps_failure_out_of_gallery(self):
        self.exercise_cli_stop('timeout')

    def test_resident_qwen_does_not_block_verified_upscale(self):
        evidence = {'upscale': {'verified':True,'resident_verified':True,'scale':4,
                              'model_sha256':'hash','cli_sha256':'hash','library_sha256':'hash'}}
        with patch.object(self.tools,'evidence',return_value=evidence), \
             patch.object(self.tools,'digest',return_value='hash'), \
             patch.object(self.engine,'_engine_alive',return_value=(True,123)):
            self.assertTrue(self.tools.capabilities()['upscale']['available'])

    def test_low_resources_reject_before_loading_or_enqueuing(self):
        item = self.upload()
        with patch.object(self.tools,'capabilities',return_value={'upscale':{'available':True,'verified':True}}), \
             patch('app.image_tools_models.resource_snapshot',return_value={'gpu':{'free':1024**3,'utilization':0},'memory':{'available':1024**3}}), \
             patch.object(self.tools.upscaler,'call') as native:
            resource = self.post('/resources/upscale',json={'params':{},'input_id':item['id']})
            self.assertFalse(resource.json()['allowed'])
            self.assertEqual(self.post('/models/upscale/load',json={'params':{}}).status_code,400)
            self.assertEqual(self.request(item['id']).status_code,400)
            native.assert_not_called()
            self.assertFalse(self.engine.jobs)

    def test_unknown_resources_fail_closed(self):
        with patch('app.image_tools_models.resource_snapshot',return_value={'gpu':None,'memory':{'available':None}}):
            self.assertFalse(self.tools.resources_for('upscale',{})['allowed'])

    def test_outpaint_budget_accounts_for_loaded_weights_and_canvas(self):
        settings = self.app.state.store.settings()
        for key in ('diffusion_model','text_encoder','vae','vision'):
            path=self.root / (key+'.model');path.write_bytes(b'fixture');settings[key]=str(path)
        self.app.state.store.save_settings(settings)
        self.engine.loaded_settings=settings
        components={key:{'weight_bytes':4*1024**3,'loaded_weight_bytes':4*1024**3} for key in ('diffusion_model','text_encoder','vae','vision')}
        resources={'gpu':{'free':7*1024**3,'utilization':0},'memory':{'available':10*1024**3}}
        with patch('app.image_tools_models.resource_snapshot',return_value=resources), \
             patch.object(self.engine,'_engine_alive',return_value=(True,123)), \
             patch.object(self.engine,'components_status',return_value=components):
            small=self.tools.resources_for('outpaint',{},(384,256))
            large=self.tools.resources_for('outpaint',{},(3840,2176))
            self.assertTrue(small['allowed'])
            self.assertEqual(small['warning'],'')
            self.assertTrue(large['allowed'],'outpaint memory estimates are advisory')
            self.assertTrue(large['warning'])
            for component in components.values():component['loaded_weight_bytes']=0
            unloaded=self.tools.resources_for('outpaint',{},(384,256))
            self.assertTrue(unloaded['allowed'])
            self.assertTrue(unloaded['warning'])

    def test_outpaint_memory_estimate_is_advisory_only(self):
        settings = self.app.state.store.settings()
        for key in ('diffusion_model','text_encoder','vae','vision'):
            path=self.root / (key+'.model');path.write_bytes(b'fixture');settings[key]=str(path)
        self.app.state.store.save_settings(settings)
        self.engine.loaded_settings=settings
        components={key:{'weight_bytes':1024,'loaded_weight_bytes':1024} for key in ('diffusion_model','text_encoder','vae','vision')}
        low={'gpu':{'free':7*1024**3,'utilization':0},'memory':{'available':2*1024**3}}
        with patch('app.image_tools_models.resource_snapshot',return_value=low), \
             patch.object(self.engine,'_engine_alive',return_value=(True,123)), \
             patch.object(self.engine,'components_status',return_value=components):
            small=self.tools.resources_for('outpaint',{},(384,256))
            loading=self.tools.resources_for('outpaint',{},(384,256),loading=True)
        self.assertTrue(small['allowed'])
        self.assertEqual(small['reason'],'')
        self.assertIn('系统内存',small['warning'])
        self.assertIn('仅作提示',small['note'])
        self.assertFalse(loading['allowed'],'manual model loading must stay conservative')
        busy={'gpu':{'free':7*1024**3,'utilization':95},'memory':{'available':2*1024**3}}
        with patch('app.image_tools_models.resource_snapshot',return_value=busy), \
             patch.object(self.engine,'_engine_alive',return_value=(True,123)), \
             patch.object(self.engine,'components_status',return_value=components):
            blocked=self.tools.resources_for('outpaint',{},(384,256))
        self.assertFalse(blocked['allowed'])
        self.assertIn('显卡繁忙',blocked['reason'])
        self.assertIn('系统内存',blocked['warning'])
        with patch('app.image_tools_models.resource_snapshot',return_value={'gpu':None,'memory':{'available':None}}):
            unknown=self.tools.resources_for('outpaint',{},(384,256))
        self.assertFalse(unknown['allowed'])
        self.assertEqual(unknown['warning'],'')
        broken=dict(settings); broken['vae']=str(self.root/'missing.model'); self.app.state.store.save_settings(broken)
        with patch('app.image_tools_models.resource_snapshot',return_value=low):
            incomplete=self.tools.resources_for('outpaint',{},(384,256))
        self.assertFalse(incomplete['allowed'])
        self.assertIn('模型文件不完整',incomplete['reason'])
        with patch('app.image_tools_models.resource_snapshot',return_value=low):
            self.assertFalse(self.tools.resources_for('upscale',{})['allowed'])

    def test_cancel_manual_upscale_load_preserves_qwen_and_blocks_tasks(self):
        entered=threading.Event()
        def load(op,params,cancelled,timeout):
            entered.set()
            while not cancelled():time.sleep(.01)
            raise InterruptedError('cancelled')
        with self.available(), patch.object(self.tools.upscaler,'call',side_effect=load), \
             patch.object(self.tools.upscaler,'stop') as stop, patch.object(self.engine,'_stop_process') as qwen_stop:
            self.assertEqual(self.post('/models/upscale/load',json={'params':{}}).status_code,200)
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ValueError): self.engine.submit_image_tool('upscale','x',{}, {})
            response=self.post('/models/upscale/release',json={})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIsNone(self.tools.model_operation)
            stop.assert_called()
            qwen_stop.assert_not_called()

    def test_manual_outpaint_load_reuses_context_and_loads_all_components(self):
        calls=[]
        def request(method,path,**kwargs):
            calls.append(path)
            return {'components':{}}
        with self.available(), patch.object(self.engine,'_load') as load, \
             patch.object(self.engine,'request',side_effect=request), \
             patch('app.image_tools_models.memory_snapshot',return_value={'available':10*1024**3}):
            response=self.post('/models/outpaint/load',json={'params':{}})
            self.assertEqual(response.status_code,200,response.text)
            deadline=time.monotonic()+3
            while self.tools.model_operation and time.monotonic()<deadline:time.sleep(.02)
            self.assertIsNone(self.tools.model_operation)
            self.assertEqual(len(calls),4)
            self.assertIn('/sdcpp/v1/models/components/vision/load',calls)
            load.assert_called_once()

    def test_pid_reuse_never_terminates_an_unrelated_process(self):
        self.tools.upscaler.record={'pid':123,'birth':111}
        with patch('app.upscale_resident.pid_alive',return_value=True), \
             patch('app.upscale_resident.process_path',return_value=self.tools.upscaler.executable), \
             patch('app.upscale_resident.process_birth',return_value=222), \
             patch('app.upscale_resident.terminate_pid') as terminate:
            self.tools.upscaler.stop()
            terminate.assert_not_called()

    def test_outpaint_memory_drop_stops_owned_engine(self):
        with patch('app.image_tools_models.memory_snapshot',return_value={'available':1024**3}), \
             patch.object(self.engine,'_stop_process') as stop:
            with self.assertRaisesRegex(RuntimeError,'低于 3 GiB'):
                with self.tools.outpaint_memory_guard():
                    time.sleep(.65)
            stop.assert_called_once()
            self.assertEqual(self.engine.state,'idle')


if __name__ == '__main__':
    unittest.main()
