from __future__ import annotations

import hashlib
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from PIL import Image, ImageOps, PngImagePlugin

from .png16 import insert_text, read_header

ROOT = Path(__file__).resolve().parents[1]
Image.MAX_IMAGE_PIXELS = 80_000_000


def sha256(path, cancelled=None):
    with Path(path).open('rb') as stream:
        if cancelled is None:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
        digest = hashlib.sha256()
        while block := stream.read(4 * 1024 * 1024):
            if cancelled():
                raise InterruptedError('已停止模型校验')
            digest.update(block)
        return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def extract_config(info, size):
    if 'qwen_studio' in info:
        try:
            return json.loads(info['qwen_studio'])
        except (ValueError, TypeError):
            pass
    config = {'width': size[0], 'height': size[1], 'source': 'imported',
              'reproducibility': '历史图片可能缺少模型哈希、软件版本或原始输入，无法保证逐像素复现。'}
    try:
        graph = json.loads(info.get('prompt', '{}'))
        # Follow the active sampler's model and conditioning links, not the last unused node.
        sampler = next((v for v in graph.values() if v.get('class_type') == 'KSampler'), None)
        if sampler:
            inputs = sampler['inputs']
            for key in ('seed', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise'):
                if key in inputs:
                    config[key] = inputs[key]
            for field, target in [('model', 'model'), ('positive', 'prompt'), ('negative', 'negative_prompt')]:
                link = inputs.get(field)
                node = graph.get(str(link[0]), {}) if isinstance(link, list) else {}
                values = node.get('inputs', {})
                names = ('negative_prompt', 'text') if target == 'negative_prompt' else ('prompt', 'text', 'unet_name')
                config[target] = next((values[k] for k in names if isinstance(values.get(k), str)), '')
        for node in graph.values():
            values = node.get('inputs', {})
            if node.get('class_type') == 'VAELoader':
                config['vae'] = values.get('vae_name')
            if node.get('class_type') == 'CLIPLoader':
                config['text_encoder'] = values.get('clip_name')
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return config


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.data = self.root / 'data'
        for name in ('', 'thumbs', 'previews', 'trash', 'vault', 'inputs', 'logs'):
            (self.data / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'outputs').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db_path = self.data / 'studio.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS images (
                  id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT, width INTEGER,
                  height INTEGER, size INTEGER, created REAL, sha256 TEXT, metadata TEXT,
                  config TEXT, tag TEXT DEFAULT 'none', deleted INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS presets (id TEXT PRIMARY KEY, name TEXT, config TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS vault_images (image_id TEXT PRIMARY KEY, vault_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS vault_secure (vault_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS private_jobs (job_id TEXT PRIMARY KEY);
            ''')
        self.settings_path = self.data / 'settings.json'
        # 默认使用项目内 models/ 的相对位置，不写死任何本机路径；可由设置页或环境变量覆盖。
        models = ROOT / 'models'
        diffusion = models / 'unet/qwen_image_2.1-Q6_K.gguf'
        vae = models / 'vae/qwen_image_2.1_vae_bf16.safetensors'
        self.defaults = dict(
            diffusion_model=os.environ.get('QWEN_DIFFUSION_MODEL', str(diffusion if diffusion.is_file() else models / diffusion.name)),
            text_encoder=os.environ.get('QWEN_TEXT_ENCODER', str(ROOT / 'models/Qwen3VL-8B-Instruct-Q4_K_M.gguf')),
            vae=os.environ.get('QWEN_VAE', str(vae if vae.is_file() else models / vae.name)),
            vision=os.environ.get('QWEN_VISION_MODEL', str(ROOT / 'models/mmproj-Qwen3VL-8B-Instruct-F16.gguf')),
            max_vram=6.0, text_encoder_cpu=True, vae_cpu=False,
            keep_loaded=True, eager_load=False, battery_protection=True, output_directory='outputs',
            port=int(os.environ.get('QWEN_PORT', '17860')),
            extra_args='',
            assistant_url='https://api.deepseek.com', assistant_model='deepseek-flash')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute('PRAGMA secure_delete=ON')
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def settings(self):
        if self.settings_path.exists():
            return self.defaults | json.loads(self.settings_path.read_text(encoding='utf-8'))
        return self.defaults.copy()

    def save_settings(self, settings):
        with self.lock:
            atomic_json(self.settings_path, settings)

    def within(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('文件必须位于项目目录内')
        return path

    def profiles(self):
        path = self.data / 'profiles.json'
        if not path.is_file():
            return []
        try:
            items = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        return [item for item in items if isinstance(item, dict) and item.get('id')]

    def add_profile(self, name, settings):
        with self.lock:
            items = self.profiles()
            identity = uuid.uuid4().hex
            items.append({'id': identity, 'name': name.strip(), 'created': time.time(), 'settings': settings})
            atomic_json(self.data / 'profiles.json', items)
            return {'id': identity, 'name': name.strip(), 'created': items[-1]['created'], 'settings': settings}

    def delete_profile(self, identity):
        with self.lock:
            items = [item for item in self.profiles() if item['id'] != identity]
            atomic_json(self.data / 'profiles.json', items)
            return {'deleted': True}

    def scan(self):
        folders = {self.root / '图片', self.within(self.root / self.settings()['output_directory'])}
        count, errors = 0, []
        for folder in folders:
            if not folder.exists():
                continue
            # Top level only: never traverse private or unrelated subfolders.
            for path in folder.iterdir():
                if path.is_file() and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                    try:
                        self.register(path)
                        count += 1
                    except Exception as exc:
                        errors.append(f'{path.name}: {exc}')
        return {'count': count, 'errors': errors}

    def register(self, path):
        path = self.within(path)
        with self.lock, self.db() as db:
            old = db.execute('SELECT * FROM images WHERE path=?', (str(path),)).fetchone()
            stat = path.stat()
            if old and old['size'] == stat.st_size and old['created'] == stat.st_mtime and json.loads(old['config']).get('extractor_version') == 2:
                return old['id']
            identity = old['id'] if old else uuid.uuid4().hex
            with Image.open(path) as im:
                im.load()
                info = {k: v for k, v in im.info.items() if isinstance(v, (str, int, float, bool))}
                width, height = im.size
                config = extract_config(info, im.size)
                config['extractor_version'] = 2
                thumb = ImageOps.exif_transpose(im).convert('RGB')
                thumb.thumbnail((480, 480))
                thumb.save(self.data / 'thumbs' / f'{identity}.webp', 'WEBP', quality=82)
            db.execute('''INSERT INTO images(id,path,name,width,height,size,created,sha256,metadata,config) VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name,width=excluded.width,height=excluded.height,
                       size=excluded.size,created=excluded.created,sha256=excluded.sha256,metadata=excluded.metadata,config=excluded.config''',
                       (identity, str(path), path.name, width, height, stat.st_size, stat.st_mtime,
                        sha256(path), json.dumps(info, ensure_ascii=False), json.dumps(config, ensure_ascii=False)))
            return identity

    def _bit_depth(self, path):
        path = Path(path)
        if path.suffix.lower() != '.png':
            return None
        try:
            with path.open('rb') as stream:
                return read_header(stream.read(33))['bit_depth']
        except (OSError, ValueError):
            return None

    def images(self, tag='all', query=''):
        with self.db() as db:
            rows = db.execute('SELECT * FROM images WHERE deleted=? AND id NOT IN (SELECT image_id FROM vault_images) ORDER BY created DESC', (int(tag == 'trash'),)).fetchall()
        result = []
        for row in rows:
            row = dict(row)
            if tag not in ('all', 'trash') and row['tag'] != tag:
                continue
            if tag == 'all' and row['tag'] == 'blocked':
                continue
            if query.casefold() not in (row['name'] + row['config']).casefold():
                continue
            row['config'] = json.loads(row['config'])
            row['bit_depth'] = self._bit_depth(row['path'])
            row.pop('metadata')
            row.pop('path')
            result.append(row)
        return result

    def image(self, identity):
        with self.db() as db:
            if db.execute('SELECT 1 FROM vault_images WHERE image_id=?', (identity,)).fetchone():
                raise KeyError('图片已归入私密库，请在私密库解锁')
            row = db.execute('SELECT * FROM images WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError('图片不存在')
        item = dict(row)
        item['config'] = json.loads(item['config'])
        item['metadata'] = json.loads(item['metadata'])
        item['bit_depth'] = self._bit_depth(item['path'])
        return item

    def file(self, identity, thumb=False):
        item = self.image(identity)
        if item['deleted']:
            raise KeyError('图片已移入回收站')
        return self.within(self.data / 'thumbs' / f'{identity}.webp' if thumb else item['path'])

    def tag(self, identity, tag):
        self.image(identity)
        with self.db() as db:
            db.execute('UPDATE images SET tag=? WHERE id=?', (tag, identity))

    def trash(self, identity, restore=False):
        with self.lock:
            item = self.image(identity)
            original = self.within(item['path'])
            archived = self.data / 'trash' / f'{identity}{original.suffix}'
            if restore:
                if not item['deleted']:
                    return
                if original.exists():
                    raise ValueError('原路径已有文件，不能覆盖')
                os.replace(archived, original)
            else:
                if item['deleted']:
                    return
                os.replace(original, archived)
            with self.db() as db:
                db.execute('UPDATE images SET deleted=? WHERE id=?', (int(not restore), identity))

    def save_output(self, raw, config):
        directory = self.within(self.root / self.settings()['output_directory'])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'qwen_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}.png'
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('qwen_studio', json.dumps(config, ensure_ascii=False))
        with Image.open(io.BytesIO(raw)) as im:
            im.save(path, pnginfo=metadata)
        # Image hash is outside the image to avoid a self-referential hash.
        atomic_json(path.with_suffix('.json'), config | {'image_sha256': sha256(path)})
        return self.register(path)

    def save_output16(self, raw, config):
        """Store a true 16-bit PNG without re-encoding its pixel data.

        Only text chunks are touched; IHDR/IDAT and every other chunk are copied
        byte for byte. A derived 8-bit preview is written under data/previews.
        """
        header = read_header(raw[:33])
        if header['bit_depth'] != 16 or header['color_type'] not in (2, 6):
            raise ValueError('引擎返回的不是真正的 16 位 PNG，已拒绝保存')
        config = dict(config)
        # The file header is authoritative; do not trust the caller's claim.
        config['bit_depth_file'] = header['bit_depth']
        directory = self.within(self.root / self.settings()['output_directory'])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'qwen_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}_16b.png'
        payload = insert_text(raw, 'qwen_studio', json.dumps(config, ensure_ascii=False))
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temp.write_bytes(payload)
        os.replace(temp, path)
        # Image hash is outside the image to avoid a self-referential hash.
        atomic_json(path.with_suffix('.json'), config | {'image_sha256': sha256(path)})
        identity = self.register(path)
        self._write_preview(identity, path)
        return identity

    def _write_preview(self, identity, master):
        target = self.data / 'previews' / f'{identity}.png'
        temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        with Image.open(master) as im:
            # Pillow loads 16-bit RGB as mode RGB (high byte); this is an 8-bit
            # preview only and never replaces the master file.
            im.convert('RGB').save(temp, 'PNG')
        header = read_header(temp.read_bytes()[:33])
        if header['bit_depth'] != 8:
            temp.unlink(missing_ok=True)
            raise RuntimeError('8 位预览生成失败：输出不是 8 bit')
        os.replace(temp, target)

    def preview_file(self, identity):
        item = self.image(identity)
        if item['deleted']:
            raise KeyError('图片已移入回收站')
        path = self.data / 'previews' / f'{identity}.png'
        if not path.is_file():
            raise KeyError('这张图没有 8 位预览')
        return path

    def ensure_preview(self, identity):
        try:
            return self.preview_file(identity)
        except KeyError:
            pass
        item = self.image(identity)
        if item['deleted']:
            raise KeyError('图片已移入回收站')
        master = self.within(item['path'])
        header = read_header(master.read_bytes()[:33])
        if header['bit_depth'] != 16:
            raise KeyError('这张图没有 8 位预览')
        self._write_preview(identity, master)
        return self.preview_file(identity)

    def save_job(self, job):
        public = {k: v for k, v in job.items() if not k.startswith('_')}
        with self.db() as db:
            if db.execute('SELECT 1 FROM private_jobs WHERE job_id=?', (job['id'],)).fetchone():
                return
            db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?)', (job['id'], json.dumps(public, ensure_ascii=False)))

    def jobs(self, limit=50):
        with self.db() as db:
            if limit is None:
                rows = db.execute('SELECT payload FROM jobs ORDER BY rowid DESC')
                return [json.loads(r[0]) for r in rows]
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT ?', (limit,))]

    def presets(self):
        with self.db() as db:
            return [dict(r) | {'config': json.loads(r['config'])} for r in db.execute('SELECT * FROM presets ORDER BY created DESC')]

    def add_preset(self, name, config):
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO presets VALUES(?,?,?,?)', (identity, name, json.dumps(config, ensure_ascii=False), time.time()))
        return identity

    def delete_preset(self, identity):
        with self.db() as db:
            db.execute('DELETE FROM presets WHERE id=?', (identity,))

    def encrypt_copy(self, identity, password):
        item = self.image(identity)
        source = self.file(identity)
        if source.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('私密库单文件上限 128 MiB')
        # Encrypt the image and its metadata together; reveal only opaque IDs outside.
        import base64
        payload = json.dumps({'image': base64.b64encode(source.read_bytes()).decode(), 'record': item}, ensure_ascii=False).encode()
        salt, nonce = os.urandom(16), os.urandom(12)
        key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode())
        header = b'QSTUDIO1' + salt + nonce
        encrypted = header + AESGCM(key).encrypt(nonce, payload, header)
        identity = uuid.uuid4().hex
        path = self.data / 'vault' / f'{identity}.qvault'
        path.write_bytes(encrypted)
        # Always a copy. Originals are not silently removed or claimed securely erased.
        return identity

    def archive_private(self, identity, vault_id):
        # Preserve source files, but deny normal gallery/file/metadata access.
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO vault_images VALUES (?,?)', (identity, vault_id))

    def private_ids(self):
        with self.db() as db:
            return {row[0] for row in db.execute('SELECT image_id FROM vault_images')}

    def decrypt(self, identity, password):
        if len(identity) != 32 or not all(c in '0123456789abcdef' for c in identity):
            raise KeyError('私密文件不存在')
        path = self.data / 'vault' / f'{identity}.qvault'
        if not path.exists():
            raise KeyError('私密文件不存在')
        data = path.read_bytes()
        if data[:8] not in (b'QSTUDIO1', b'QSTUDIO2'):
            raise ValueError('不支持的私密文件版本')
        key = Scrypt(salt=data[8:24], length=32, n=2**17, r=8, p=1).derive(password.encode())
        return json.loads(AESGCM(key).decrypt(data[24:36], data[36:], data[:36]))
