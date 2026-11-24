"""Password-only vault. Plaintext exists only in request memory during migration.

Ciphertext is atomically written and authenticated before any source is removed.
The authenticated payload contains the cleanup manifest, so interrupted cleanup
can be retried without a plaintext journal or a remembered password.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .assistant import _dpapi
from .password_book import PasswordBook
from .storage import sha256


class VaultKey:
    """The single vault password, kept with Windows DPAPI for the current account.

    The .qvault files still need this password to decrypt; the saved copy only
    removes the typing. Copying the vault or the file to another Windows account
    gives nothing, because DPAPI cannot unwrap it there.
    """

    def __init__(self, data):
        self.path = data / 'vault.key'

    def get(self):
        if not self.path.exists():
            return None
        try:
            record = json.loads(self.path.read_text(encoding='utf-8'))
            raw = _dpapi(base64.b64decode(record['password'], validate=True), decrypt=True)
            return raw.decode('utf-8')
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def set(self, password):
        record = {'password': base64.b64encode(_dpapi(password.encode('utf-8'))).decode('ascii')}
        temp = self.path.with_name(self.path.name + '.tmp')
        try:
            temp.write_text(json.dumps(record), encoding='utf-8')
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)


class Vault:
    def __init__(self, store):
        self.store = store
        self.directory = store.data / 'vault'
        self.key = VaultKey(store.data)
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS vault_secure (vault_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS private_jobs (job_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS vault_unreadable (
                  vault_id TEXT PRIMARY KEY, reason TEXT, detected REAL);
            ''')

    def _states(self):
        with self.store.db() as db:
            secured = {r[0] for r in db.execute('SELECT vault_id FROM vault_secure')}
            unreadable = {r[0] for r in db.execute('SELECT vault_id FROM vault_unreadable')}
        return secured, unreadable

    def status(self):
        secured, unreadable = self._states()
        stems = {path.stem for path in self.directory.glob('*.qvault')}
        # A file that cannot be decrypted with any known password stays encrypted
        # as-is. It is reported, never counted as a pending migration and never
        # deleted, so it can never block the upgrade of the files that do work.
        return {'total': len(stems), 'secured': len(stems & secured),
                'pending': len(stems - secured - unreadable),
                'unreadable': len(stems & unreadable),
                'unreadable_ids': sorted(stems & unreadable),
                'password_book_present': (self.store.data / 'password-book.dat').exists(),
                'key_saved': self.key.get() is not None,
                'password_required': False}

    def files(self):
        secured, unreadable = self._states()
        result = []
        for path in self.directory.glob('*.qvault'):
            state = 'secure' if path.stem in secured else 'unreadable' if path.stem in unreadable else 'legacy'
            stat = path.stat()
            result.append({'id': path.stem, 'size': stat.st_size, 'created': stat.st_mtime, 'state': state})
        return sorted(result, key=lambda item: item['created'], reverse=True)

    def _path(self, relative):
        # Only explicitly inventoried image/artifact files may be removed.
        path = self.store.within(self.store.root / relative)
        allowed = [self.store.root / 'outputs', self.store.root / '图片']
        allowed += [self.store.data / n for n in ('thumbs', 'previews', 'trash', 'image-tools')]
        if not any(path.is_relative_to(p.resolve()) for p in allowed):
            raise ValueError('私密文件清理路径不在允许的图片目录内')
        if path.is_symlink() or path.is_dir():
            raise ValueError('私密文件路径发生变化，已停止清理')
        return path

    def _validate(self, payload):
        raw = base64.b64decode(payload['image'], validate=True)
        record = payload['record']
        if not re.fullmatch(r'[a-f0-9]{32}', record['id']):
            raise ValueError('私密图片记录无效')
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('加密图片完整性校验失败，未删除原图')
        return raw

    def _bundle(self, payload):
        self._validate(payload)
        if payload.get('vault_version') == 2:
            return payload
        record = payload['record']
        with self.store.db() as db:
            records = [dict(r) for r in db.execute('SELECT * FROM images WHERE sha256=?', (record['sha256'],))]
            jobs = [json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs')]
        # Old unlinked copies can still identify their original through the authenticated record.
        if not any(r['id'] == record['id'] for r in records):
            records.append(record)
        ids = {r['id'] for r in records}
        entries = {}
        for item in records:
            original = self._path(item['path'])
            candidates = [(original, 'master'), (original.with_suffix('.json'), 'sidecar'),
                          (original.with_suffix('.prompt.txt'), 'sidecar'),
                          (self.store.data / 'thumbs' / f"{item['id']}.webp", 'cache'),
                          (self.store.data / 'previews' / f"{item['id']}.png", 'cache'),
                          (self.store.data / 'trash' / f"{item['id']}{original.suffix}", 'master')]
            # Seam comparisons belong to the same private output, not independent artwork.
            if (original.name == 'output.png' and original.parent.parent == self.store.root / 'outputs' / 'outpaint'
                    and re.fullmatch(r'[a-f0-9]{32}', original.parent.name)):
                candidates.extend((original.parent / name, 'sidecar')
                                  for name in ('strict.png', 'comparison.png'))
            for path, kind in candidates:
                path = self._path(path)
                if not path.is_file():
                    continue
                relative = str(path.relative_to(self.store.root))
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if kind == 'master' and digest != record['sha256']:
                    raise ValueError('原图已发生变化，已停止迁移以避免丢失修改')
                entries[relative] = {'path': relative, 'sha256': digest, 'kind': kind}
                if kind != 'master':
                    entries[relative]['data'] = base64.b64encode(content).decode()
        # New workflow uploads retain original bytes. Include exact copies and their
        # descriptors in the authenticated cleanup inventory, never derived images.
        tool_inputs = self.store.data / 'image-tools'
        for descriptor in tool_inputs.glob('*.json'):
            try:
                item = json.loads(descriptor.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if item.get('sha256') != record['sha256'] and item.get('source_image_id') not in ids:
                continue
            candidates = [(descriptor, 'cache')]
            if not item.get('source_image_id'):
                candidates.append((descriptor.with_suffix('.png'), 'master'))
            for path, kind in candidates:
                path = self._path(path)
                if not path.is_file():
                    continue
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if kind == 'master' and digest != record['sha256']:
                    raise ValueError('工作流输入副本已变化，未清理明文')
                relative = str(path.relative_to(self.store.root))
                entries[relative] = {'path': relative, 'sha256': digest, 'kind': kind}
                if kind != 'master':
                    entries[relative]['data'] = base64.b64encode(content).decode()
        related = [j for j in jobs if ids.intersection(j.get('image_ids', []))]
        if any(j.get('status') in ('queued', 'running') for j in related):
            raise ValueError('请等待相关生成任务完成后再加密')
        return {**payload, 'vault_version': 2, 'cleanup_files': list(entries.values()),
                'image_ids': sorted(ids), 'jobs': related}

    def _write_verified(self, identity, payload, password):
        self._validate(payload)
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        salt, nonce = os.urandom(16), os.urandom(12)
        key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode())
        header = b'QSTUDIO2' + salt + nonce
        encrypted = header + AESGCM(key).encrypt(nonce, raw, header)
        target = self.directory / f'{identity}.qvault'
        temp = target.with_name(target.name + '.writing')
        try:
            with temp.open('wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            actual = temp.read_bytes()
            if actual != encrypted or AESGCM(key).decrypt(actual[24:36], actual[36:], actual[:36]) != raw:
                raise ValueError('加密文件写入验证失败，原图未删除')
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def _cleanup(self, identity, payload):
        self._validate(payload)
        files = []
        for entry in payload['cleanup_files']:
            path = self._path(entry['path'])
            if path.exists():
                if sha256(path) != entry['sha256']:
                    raise ValueError('待清理文件已改变；加密副本已保留，请检查后重试')
                files.append(path)
        # Fail closed in the normal gallery even if a later unlink is denied.
        with self.store.db() as db:
            for image_id in payload['image_ids']:
                db.execute('INSERT OR REPLACE INTO vault_images VALUES (?,?)', (image_id, identity))
        for path in files:
            path.unlink(missing_ok=True)
        with self.store.db() as db:
            for image_id in payload['image_ids']:
                db.execute('DELETE FROM images WHERE id=?', (image_id,))
            for job in payload['jobs']:
                db.execute('INSERT OR IGNORE INTO private_jobs VALUES (?)', (job['id'],))
                db.execute('DELETE FROM jobs WHERE id=?', (job['id'],))
            db.execute('INSERT OR IGNORE INTO vault_secure VALUES (?)', (identity,))

    def _compact(self):
        with self.store.db() as db:
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')

    def _password(self, password=None, missing='尚未保存私密库密码，请先设置一次'):
        if password:
            return password
        saved = self.key.get()
        if not saved:
            raise ValueError(missing)
        return saved

    def save_password(self, password):
        """Remember one password for future encrypt / unlock, after proving it works."""
        with self.store.lock:
            secured, _ = self._states()
            existing = [path.stem for path in self.directory.glob('*.qvault') if path.stem in secured]
            if existing:
                try:
                    self.store.decrypt(existing[0], password)
                except InvalidTag:
                    raise ValueError('这个密码打不开已有私密文件；如果确实换了密码，请用「更换密码」而不是重新设置。') from None
            self.key.set(password)
            return self.status()

    def rekey(self, password, current=None):
        """Re-encrypt every secure file to a new password; nothing changes if any file fails."""
        with self.store.lock:
            current = self._password(current, '换密码前需要先保存或输入当前密码')
            secured, _ = self._states()
            targets = [path.stem for path in self.directory.glob('*.qvault') if path.stem in secured]
            for identity in targets:
                try:
                    self.store.decrypt(identity, current)
                except InvalidTag:
                    raise ValueError('当前密码无法解开全部私密文件，未做任何修改') from None
            for identity in targets:
                self._write_verified(identity, self._bundle(self.store.decrypt(identity, current)), password)
            self.key.set(password)
            return {'rekeyed': len(targets), **self.status()}

    def encrypt(self, image_id, password=None):
        with self.store.lock:
            if self.status()['pending'] or self.status()['password_book_present']:
                raise ValueError('请先在私密库完成旧文件升级')
            password = self._password(
                password, '尚未保存私密库密码。设置一次之后，加密与查看都不再询问密码。')
            item = self.store.image(image_id)
            source = self.store.file(image_id)
            if source.stat().st_size > 128 * 1024 * 1024:
                raise ValueError('私密库单文件上限 128 MiB')
            payload = self._bundle({'image': base64.b64encode(source.read_bytes()).decode(), 'record': item})
            identity = uuid.uuid4().hex
            self._write_verified(identity, payload, password)
            self._cleanup(identity, payload)
            self._compact()
            return {'id': identity, 'original_retained': False, 'archived': True}

    def migrate(self, password, legacy_password=None, keep_unreadable=False):
        """Re-key every legacy file to one password and delete the plaintext sources.

        Files that no known password can open stay encrypted, are recorded as
        unreadable and are never deleted. Until they are explicitly kept, the
        migration refuses to touch anything, so a single unknown password can
        never silently strand the rest of the library.
        """
        with self.store.lock:
            book_path = self.store.data / 'password-book.dat'
            legacy_keys = []
            if book_path.exists():
                legacy_keys = [e['password'] for e in PasswordBook(self.store.data)._read()['entries']]
            keys = list(dict.fromkeys([password] + ([legacy_password] if legacy_password else []) + legacy_keys))
            with self.store.db() as db:
                secured = {r[0] for r in db.execute('SELECT vault_id FROM vault_secure')}
            targets = [p.stem for p in self.directory.glob('*.qvault') if p.stem not in secured or book_path.exists()]

            def read(identity):
                for key in keys:
                    try:
                        return self._bundle(self.store.decrypt(identity, key))
                    except InvalidTag:
                        continue
                raise InvalidTag(identity)

            # Preflight every file before changing any ciphertext or deleting anything.
            readable, unreadable = [], []
            for identity in targets:
                try:
                    read(identity)
                except InvalidTag:
                    unreadable.append(identity)
                    continue
                readable.append(identity)
            if unreadable and not keep_unreadable:
                raise ValueError(
                    f'有 {len(unreadable)} 个旧加密文件无法用现有密码解锁（可能来自更早、未保存的密码）。'
                    '勾选「保留无法解锁的旧文件」可以先把其余文件升级完：这些文件保持加密、不会被删除，'
                    '也不能在页面查看；未勾选时不会改动任何文件。')
            # Stage all replacements first. A retry accepts the new password as well as old keys.
            for identity in readable:
                self._write_verified(identity, read(identity), password)
            for identity in readable:
                self._cleanup(identity, self.store.decrypt(identity, password))
            if unreadable:
                with self.store.db() as db:
                    for identity in unreadable:
                        db.execute('INSERT OR REPLACE INTO vault_unreadable VALUES (?,?,?)',
                                   (identity, 'no known password; ciphertext retained', time.time()))
            self._compact()
            if self.status()['pending']:
                raise ValueError('仍有未完成的私密文件，密码本已保留，请重试')
            # No credentials are retained after successful, verified migration.
            book_path.unlink(missing_ok=True)
            for temp in self.store.data.glob('password-book.dat.*.tmp'):
                temp.unlink(missing_ok=True)
            # Keep the chosen password for later encrypt / unlock, so it is asked only once.
            self.key.set(password)
            return {'migrated': len(readable), 'kept': unreadable, **self.status()}

    def unlock(self, identity, password=None):
        with self.store.db() as db:
            if db.execute('SELECT 1 FROM vault_unreadable WHERE vault_id=?', (identity,)).fetchone():
                raise ValueError('这个文件来自更早的密码，现在无法解密；它保持加密保存，不会被删除。')
            if not db.execute('SELECT 1 FROM vault_secure WHERE vault_id=?', (identity,)).fetchone():
                raise ValueError('该文件尚未完成升级，请先完成私密库迁移')
        password = self._password(password, '尚未保存私密库密码，请输入密码打开这个文件')
        payload = self.store.decrypt(identity, password)
        self._validate(payload)
        return {'image': payload['image'], 'record': payload['record']}
