"""Install reviewed travel/reload modules on the next closed-game launch."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid


def prepare_qol(base, game):
    status = subprocess.check_output(
        ['tasklist', '/FI', 'IMAGENAME eq Borderlands2.exe', '/FO', 'CSV', '/NH'],
        creationflags=subprocess.CREATE_NO_WINDOW)
    if b'borderlands2.exe' in status.lower():
        raise RuntimeError('游戏仍在运行，快速旅行和重载功能将在下次启动时安装。')
    source = base / 'launcher-assets/qol'
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    upgrade_path = source / 'upgrades.json'
    upgrades = json.loads(upgrade_path.read_text(encoding='utf-8')) if upgrade_path.is_file() else {}
    pending = []
    mods = game.parent / 'Mods'
    if not (mods / 'ModMenu/__init__.py').is_file():
        raise RuntimeError('缺少 ModMenu，无法安装快速旅行和重载功能。')
    for name, files in manifest.items():
        if name not in ('BSABT', 'Quickload', 'SessionInspector'):
            raise ValueError('未知模块名')
        for filename, expected in files.items():
            if Path(filename).name != filename or '/' in filename or '\\' in filename:
                raise ValueError('无效模块文件名')
            src = source / name / filename
            if hashlib.sha256(src.read_bytes()).hexdigest() != expected:
                raise RuntimeError('待安装模块校验失败：' + str(src))
            if (mods / name).exists() and filename not in ('settings.json', 'SOURCE.txt'):
                installed = mods / name / filename
                actual = hashlib.sha256(installed.read_bytes()).hexdigest() if installed.is_file() else None
                if actual != expected:
                    if (name == 'SessionInspector'
                            and actual in upgrades.get(name, {}).get(filename, [])):
                        pending.append((installed, src, actual))
                    else:
                        raise RuntimeError('存在其他版本模块，未覆盖：' + name)
    for installed, src, old_hash in pending:
        # Upgrade only our known observer; keep settings and diagnostics intact.
        current_hash = hashlib.sha256(installed.read_bytes()).hexdigest() if installed.is_file() else None
        if current_hash != old_hash:
            raise RuntimeError('模块在校验后发生变化，停止更新。')
        backup = base / 'research-codex' / ('installed-observer-backup-' + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        if installed.is_file():
            shutil.copy2(installed, backup / installed.name)
        descriptor, temporary = tempfile.mkstemp(prefix='.observer-update-', dir=installed.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(src, temporary)
            os.replace(temporary, installed)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for name, files in manifest.items():
        target = mods / name
        if target.exists():
            continue  # Keep the user's enabled state and keybind changes.
        staging = Path(tempfile.mkdtemp(prefix='.qol-prepare-', dir=mods))
        try:
            for filename in files:
                shutil.copyfile(source / name / filename, staging / filename)
            os.rename(staging, target)
        finally:
            if staging.exists():
                for filename in files:
                    (staging / filename).unlink(missing_ok=True)
                staging.rmdir()
