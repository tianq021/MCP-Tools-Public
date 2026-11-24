# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_data_files


sys.path.insert(0, 'G:/Codex_mmx/Borderlands/Borderlands3/vendor')
bl3_datas = collect_data_files('bl3save')


a = Analysis(
    ['G:/Codex_mmx/Borderlands/Borderlands3/trainer_gui.py'],
    pathex=['G:/Codex_mmx/Borderlands/Borderlands3/vendor'],
    binaries=[],
    datas=bl3_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BL3Trainer_CN',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
