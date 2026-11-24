"""Prepare the pinned legacy Offline Helpers mod before game startup only."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE_HASH = 'a5b6763ab994734d593e70c46757357995a28a0725f6f634a42c9cdbcdc615fd'


def prepare_offline(base: Path, game: Path, enabled: bool):
    result = subprocess.check_output(
        ['tasklist', '/FI', 'IMAGENAME eq Borderlands2.exe', '/FO', 'CSV', '/NH'],
        creationflags=subprocess.CREATE_NO_WINDOW)
    if b'borderlands2.exe' in result.lower():
        raise RuntimeError('游戏正在运行，离线选项将在退出后重新启动时应用。')
    mods = game.parent / 'Mods'
    target = mods / 'OfflineHelpers'
    if not target.exists() and not enabled:
        return
    if not (mods / 'ModMenu/__init__.py').is_file():
        raise RuntimeError('缺少基础 SDK ModMenu，无法应用离线设置。')
    source = base / 'launcher-assets/OfflineHelpers'
    if hashlib.sha256((source / '__init__.py').read_bytes()).hexdigest() != SOURCE_HASH:
        raise RuntimeError('离线模块来源校验失败，未启动游戏。')
    if target.exists():
        if hashlib.sha256((target / '__init__.py').read_bytes()).hexdigest() != SOURCE_HASH:
            raise RuntimeError('已有不同版本 Offline Helpers，未覆盖，请先检查兼容性。')
        settings_file = target / 'settings.json'
        previous = settings_file.read_bytes() if settings_file.exists() else None
        settings = json.loads(previous.decode('utf-8')) if previous is not None else {}
    else:
        previous = None
        settings = {}
    settings.setdefault('Options', {})['Force Offline Mode (ADVANCED)'] = enabled
    # Keep connection warnings visible so failures are not silently hidden.
    settings['Options']['Hide Offline Warning'] = False
    settings['AutoEnable'] = enabled
    content = json.dumps(settings, ensure_ascii=False, indent=2).encode('utf-8')
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix='.offline-prepare-', dir=mods))
        try:
            for name in ('__init__.py', 'LICENSE'):
                shutil.copyfile(source / name, staging / name)
            (staging / 'settings.json').write_bytes(content)
            os.rename(staging, target)
        finally:
            if staging.exists():
                for name in ('__init__.py', 'LICENSE', 'settings.json'):
                    (staging / name).unlink(missing_ok=True)
                staging.rmdir()
    elif previous != content:
        if previous is not None:
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            (target / ('settings.launcher-' + stamp + '.bak')).write_bytes(previous)
        tmp = target / 'settings.launcher.tmp'
        tmp.write_bytes(content)
        os.replace(tmp, target / 'settings.json')
