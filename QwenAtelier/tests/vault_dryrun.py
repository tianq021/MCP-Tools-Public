"""Rehearse the private-vault migration on a copy of the real library.

The real files are only read. Everything happens in a throwaway directory:
the database, the encrypted files, the password book and the plaintext
sources are copied there, the paths are rewritten, and the migration runs
exactly as the web page would. Nothing in `data/`, `outputs/` or `图片/`
is deleted or modified.

    .\\.venv\\Scripts\\python.exe tests\\vault_dryrun.py
    .\\.venv\\Scripts\\python.exe tests\\vault_dryrun.py --keep
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.storage import Store  # noqa: E402
from app.vault import Vault  # noqa: E402


def copy_private_sources(source, work):
    """Copy only what the vault migration touches, and point the copy at itself."""
    data, target = source / 'data', work / 'data'
    for name in ('thumbs', 'previews', 'trash', 'vault', 'inputs', 'logs'):
        (target / name).mkdir(parents=True, exist_ok=True)
    (work / 'outputs').mkdir(parents=True, exist_ok=True)
    (work / '图片').mkdir(parents=True, exist_ok=True)

    read_only = f'file:{(data / "studio.sqlite3").as_posix()}?mode=ro'
    db = sqlite3.connect(read_only, uri=True)
    db.row_factory = sqlite3.Row
    copied = 0
    try:
        rows = db.execute('''SELECT i.path FROM vault_images v JOIN images i ON i.id = v.image_id''').fetchall()
        paths = [Path(row['path']) for row in rows]
    finally:
        db.close()
    shutil.copy2(data / 'studio.sqlite3', target / 'studio.sqlite3')

    wanted = []
    for path in paths:
        wanted += [path, path.with_suffix('.json'), path.with_suffix('.prompt.txt')]
    for path in wanted:
        if path.is_file():
            relative = path.relative_to(source)
            destination = work / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1
    for folder in ('thumbs', 'previews'):
        for item in (data / folder).glob('*'):
            if item.is_file():
                shutil.copy2(item, target / folder / item.name)
                copied += 1
    for item in (data / 'vault').glob('*.qvault'):
        shutil.copy2(item, target / 'vault' / item.name)
    book = data / 'password-book.dat'
    if book.is_file():
        shutil.copy2(book, target / 'password-book.dat')
    return copied, len(paths)


def audit(work, private_paths, private_sha):
    """Return every plaintext file under the copy that still belongs to a private image."""
    found = []
    sidecars = set()
    for path in private_paths:
        sidecars.update({path.with_suffix('.json').name, path.with_suffix('.prompt.txt').name})
    for folder in ('outputs', '图片', 'data/thumbs', 'data/previews', 'data/trash', 'data/inputs'):
        path = work / folder
        if not path.is_dir():
            continue
        for item in path.iterdir():
            if not item.is_file():
                continue
            if item.name in sidecars:
                found.append(item)
                continue
            if item.stat().st_size < 1024:
                continue
            with item.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() in private_sha:
                    found.append(item)
    return found


def main():
    parser = argparse.ArgumentParser(description='在副本上预演私密库迁移')
    parser.add_argument('--source', default=str(ROOT))
    parser.add_argument('--work', default=str(ROOT / '.tmp' / 'vault-dryrun'))
    parser.add_argument('--keep', action='store_true', help='保留副本目录')
    arguments = parser.parse_args()
    source, work = Path(arguments.source).resolve(), Path(arguments.work).resolve()
    if source == work or not work.is_relative_to(source):
        raise SystemExit('副本目录必须位于项目目录内，且不能等于项目目录')

    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    copied, private = copy_private_sources(source, work)
    print(f'已复制 {copied} 个明文文件与全部加密文件；私密记录 {private} 条。')

    store = Store(work)
    vault = Vault(store)
    # Encrypted legacy payloads still contain the original absolute paths. Map that
    # prefix onto the copy so the rehearsal exercises the same code path; the patch
    # lives only in this script and never touches the real files.
    original_path = Vault._path

    def mapped(self, relative):
        path = Path(relative)
        if not path.is_absolute():
            path = work / path
        resolved = path.resolve()
        # Paths already inside the copy are left alone; only the original host
        # prefix is rewritten, so the rehearsal still walks the same branches.
        if not resolved.is_relative_to(work) and resolved.is_relative_to(source):
            path = work / resolved.relative_to(source)
        return original_path(self, path)

    Vault._path = mapped
    before = vault.status()
    print(f'迁移前：{before["total"]} 个加密文件，{before["pending"]} 个待升级，'
          f'密码本{"存在" if before["password_book_present"] else "不存在"}。')

    password = 'dryrun-' + secrets.token_hex(12)
    result = vault.migrate(password, keep_unreadable=True)
    print(f'迁移结果：升级 {result["migrated"]} 个，保留无法解锁 {len(result["kept"])} 个，'
          f'待升级 {result["pending"]} 个。')

    with sqlite3.connect(f'file:{(source / "data" / "studio.sqlite3").as_posix()}?mode=ro', uri=True) as db:
        originals = [row[0] for row in db.execute(
            'SELECT i.sha256 FROM vault_images v JOIN images i ON i.id=v.image_id')]
        private_paths = [Path(row[0]) for row in db.execute(
            'SELECT i.path FROM vault_images v JOIN images i ON i.id=v.image_id')]
    # Every surviving secure file must decrypt with the new password and match its record hash.
    verified, mismatch = 0, []
    for item in vault.files():
        if item['state'] != 'secure':
            continue
        payload = vault.unlock(item['id'], password)
        raw = base64.b64decode(payload['image'])
        if hashlib.sha256(raw).hexdigest() == payload['record']['sha256']:
            verified += 1
        else:
            mismatch.append(item['id'])
    print(f'用新密码逐个校验：{verified} 个通过，{len(mismatch)} 个不匹配。')

    leftover = audit(work, private_paths, {item for item in originals if item})
    print(f'副本内仍能直接打开的私密明文：{len(leftover)} 个')
    database = work / 'data' / 'studio.sqlite3'
    raw = database.read_bytes() if database.is_file() else b''
    db_hits = [str(path) for path, digest in zip(private_paths, originals)
               if digest and digest.encode() in raw and str(path).encode() in raw]
    print(f'副本数据库里仍留有原图路径的私密记录：{len(db_hits)} 条')
    book = work / 'data' / 'password-book.dat'
    print(f'旧密码本：{"仍存在" if book.exists() else "已删除"}')

    ok = (not mismatch and not leftover and not db_hits and not book.exists() and result['pending'] == 0
          and verified == result['migrated'])
    print('结论：' + ('迁移流程在真实数据副本上通过，可以放心在网页上执行。' if ok
                      else '迁移流程存在问题，请勿在真实数据上执行。'))
    if not arguments.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f'副本保留在 {work}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
