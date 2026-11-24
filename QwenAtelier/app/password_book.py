"""Local password book; only metadata crosses the normal API boundary."""
import json
import os
import threading
import uuid

from .assistant import _dpapi


class PasswordBook:
    def __init__(self, data):
        self.path = data / 'password-book.dat'
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {'default': None, 'entries': [], 'bindings': {}}
        try:
            return json.loads(_dpapi(self.path.read_bytes(), decrypt=True))
        except (OSError, ValueError):
            raise ValueError('无法读取密码本，请使用保存密码时的 Windows 账户；仍可手动输入图片密码') from None

    def _write(self, book):
        try:
            raw = _dpapi(json.dumps(book, ensure_ascii=False).encode('utf-8'))
        except OSError:
            raise ValueError('Windows 密码保护不可用，密码未保存，请检查当前账户') from None
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_bytes(raw)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def status(self):
        with self.lock:
            book = self._read()
            return {'default': book['default'], 'entries': [
                {'id': item['id'], 'name': item['name']} for item in book['entries']]}

    def add(self, name, password):
        with self.lock:
            book = self._read()
            identity = uuid.uuid4().hex
            book['entries'].append({'id': identity, 'name': name.strip() or '默认密码', 'password': password})
            book['default'] = identity
            self._write(book)
            return self.status()

    def set_default(self, identity):
        with self.lock:
            book = self._read()
            if not any(item['id'] == identity for item in book['entries']):
                raise KeyError('密码条目不存在')
            book['default'] = identity
            self._write(book)
            return self.status()

    def resolve(self, vault_id=None):
        with self.lock:
            book = self._read()
            identity = book['bindings'].get(vault_id) if vault_id else book['default']
            item = next((item for item in book['entries'] if item['id'] == identity), None)
            if item is None:
                raise ValueError('请先设置密码本默认密码' if not vault_id else '此图片尚未关联密码本，请手动输入密码')
            return item['id'], item['password']

    def bind(self, vault_id, entry_id):
        with self.lock:
            book = self._read()
            book['bindings'][vault_id] = entry_id
            self._write(book)
