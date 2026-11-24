"""Audit that private images have no leftover plaintext on this machine.

Read-only: it never decrypts, moves or deletes anything. Run it before and
after the one-time vault migration to see what is still readable outside the
encrypted files.

    .\\.venv\\Scripts\\python.exe scripts\\audit_private_plaintext.py
    .\\.venv\\Scripts\\python.exe scripts\\audit_private_plaintext.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
MEDIA = ('.png', '.jpg', '.jpeg', '.webp')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private_records():
    database = DATA / 'studio.sqlite3'
    if not database.is_file():
        return []
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_images'").fetchone():
            return []
        rows = db.execute('''SELECT v.vault_id, i.id, i.path, i.sha256
                             FROM vault_images v LEFT JOIN images i ON i.id = v.image_id''').fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def audit():
    records = private_records()
    families = ('master', 'thumb', 'preview', 'sidecar', 'trash', 'input', 'database')
    leftovers = {key: [] for key in families}
    if not records:
        return {'private': 0, 'leftovers': leftovers, 'counts': {key: 0 for key in families}, 'clean': True}

    ids = {record['id'] for record in records if record['id']}
    hashes = {record['sha256'] for record in records if record['sha256']}
    sidecars = set()
    for record in records:
        if not record['path']:
            continue
        original = Path(record['path'])
        sidecars.update({original.with_suffix('.json').resolve(), original.with_suffix('.prompt.txt').resolve()})

    def bucket(folder):
        return {'thumbs': 'thumb', 'previews': 'preview', 'trash': 'trash', 'inputs': 'input', 'image-tools': 'input'}.get(folder, 'master')

    folders = [ROOT / folder for folder in ('outputs', '图片', 'data/thumbs', 'data/previews', 'data/trash', 'data/inputs', 'data/image-tools')]
    for mode in ('upscale', 'outpaint'):
        folders.extend(p for p in (ROOT / 'outputs' / mode).glob('*') if p.is_dir() and not p.is_symlink())
    for path in folders:
        if not path.is_dir():
            continue
        for item in path.iterdir():
            if not item.is_file():
                continue
            name = item.name
            try:
                relative = item.relative_to(ROOT).as_posix()
            except ValueError:
                relative = str(item)
            if item.resolve() in sidecars:
                leftovers['sidecar'].append(relative)
                continue
            if item.suffix.lower() not in MEDIA:
                continue
            # Cache and trash copies are named after the image id.
            if item.stem in ids:
                leftovers[bucket(path.name)].append(relative)
                continue
            if item.stat().st_size > 0:
                try:
                    if digest(item) in hashes:
                        leftovers['master'].append(relative)
                except OSError:
                    continue

    database = DATA / 'studio.sqlite3'
    if database.is_file():
        raw = database.read_bytes()
        for record in records:
            # The hash alone also lives inside encrypted payloads; a surviving row keeps
            # both the hash and the original path in the clear.
            if record['sha256'] and record['path'] and record['sha256'].encode() in raw and record['path'].encode() in raw:
                leftovers['database'].append(record['id'] or 'unknown')

    counts = {key: len(value) for key, value in leftovers.items()}
    return {'private': len(records), 'leftovers': leftovers, 'counts': counts, 'clean': sum(counts.values()) == 0}


def main():
    parser = argparse.ArgumentParser(description='检查私密图片是否还有可在本机直接打开的明文文件')
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    arguments = parser.parse_args()
    report = audit()
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['clean'] else 1
    if not report['private']:
        print('私密库里还没有已关联的图片记录。')
        return 0
    print(f'私密图片记录：{report["private"]} 条')
    labels = {'master': '明文原图', 'thumb': '缩略图', 'preview': '8 位预览图', 'sidecar': '同名配置 JSON',
              'trash': '回收站明文', 'input': '参考图明文', 'database': '数据库明文记录'}
    for key, label in labels.items():
        items = report['leftovers'][key]
        print(f'  {label}：{len(items)} 个' + (f'  例如 {items[0]}' if items else ''))
    print('结论：' + ('未发现可在本机直接打开的私密明文文件。' if report['clean']
                     else '仍有明文可直接打开，需要完成私密库迁移。'))
    return 0 if report['clean'] else 1


if __name__ == '__main__':
    sys.exit(main())
