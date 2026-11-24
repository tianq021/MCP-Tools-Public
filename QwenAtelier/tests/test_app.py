import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import httpx
from PIL import Image, PngImagePlugin

from app.assistant import Assistant
from app.main import Generation, create_app
from app.storage import Store, extract_config


class StudioTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)
        (self.root / '图片').mkdir()
        info = PngImagePlugin.PngInfo()
        info.add_text('prompt', json.dumps({
            '1': {'class_type':'KSampler', 'inputs':{'seed':42,'steps':20,'cfg':1,'model':['2',0], 'positive':['3',0], 'negative':['3',1]}},
            '2': {'class_type':'UnetLoaderGGUF','inputs':{'unet_name':'active-Q6_K.gguf'}},
            '3': {'class_type':'TextEncodeQwenImage21','inputs':{'prompt':'a small cabin','negative_prompt':'blurry'}},
            '4': {'class_type':'UnetLoaderGGUF','inputs':{'unet_name':'unused-Q4_K.gguf'}}}))
        Image.new('RGB',(64,96),'teal').save(self.root/'图片/sample.png',pnginfo=info)
        self.app = create_app(self.root)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {'X-Studio-Token':self.client.get('/api/session').json()['token']}
        models = self.root / 'models'
        models.mkdir()
        for name in ('diffusion.gguf', 'text.gguf', 'vae.safetensors', 'vision.gguf'):
            (models / name).write_bytes(b'\0' * 16)
        applied = self.client.put('/api/settings', json=self.client.get('/api/settings').json() | {
            'diffusion_model': str(models / 'diffusion.gguf'),
            'text_encoder': str(models / 'text.gguf'),
            'vae': str(models / 'vae.safetensors'),
            'vision': str(models / 'vision.gguf'),
        }, headers=self.headers)
        self.assertEqual(applied.status_code, 200, applied.text)

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.app.state.engine.worker.join(timeout=2)
        self.temp.cleanup()

    def scan(self):
        response = self.client.post('/api/gallery/scan',headers=self.headers)
        self.assertEqual(response.status_code,200)
        return self.client.get('/api/images').json()[0]

    def test_power_guard_is_enforced_by_api_and_setting_is_persistent(self):
        from app.power import interpret_power
        with patch('app.engine.power_snapshot', return_value=interpret_power(0, 1, 70)):
            for path, body in (('/api/jobs', {'prompt': 'cup', 'content_confirmed': True}),
                               ('/api/models/load', None),
                               ('/api/models/components/vae/load', None)):
                response = self.client.post(path, json=body, headers=self.headers)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('电池', response.json()['detail'])
            self.assertTrue(self.client.get('/api/status').json()['engine']['power']['blocked'])
            settings = self.client.get('/api/settings').json() | {'battery_protection': False}
            self.assertEqual(self.client.put('/api/settings', json=settings, headers=self.headers).status_code, 200)
            self.app.state.engine.update_power()
            self.assertFalse(self.client.get('/api/status').json()['engine']['power']['blocked'])
            self.assertFalse(self.app.state.store.settings()['battery_protection'])
        self.assertIsNone(self.app.state.engine.process)
        self.assertEqual(self.client.post('/api/models/components/invalid/load', headers=self.headers).status_code, 422)

    def test_gallery_metadata_tags_trash_restore_and_persistence(self):
        item=self.scan(); identity=item['id']
        self.assertEqual(item['config']['seed'],42)
        self.assertEqual(item['config']['model'],'active-Q6_K.gguf')
        self.assertEqual(item['config']['prompt'],'a small cabin')
        self.assertEqual(item['config']['negative_prompt'],'blurry')
        self.assertEqual(len(item['sha256']),64)
        self.assertEqual(self.client.get(f'/api/images/{identity}/file?thumb=true').status_code,200)
        self.client.put(f'/api/images/{identity}/tag',json={'tag':'favorite'},headers=self.headers)
        self.assertEqual(Store(self.root).images('favorite')[0]['id'],identity)
        self.client.delete(f'/api/images/{identity}',headers=self.headers)
        self.assertFalse((self.root/'图片/sample.png').exists())
        self.assertEqual(self.client.get('/api/images').json(),[])
        self.assertEqual(self.client.get(f'/api/images/{identity}/file').status_code,404)
        self.assertEqual(len(self.client.get('/api/images?tag=trash').json()),1)
        self.client.post(f'/api/images/{identity}/restore',headers=self.headers)
        self.assertTrue((self.root/'图片/sample.png').exists())
        self.assertEqual(self.client.get('/api/images?tag=favorite').json()[0]['sha256'],item['sha256'])

    def test_gallery_like_tag_toggle_filter_and_persistence(self):
        identity = self.scan()['id']
        for tag in ('like', 'dislike', 'favorite', 'none'):
            response = self.client.put(f'/api/images/{identity}/tag', json={'tag': tag}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(Store(self.root).image(identity)['tag'], tag)
            for selected in ('like', 'dislike', 'favorite'):
                rows = self.client.get('/api/images', params={'tag': selected}).json()
                self.assertEqual([row['id'] for row in rows], [identity] if tag == selected else [])
        self.assertEqual(self.client.put(f'/api/images/{identity}/tag', json={'tag': 'invalid'}, headers=self.headers).status_code, 422)

    def test_system_prompt_is_prepended_to_every_request(self):
        from app.assistant import parameter_block, system_content, system_prompt

        self.client.put('/api/assistant/config',json={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'test-key-system'},headers=self.headers)
        bodies=[]
        def handler(request):
            bodies.append(json.loads(request.content))
            chunk=json.dumps({'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]})
            return httpx.Response(200,text=f'data: {chunk}\n\ndata: [DONE]\n\n',headers={'content-type':'text/event-stream'})
        self.app.state.assistant.transport=httpx.MockTransport(handler)
        for text in ('第一句','第二句'):
            self.assertEqual(self.client.post('/api/assistant/chat',json={'messages':[{'role':'user','content':text}]},headers=self.headers).status_code,200)
        self.assertEqual(len(bodies),2)
        store,engine=self.app.state.store,self.app.state.engine
        for body in bodies:
            self.assertEqual(body['messages'][0]['role'],'system')
            self.assertEqual(body['messages'][0]['content'],system_content(store,engine))
            self.assertTrue(body['messages'][0]['content'].strip())
        self.assertTrue(system_prompt().startswith('你是 Qwen Atelier'))
        # 参数快照必须写进系统提示词，且包含当前模型与显卡
        system=bodies[0]['messages'][0]['content']
        self.assertIn('当前工作台参数',system)
        self.assertIn('Q4_K',system)
        self.assertIn('显存预算：',system)
        self.assertEqual(parameter_block(store,engine),parameter_block(store,engine))
        preview=self.client.get('/api/assistant/prompt').json()
        self.assertEqual(preview['path'],'prompts/system.md')
        self.assertEqual(preview['text'],system_content(store,engine))
        self.assertIn('当前工作台参数',preview['parameters'])
        # 改本地文件后，下一次请求立刻用新内容（按 mtime 失效）
        custom=self.root/'system.md';custom.write_text('自定义系统提示：只回答中文。',encoding='utf-8')
        with patch('app.assistant.PROMPT_FILE',custom):
            self.client.post('/api/assistant/chat',json={'messages':[{'role':'user','content':'第三句'}]},headers=self.headers)
        self.assertTrue(bodies[-1]['messages'][0]['content'].startswith('自定义系统提示：只回答中文。'))
        self.assertIn('当前工作台参数',bodies[-1]['messages'][0]['content'])

    def test_assistant_image_message_becomes_data_url_block(self):
        buffer=io.BytesIO();Image.new('RGB',(64,48),'salmon').save(buffer,'PNG')
        upload=self.client.post('/api/inputs',files={'file':('shot.png',buffer.getvalue(),'image/png')},headers=self.headers)
        self.assertEqual(upload.status_code,200)
        identity=upload.json()['id']
        self.client.put('/api/assistant/config',json={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'test-key-image'},headers=self.headers)
        bodies=[]
        def handler(request):
            bodies.append(json.loads(request.content))
            chunk=json.dumps({'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]})
            return httpx.Response(200,text=f'data: {chunk}\n\ndata: [DONE]\n\n',headers={'content-type':'text/event-stream'})
        self.app.state.assistant.transport=httpx.MockTransport(handler)
        response=self.client.post('/api/assistant/chat',json={'messages':[{'role':'user','content':'看看这张图','image_ids':[identity]}]},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        content=bodies[0]['messages'][1]['content']
        self.assertIsInstance(content,list)
        self.assertEqual(content[0],{'type':'text','text':'看看这张图'})
        self.assertEqual(content[1]['type'],'image_url')
        self.assertEqual(content[1]['image_url']['detail'],'auto')
        url=content[1]['image_url']['url']
        self.assertTrue(url.startswith('data:image/jpeg;base64,'))
        image=Image.open(io.BytesIO(base64.b64decode(url.split(',',1)[1])))
        self.assertEqual(image.size,(64,48))
        # 只有图片、没有文字时自动补一句提示语
        response=self.client.post('/api/assistant/chat',json={'messages':[{'role':'user','content':'','image_ids':[identity]}]},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(bodies[1]['messages'][1]['content'][0]['text'],'请分析这张图片。')
        # 不存在的图片 id 必须被拒绝，且不会发起上游请求
        before=len(bodies)
        missing=self.client.post('/api/assistant/chat',json={'messages':[{'role':'user','content':'x','image_ids':['0'*32]}]},headers=self.headers)
        self.assertEqual(missing.status_code,400)
        self.assertEqual(len(bodies),before)
        # 图片不能挂在 assistant 消息上
        rejected=self.client.post('/api/assistant/chat',json={'messages':[{'role':'user','content':'x'},{'role':'assistant','content':'y','image_ids':[identity]},{'role':'user','content':'z'}]},headers=self.headers)
        self.assertEqual(rejected.status_code,422)

    def test_startup_flags_and_profiles(self):
        from app.engine import extra_tokens
        settings=self.client.get('/api/settings').json()
        bad=settings|{'extra_args':'--vae-tiling\noops'}
        self.assertEqual(self.client.put('/api/settings',json=bad,headers=self.headers).status_code,422)
        flags='--vae-tiling\n--model-args qwen_image_2_1_prefix_cache_type=q8_0'
        good=settings|{'extra_args':flags}
        self.assertEqual(self.client.put('/api/settings',json=good,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/settings').json()['extra_args'],flags)
        self.assertEqual(extra_tokens('--a 1\n\n# comment\n--b'),['--a','1','--b'])
        created=self.client.post('/api/profiles',json={'name':'显存优先','settings':good},headers=self.headers)
        self.assertEqual(created.status_code,200,created.text)
        items=self.client.get('/api/profiles').json()
        self.assertEqual([item['name'] for item in items],['显存优先'])
        self.assertEqual(items[0]['summary']['extra_args'],flags)
        self.client.put('/api/settings',json=settings|{'max_vram':4.0},headers=self.headers)
        applied=self.client.post(f"/api/profiles/{created.json()['id']}/apply",headers=self.headers)
        self.assertEqual(applied.status_code,200,applied.text)
        self.assertEqual(applied.json()['max_vram'],settings['max_vram'])
        self.assertEqual(applied.json()['extra_args'],flags)
        self.assertEqual(applied.json()['port'],settings['port'])
        self.assertEqual(self.client.delete('/api/profiles/'+created.json()['id'],headers=self.headers).json(),{'deleted':True})
        self.assertEqual(self.client.get('/api/profiles').json(),[])

    def test_remembered_key_is_encrypted_and_restored(self):
        secret='test-key-persist'
        applied=self.client.put('/api/assistant/config',json={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':secret,'remember':True},headers=self.headers)
        self.assertEqual(applied.status_code,200,applied.text)
        self.assertTrue(applied.json()['has_key'])
        self.assertTrue(applied.json()['saved'])
        key_file=self.root/'data/assistant.key'
        self.assertTrue(key_file.is_file())
        self.assertNotIn(secret.encode(),key_file.read_bytes())
        self.assertNotIn(secret,json.dumps(self.client.get('/api/settings').json()))
        restored=Assistant(Store(self.root))
        self.assertTrue(restored.config()['has_key'])
        self.assertEqual(restored.key,secret)
        self.client.delete('/api/assistant/key',headers=self.headers)
        self.assertFalse(key_file.exists())
        self.assertFalse(Assistant(Store(self.root)).config()['has_key'])

    def test_manual_load_rejects_bad_combination_without_saving(self):
        settings=self.client.get('/api/settings').json()
        original=settings['vae']
        settings['vae']=str(self.root/'nope.safetensors')
        response=self.client.post('/api/models/load',json=settings,headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertIn('VAE',response.json()['detail'])
        self.assertEqual(self.client.get('/api/settings').json()['vae'],original)
        self.assertIsNone(self.app.state.engine.process)

    def test_mutation_token_cross_origin_and_output_path(self):
        self.assertEqual(self.client.post('/api/gallery/scan').status_code,403)
        self.assertEqual(self.client.get('/api/settings',headers={'Origin':'https://untrusted.example'}).status_code,403)
        settings=self.client.get('/api/settings').json()
        settings['output_directory']='outputs/../../outside'
        self.assertEqual(self.client.put('/api/settings',json=settings,headers=self.headers).status_code,422)
        self.assertEqual(self.client.get('/api/images/not-real/file').status_code,404)

    def vault_password(self, password='new-private-password-123'):
        return {'password': password, 'confirm_password': password}

    def test_password_only_vault_removes_plaintext_and_requires_password(self):
        item = self.scan(); source = self.root/'图片/sample.png'
        original = source.read_bytes()
        source.with_suffix('.json').write_text('private sidecar', encoding='utf-8')
        preview = self.root/'data/previews'/f'{item["id"]}.png'
        preview.write_bytes(original)
        self.app.state.store.save_job({'id':'private-job','status':'succeeded','image_ids':[item['id']], 'config':item['config'], 'created':1})
        response = self.client.post(f'/api/images/{item["id"]}/encrypt', json=self.vault_password(), headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['original_retained'])
        identity=response.json()['id']; path=self.root/'data/vault'/f'{identity}.qvault'
        raw=path.read_bytes()
        self.assertTrue(raw.startswith(b'QSTUDIO2'))
        self.assertNotIn(b'a small cabin',raw)
        for p in [source,source.with_suffix('.json'),preview,self.root/'data/thumbs'/f'{item["id"]}.webp']:
            self.assertFalse(p.exists())
        self.assertNotIn(b'a small cabin',(self.root/'data/studio.sqlite3').read_bytes())
        self.app.state.store.save_job({'id':'private-job','status':'succeeded','image_ids':[item['id']]})
        self.assertEqual(self.client.get('/api/jobs').json(),[])
        for suffix in ('','/file','/file?thumb=true','/preview','/export?depth=8'):
            self.assertEqual(self.client.get(f'/api/images/{item["id"]}{suffix}').status_code,404)
        url=f'/api/vault/{identity}/unlock'
        self.assertEqual(self.client.post(url,headers=self.headers).status_code,400)
        self.assertEqual(self.client.post(url,json={'password':'wrong password'},headers=self.headers).status_code,400)
        decoded=self.client.post(url,json={'password':'new-private-password-123'},headers=self.headers).json()
        self.assertEqual(base64.b64decode(decoded['image']),original)
        self.assertNotIn('cleanup_files',decoded)
        self.assertFalse(source.exists())
        self.assertEqual(self.client.get('/api/password-book').status_code,410)
        path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        self.assertEqual(self.client.post(url,json={'password':'new-private-password-123'},headers=self.headers).status_code,400)

    def test_vault_migration_rekeys_all_legacy_and_removes_password_book(self):
        from app.password_book import PasswordBook
        item=self.scan(); original=(self.root/'图片/sample.png').read_bytes()
        old='old-private-password-123'
        book=PasswordBook(self.root/'data');entry=book.add('old',old)['default']
        identity=self.app.state.store.encrypt_copy(item['id'],old)
        book.bind(identity,entry);self.app.state.store.archive_private(item['id'],identity)
        # An unlinked legacy copy must also be migrated and preserved.
        with self.app.state.store.db() as db:db.execute('DELETE FROM vault_images')
        other=self.app.state.store.encrypt_copy(item['id'],'unremembered-password-123')
        original_cipher=(self.root/'data/vault'/f'{identity}.qvault').read_bytes()
        response=self.client.post('/api/vault/migrate',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertTrue((self.root/'图片/sample.png').exists())
        self.assertTrue(book.path.exists())
        self.assertEqual((self.root/'data/vault'/f'{identity}.qvault').read_bytes(),original_cipher)
        response=self.client.post('/api/vault/migrate',json=self.vault_password()|{'legacy_password':'unremembered-password-123'},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['migrated'],2)
        self.assertFalse(book.path.exists())
        self.assertFalse((self.root/'图片/sample.png').exists())
        for vid in (identity,other):
            decoded=self.client.post(f'/api/vault/{vid}/unlock',json={'password':'new-private-password-123'},headers=self.headers)
            self.assertEqual(decoded.status_code,200,decoded.text)
            self.assertEqual(base64.b64decode(decoded.json()['image']),original)
            self.assertEqual(self.client.post(f'/api/vault/{vid}/unlock',json={'password':old},headers=self.headers).status_code,400)
        self.assertEqual(self.client.get('/api/vault/status').json()['pending'],0)

    def test_vault_unreadable_legacy_file_can_be_kept_without_blocking(self):
        from app.password_book import PasswordBook
        item=self.scan();source=self.root/'图片/sample.png';original=source.read_bytes()
        old='old-private-password-123'
        book=PasswordBook(self.root/'data');entry=book.add('old',old)['default']
        readable=self.app.state.store.encrypt_copy(item['id'],old)
        book.bind(readable,entry)
        # A copy from a password nobody saved anymore: it can never be decrypted again.
        orphan=self.app.state.store.encrypt_copy(item['id'],'never-saved-password-456')
        self.app.state.store.archive_private(item['id'],readable)
        orphan_bytes=(self.root/'data/vault'/f'{orphan}.qvault').read_bytes()
        # Without explicit consent nothing is re-keyed and no plaintext is deleted.
        response=self.client.post('/api/vault/migrate',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertIn('无法',response.json()['detail'])
        self.assertTrue(source.exists())
        self.assertTrue(book.path.exists())
        self.assertEqual((self.root/'data/vault'/f'{readable}.qvault').read_bytes()[:8],b'QSTUDIO1')
        # Keeping it must not drop the encrypted bytes, only mark them unreadable.
        response=self.client.post('/api/vault/migrate',json=self.vault_password()|{'keep_unreadable':True},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['migrated'],1)
        self.assertEqual(response.json()['kept'],[orphan])
        self.assertEqual(response.json()['pending'],0)
        self.assertEqual(response.json()['unreadable'],1)
        self.assertFalse(source.exists())
        self.assertFalse((self.root/'data/thumbs'/f'{item["id"]}.webp').exists())
        self.assertFalse(book.path.exists())
        self.assertEqual((self.root/'data/vault'/f'{orphan}.qvault').read_bytes(),orphan_bytes)
        decoded=self.client.post(f'/api/vault/{readable}/unlock',json={'password':'new-private-password-123'},headers=self.headers)
        self.assertEqual(decoded.status_code,200,decoded.text)
        self.assertEqual(base64.b64decode(decoded.json()['image']),original)
        blocked=self.client.post(f'/api/vault/{orphan}/unlock',json={'password':'new-private-password-123'},headers=self.headers)
        self.assertEqual(blocked.status_code,400)
        states={f['id']:f['state'] for f in self.client.get('/api/vault').json()}
        self.assertEqual(states,{readable:'secure',orphan:'unreadable'})
        # A kept file no longer blocks encrypting new images.
        Image.new('RGB',(32,32),'navy').save(self.root/'图片/another.png')
        self.assertEqual(self.client.post('/api/gallery/scan',headers=self.headers).json()['count'],1)
        second=[i for i in self.client.get('/api/images').json() if i['name']=='another.png'][0]
        third=self.client.post(f'/api/images/{second["id"]}/encrypt',json=self.vault_password(),headers=self.headers)
        self.assertEqual(third.status_code,200,third.text)
        self.assertFalse((self.root/'图片/another.png').exists())

    def test_saved_vault_password_is_reused_for_encrypt_and_unlock(self):
        item=self.scan()
        # Nothing saved yet: encryption must ask for a password once.
        response=self.client.post(f'/api/images/{item["id"]}/encrypt',json={},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertTrue((self.root/'图片/sample.png').exists())
        self.assertEqual(self.client.put('/api/vault/key',json={'password':'one-vault-password-123'},headers=self.headers).status_code,200)
        self.assertTrue(self.client.get('/api/vault/key').json()['configured'])
        response=self.client.post(f'/api/images/{item["id"]}/encrypt',json={},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        identity=response.json()['id']
        self.assertFalse((self.root/'图片/sample.png').exists())
        decoded=self.client.post(f'/api/vault/{identity}/unlock',json={},headers=self.headers)
        self.assertEqual(decoded.status_code,200,decoded.text)
        self.assertTrue(base64.b64decode(decoded.json()['image']))
        # A password that cannot open the vault must not silently replace the saved one.
        self.assertEqual(self.client.put('/api/vault/key',json={'password':'another-password-456'},headers=self.headers).status_code,400)
        self.assertEqual(self.client.post(f'/api/vault/{identity}/unlock',json={},headers=self.headers).status_code,200)

    def test_vault_password_can_be_changed_and_forgotten(self):
        item=self.scan()
        self.client.put('/api/vault/key',json={'password':'first-vault-password-123'},headers=self.headers)
        identity=self.client.post(f'/api/images/{item["id"]}/encrypt',json={},headers=self.headers).json()['id']
        response=self.client.post('/api/vault/rekey',json={'password':'second-vault-password-456','confirm_password':'second-vault-password-456'},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['rekeyed'],1)
        self.assertEqual(self.client.post(f'/api/vault/{identity}/unlock',json={},headers=self.headers).status_code,200)
        self.assertEqual(self.client.post(f'/api/vault/{identity}/unlock',json={'password':'first-vault-password-123'},headers=self.headers).status_code,400)
        self.assertEqual(self.client.post('/api/vault/rekey',json={'password':'third-vault-password-789','confirm_password':'mismatch-password-000'},headers=self.headers).status_code,400)
        self.assertEqual(self.client.post(f'/api/vault/{identity}/unlock',json={'password':'second-vault-password-456'},headers=self.headers).status_code,200)
        # Forgetting the saved password brings the manual prompt back.
        self.assertEqual(self.client.delete('/api/vault/key',headers=self.headers).status_code,200)
        self.assertFalse(self.client.get('/api/vault/status').json()['key_saved'])
        self.assertEqual(self.client.post(f'/api/vault/{identity}/unlock',json={},headers=self.headers).status_code,400)
        self.assertEqual(self.client.post(f'/api/vault/{identity}/unlock',json={'password':'second-vault-password-456'},headers=self.headers).status_code,200)

    def test_migration_remembers_the_new_password(self):
        from app.password_book import PasswordBook
        item=self.scan();old='old-private-password-123'
        book=PasswordBook(self.root/'data');entry=book.add('old',old)['default']
        identity=self.app.state.store.encrypt_copy(item['id'],old)
        book.bind(identity,entry);self.app.state.store.archive_private(item['id'],identity)
        response=self.client.post('/api/vault/migrate',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['key_saved'])
        # Right after the one-time upgrade, viewing needs no password at all.
        decoded=self.client.post(f'/api/vault/{identity}/unlock',json={},headers=self.headers)
        self.assertEqual(decoded.status_code,200,decoded.text)
        Image.new('RGB',(32,32),'navy').save(self.root/'图片/another.png')
        self.client.post('/api/gallery/scan',headers=self.headers)
        second=[i for i in self.client.get('/api/images').json() if i['name']=='another.png'][0]
        encrypted=self.client.post(f'/api/images/{second["id"]}/encrypt',json={},headers=self.headers)
        self.assertEqual(encrypted.status_code,200,encrypted.text)

    def test_vault_failed_write_keeps_original(self):
        item=self.scan()
        with patch('app.vault.Vault._write_verified',side_effect=ValueError('write failed')):
            response=self.client.post(f'/api/images/{item["id"]}/encrypt',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertTrue((self.root/'图片/sample.png').exists())
        self.assertEqual(self.client.get('/api/vault').json(),[])

    def test_vault_interrupted_cleanup_can_resume_with_new_password(self):
        item=self.scan()
        with patch('app.vault.Vault._cleanup',side_effect=ValueError('cleanup interrupted')):
            response=self.client.post(f'/api/images/{item["id"]}/encrypt',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertTrue((self.root/'图片/sample.png').exists())
        self.assertEqual(self.client.get('/api/vault/status').json()['pending'],1)
        response=self.client.post('/api/vault/migrate',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse((self.root/'图片/sample.png').exists())
        self.assertEqual(response.json()['pending'],0)

    def test_vault_changed_source_is_not_deleted(self):
        from app.password_book import PasswordBook
        item=self.scan();old='old-private-password-123'
        PasswordBook(self.root/'data').add('old',old)
        identity=self.app.state.store.encrypt_copy(item['id'],old)
        source=self.root/'图片/sample.png';source.write_bytes(b'changed contents')
        response=self.client.post('/api/vault/migrate',json=self.vault_password(),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertEqual(source.read_bytes(),b'changed contents')
        self.assertTrue((self.root/'data/vault'/f'{identity}.qvault').read_bytes().startswith(b'QSTUDIO1'))

    def test_vault_password_validation_does_not_echo_secrets(self):
        item=self.scan()
        response=self.client.post(f'/api/images/{item["id"]}/encrypt',json={'password':'secretshort','confirm_password':'secretshort'},headers=self.headers)
        self.assertEqual(response.status_code,422)
        self.assertNotIn('secretshort',response.text)
        response=self.client.post(f'/api/images/{item["id"]}/encrypt',json=self.vault_password()|{'confirm_password':'different-password-123'},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertTrue((self.root/'图片/sample.png').exists())

    def test_prompt_optional_and_sizes_align_to_eight(self):
        # 空提示词也允许生成；尺寸任意输入但向下对齐到 8 的倍数。
        empty=Generation(content_confirmed=True)
        self.assertEqual(empty.prompt,'')
        self.assertEqual((empty.width,empty.height),(1024,1024))
        odd=Generation(width=1003,height=260,content_confirmed=True)
        self.assertEqual((odd.width,odd.height),(1000,256))
        self.assertEqual(Generation(width=4095,height=256,content_confirmed=True).width,4088)
        with self.assertRaises(Exception):
            Generation(width=255)
        # 直接调接口：空提示词不应被 422 拦截，应进入模型就绪检查。
        settings=self.client.get('/api/settings').json()
        settings['vae']=str(self.root/'missing.safetensors')
        self.client.put('/api/settings',json=settings,headers=self.headers)
        r=self.client.post('/api/jobs',json={'prompt':'','width':1003,'height':1000,'content_confirmed':True},headers=self.headers)
        self.assertEqual(r.status_code,400)
        self.assertIn('VAE',r.json()['detail'])

    def test_presets_input_and_missing_model_guard(self):
        config=Generation(prompt='a small cabin',content_confirmed=True).model_dump()
        settings=self.client.get('/api/settings').json()
        settings['vae']=str(self.root/'missing.safetensors')
        self.client.put('/api/settings',json=settings,headers=self.headers)
        self.assertEqual(self.client.post('/api/jobs',json=config,headers=self.headers).status_code,400)
        self.assertIsNone(self.app.state.engine.process)
        r=self.client.post('/api/presets',json={'name':'cabin','config':config},headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/presets').json()[0]['config']['steps'],30)
        self.client.delete('/api/presets/'+r.json()['id'],headers=self.headers)
        self.assertEqual(self.client.get('/api/presets').json(),[])
        self.assertEqual(self.client.post('/api/inputs',files={'file':('bad.png',b'not an image')},headers=self.headers).status_code,400)

    def test_mock_native_job_saves_exact_seed_metadata_and_thumbnail(self):
        # Contract test only: no GPU inference, no changes to real images.
        engine=self.app.state.engine
        output=io.BytesIO();Image.new('RGB',(64,64),'green').save(output,format='PNG')
        config=Generation(prompt='a small cabin',seed=8,batch_count=2,content_confirmed=True).model_dump()
        sent=[]
        def request(method,path,**kw):
            if method=='POST':
                sent.append(kw['json']);return {'id':'mockjob'}
            return {'status':'completed','started':1000,'completed':2500,
                    'result':{'images':[{'b64_json':base64.b64encode(output.getvalue()).decode()}]}}
        with patch('app.engine.model_status',return_value={'blockers':[]}), patch.object(engine,'_load'), patch.object(engine,'model_hashes',return_value={'test':'hash'}), patch.object(engine,'request',side_effect=request):
            job=engine.submit(config)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and engine.jobs[job['id']]['status'] in ('queued','running'):
                time.sleep(.05)
            result=engine.jobs[job['id']]
            self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual([b['seed'] for b in sent],[8,9])
        self.assertNotIn('scheduler',sent[0]['sample_params'])
        images=self.app.state.store.images()
        self.assertEqual(len(images),2)
        self.assertEqual(sorted(i['config']['seed'] for i in images),[8,9])
        self.assertEqual(images[0]['config']['elapsed_seconds'],1.5)
        self.assertTrue(self.app.state.store.file(images[0]['id'],True).exists())
        self.assertEqual(len(list((self.root/'outputs').glob('*.json'))),2)
        stored=self.app.state.store.jobs()[0]
        self.assertEqual(len(stored['timing_signature']),64)
        self.assertNotIn('_timing',stored)
        public=self.client.get('/api/jobs').json()[0]
        self.assertIsNone(public['time_estimate'])

    def test_preload_command_and_unload_release_owned_process(self):
        engine=self.app.state.engine
        settings=self.app.state.store.settings()|{'eager_load':True}
        process=Mock(pid=99999999)
        process.poll.return_value=None
        with patch('app.engine.ROOT',self.root), patch('app.engine.subprocess.Popen',return_value=process) as launch, patch.object(engine,'request',return_value={'state':'ready'}):
            engine._load(settings)
            command=launch.call_args.args[0]
            self.assertIn('--eager-load',command)
            self.assertIn('--offload-to-cpu',command)
            self.assertEqual(engine.loaded_settings,settings)
            response=self.client.get('/api/status').json()
            self.assertEqual(response['engine']['pid'],99999999)
            self.assertIn('process_rss',response['memory'])
            self.assertFalse(response['models']['individual_preload_supported'])
            engine.unload()
            process.terminate.assert_called_once()
            process.wait.assert_called_once()
        self.assertEqual(engine.state,'idle')
        self.assertIsNone(engine.process)
        self.assertIsNone(engine.loaded_settings)
        response=self.client.get('/api/status').json()
        self.assertEqual(response['memory']['process_rss'],0)
        self.assertFalse(any(m['loaded'] for m in response['models']['components']))

    def test_cancel_running_keeps_model_loaded_and_queued_cancel_never_starts(self):
        engine = self.app.state.engine
        entered = threading.Event()
        process = Mock(pid=99999999)
        process.poll.return_value = None
        phase = {'status': 'running'}
        submitted = []

        def load(settings, **kwargs):
            engine.process, engine.loaded_settings, engine.state = process, settings, 'ready'

        def request(method, path, **kwargs):
            if path == '/sdcpp/v1/img_gen':
                submitted.append(kwargs.get('json', {}).get('seed'))
                return {'id': 'native-job-1'}
            if method == 'GET' and '/jobs/' in path:
                if phase['status'] == 'running':
                    entered.set()
                    time.sleep(0.05)
                return {'status': phase['status'], 'started': 1, 'completed': 2,
                        'preview': {'step': 1, 'total_steps': 8}, 'result': {'images': []}}
            raise AssertionError(f'unexpected request {method} {path}')

        with patch('app.engine.model_status', return_value={'blockers': []}), \
             patch.object(engine, '_load', side_effect=load), \
             patch.object(engine, 'model_hashes', return_value={}), \
             patch.object(engine, 'environment_record', return_value={}), \
             patch.object(engine, 'request', side_effect=request):
            first = engine.submit(Generation().model_dump())
            self.assertTrue(entered.wait(3))
            second = engine.submit(Generation().model_dump())
            response = self.client.post(f'/api/jobs/{second["id"]}/cancel', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['job']['status'], 'cancelled')
            phase['status'] = 'completed'
            response = self.client.post(f'/api/jobs/{first["id"]}/cancel', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(engine.draining, 'native-job-1')
            deadline = time.monotonic() + 5
            while engine.active or engine.pending.unfinished_tasks:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
        # Cancelling no longer unloads anything: the engine and its weights stay.
        process.terminate.assert_not_called()
        self.assertEqual(engine.state, 'ready')
        self.assertIs(engine.process, process)
        self.assertEqual(engine.jobs[first['id']]['status'], 'cancelled')
        self.assertIsNone(engine.jobs[first['id']]['error'])
        self.assertEqual(engine.jobs[first['id']]['native_id'], 'native-job-1')
        self.assertEqual(submitted, [engine.jobs[first['id']]['config']['seed']])
        self.assertIsNone(engine.draining)
        # A delayed second click must not touch a later model process.
        other = Mock(pid=99999998)
        other.poll.return_value = None
        engine.process = other
        response = self.client.post(f'/api/jobs/{first["id"]}/cancel', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        other.terminate.assert_not_called()
        engine.process = None

    def test_cancel_loading_job_and_runtime_errors_are_json(self):
        engine = self.app.state.engine
        entered = threading.Event()
        def load(settings, cancelled):
            engine.state = 'loading'
            entered.set()
            deadline = time.monotonic() + 3
            while not cancelled():
                if time.monotonic() > deadline:
                    raise AssertionError('loading was not cancelled')
                time.sleep(.01)
            raise InterruptedError('cancelled')
        with patch('app.engine.model_status', return_value={'blockers': []}), patch.object(engine, '_load', side_effect=load):
            job = engine.submit(Generation().model_dump())
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.client.post(f'/api/jobs/{job["id"]}/cancel', headers=self.headers).status_code, 200)
            deadline = time.monotonic() + 3
            while engine.active:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
        self.assertEqual(engine.jobs[job['id']]['status'], 'cancelled')
        self.assertEqual(engine.state, 'idle')
        with patch.object(engine, 'cancel', side_effect=RuntimeError('native failure')), self.assertLogs('app.main', level='ERROR'):
            response = self.client.post('/api/jobs/test/cancel', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn('detail', response.json())

    def test_preload_can_be_cancelled_but_generation_cannot_be_unloaded(self):
        engine=self.app.state.engine
        entered=threading.Event()
        def loading(settings,cancelled):
            entered.set()
            deadline=time.monotonic()+3
            while not cancelled() and time.monotonic()<deadline:
                time.sleep(.01)
            if cancelled():
                raise InterruptedError('cancelled')
            raise RuntimeError('test timed out waiting for cancellation')
        with patch('app.engine.model_status',return_value={'blockers':[]}),patch.object(engine,'_load',side_effect=loading):
            engine.preload(self.app.state.store.settings())
            self.assertTrue(entered.wait(timeout=2))
            response=self.client.post('/api/models/unload',headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(engine.state,'idle')
            self.assertIsNone(engine.error)
        engine.active='running-job'
        response=self.client.post('/api/models/unload',headers=self.headers)
        self.assertEqual(response.status_code,400)
        engine.active=None

    def test_unload_records_owned_usage_and_preserves_unknown_values(self):
        engine=self.app.state.engine
        for gpu in ({'process_used':2*1024**3}, None):
            process=Mock(pid=99999999)
            process.poll.return_value=None
            engine.process,engine.state=process,'ready'
            snapshot={'memory':{'process_rss':8*1024**3,'process_private':9*1024**3},'gpu':gpu}
            with patch('app.engine.resource_snapshot',return_value=snapshot) as read:
                response=self.client.post('/api/models/unload',headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            read.assert_called_once_with(99999999)
            release=response.json()['last_release']
            self.assertEqual(release['pid'],99999999)
            self.assertEqual(release['process_rss'],8*1024**3)
            self.assertEqual(release['process_private'],9*1024**3)
            self.assertEqual(release['process_gpu'],gpu['process_used'] if gpu else None)
            self.assertEqual(self.client.get('/api/status').json()['engine']['last_release'],release)
            process.terminate.assert_called_once()
        # Repeating unload must not overwrite the last real measurement with zeros.
        self.client.post('/api/models/unload',headers=self.headers)
        self.assertEqual(engine.last_release,release)
        engine.active='running'
        with patch('app.engine.resource_snapshot') as read:
            self.assertEqual(self.client.post('/api/models/unload',headers=self.headers).status_code,400)
            read.assert_not_called()
        engine.active=None

    def test_job_survives_http_client_close_and_is_visible_after_reconnect(self):
        engine=self.app.state.engine
        waiting,finish=threading.Event(),threading.Event()
        output=io.BytesIO();Image.new('RGB',(16,16),'green').save(output,format='PNG')
        def request(method,path,**kwargs):
            if method=='POST':
                return {'id':'disconnected-client-job'}
            waiting.set()
            if not finish.is_set():
                return {'status':'running'}
            return {'status':'completed','result':{'images':[{'b64_json':base64.b64encode(output.getvalue()).decode()}]}}
        with patch('app.engine.model_status',return_value={'blockers':[]}), \
             patch.object(engine,'_load'),patch.object(engine,'model_hashes',return_value={}), \
             patch.object(engine,'environment_record',return_value={}),patch.object(engine,'request',side_effect=request):
            connection=TestClient(self.app)  # No app lifespan: this is only an HTTP connection.
            try:
                response=connection.post('/api/jobs',json={'prompt':'a ceramic cup','steps':1,'content_confirmed':True},headers=self.headers)
                self.assertEqual(response.status_code,202,response.text)
                identity=response.json()['id']
                self.assertTrue(waiting.wait(3))
            finally:
                connection.close()
            try:
                self.assertFalse(engine.stopping.is_set())
                reconnect=TestClient(self.app)
                try:
                    running=next(j for j in reconnect.get('/api/jobs').json() if j['id']==identity)
                    self.assertEqual(running['status'],'running')
                    self.assertFalse(engine.jobs[identity]['_cancel'])
                    finish.set()
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline and engine.active:
                        time.sleep(.02)
                    completed=next(j for j in reconnect.get('/api/jobs').json() if j['id']==identity)
                    self.assertEqual(completed['status'],'completed',completed.get('error'))
                    self.assertEqual(len(completed['image_ids']),1)
                finally:
                    reconnect.close()
            finally:
                finish.set()

    def test_restart_cancels_unfinished_jobs_and_keeps_their_parameters(self):
        from app.engine import Engine
        store=self.app.state.store
        for state in ('queued','running'):
            store.save_job({'id':state,'status':state,'created':time.time(),'finished':None,
                            'config':{'prompt':'restart task','steps':8}})
        with patch.object(Engine,'_load') as load:
            restarted=Engine(store)
            try:
                self.assertTrue(restarted.pending.empty())
                self.assertIsNone(restarted.process)
                for job in store.jobs():
                    self.assertEqual(job['status'],'cancelled')
                    self.assertIn('服务重启',job['error'])
                    self.assertIsNotNone(job['finished'])
                # The request parameters survive the restart so the job can be submitted again.
                for state in ('queued','running'):
                    saved=json.loads((store.data/'interrupted'/f'{state}.json').read_text(encoding='utf-8'))
                    self.assertEqual(saved['config']['prompt'],'restart task')
                load.assert_not_called()
            finally:
                restarted.close();restarted.worker.join(timeout=2)

    def test_live_estimate_api_uses_server_clock_and_clears_on_cancel(self):
        from app.timing import JobTiming
        engine=self.app.state.engine
        now=time.monotonic()
        timing=JobTiming(now-210,history=240,sample_count=2)
        timing.begin_image(1,8)
        timing.observe(1,8,now-3)
        timing.observe(2,8,now)
        engine.jobs['estimate-test']=dict(id='estimate-test',status='running',created=time.time(),
            started=time.time()-210,finished=None,config=Generation().model_dump(),_timing=timing)
        public=self.client.get('/api/jobs').json()[0]
        self.assertNotIn('_timing',public)
        self.assertAlmostEqual(public['time_estimate']['remaining_seconds'],30,delta=1)
        self.assertAlmostEqual(public['time_estimate']['sampling_remaining_seconds'],18,delta=1)
        engine.jobs['estimate-test']['status']='cancelled'
        self.assertIsNone(self.client.get('/api/jobs').json()[0]['time_estimate'])

    def test_assistant_key_stays_in_memory_and_streams_events(self):
        chat={'messages':[{'role':'user','content':'写一句提示词'}]}
        self.assertEqual(self.client.post('/api/assistant/chat',json=chat,headers=self.headers).status_code,400)
        self.assertFalse(self.client.get('/api/assistant/config').json()['has_key'])
        insecure=self.client.put('/api/assistant/config',json={'base_url':'http://example.com/v1','model':'x'},headers=self.headers)
        self.assertEqual(insecure.status_code,400)
        settings=self.client.get('/api/settings').json()
        settings['assistant_url']='api.deepseek.com'
        self.assertEqual(self.client.put('/api/settings',json=settings,headers=self.headers).status_code,422)
        secret='test-key-not-real'
        applied=self.client.put('/api/assistant/config',json={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':secret},headers=self.headers)
        self.assertEqual(applied.status_code,200)
        self.assertTrue(applied.json()['has_key'])
        self.assertNotIn(secret,applied.text)
        self.assertNotIn(secret,(self.root/'data/settings.json').read_text(encoding='utf-8'))
        self.assertNotIn(secret,json.dumps(self.client.get('/api/settings').json()))

        seen={}
        def handler(request):
            seen['auth']=request.headers.get('authorization')
            seen['body']=json.loads(request.content)
            chunks=[{'choices':[{'delta':{'content':'雾中山间小屋'},'finish_reason':None}]},
                    {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'total_tokens':12}}]
            body=''.join(f'data: {json.dumps(c)}\n\n' for c in chunks)+'data: [DONE]\n\n'
            return httpx.Response(200,text=body,headers={'content-type':'text/event-stream'})
        self.app.state.assistant.transport=httpx.MockTransport(handler)
        response=self.client.post('/api/assistant/chat',json=chat,headers=self.headers)
        self.assertEqual(response.status_code,200)
        events=[json.loads(line) for line in response.text.strip().splitlines() if line.strip()]
        self.assertEqual(''.join(e['text'] for e in events if e['type']=='content'),'雾中山间小屋')
        self.assertEqual(events[-1]['type'],'done')
        self.assertEqual(seen['auth'],f'Bearer {secret}')
        self.assertEqual(seen['body']['messages'][-1],{'role':'user','content':'写一句提示词'})
        self.assertEqual(seen['body']['thinking'],{'type':'disabled'})

        self.app.state.assistant.transport=httpx.MockTransport(lambda request: httpx.Response(401,json={'error':'nope'}))
        failed=self.client.post('/api/assistant/chat',json=chat,headers=self.headers)
        self.assertIn('API Key 无效',failed.text)
        self.assertEqual(self.client.delete('/api/assistant/key',headers=self.headers).json(),{'cleared':True})
        self.assertFalse(self.client.get('/api/assistant/config').json()['has_key'])


if __name__=='__main__':
    unittest.main(verbosity=2)
