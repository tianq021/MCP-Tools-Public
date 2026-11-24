# Build using: python -m PyInstaller --noconfirm BL4Trainer.spec
from pathlib import Path
root = Path(SPECPATH)
a = Analysis(
    [str(root / 'trainer_gui.py')],
    pathex=[str(root / 'src'), str(root / 'tools')],
    binaries=[],
    datas=[(str(root / 'trainer_config.json'), '.'),
           (str(root / 'research/reflection_signatures.local.json'), 'research')],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='BL4Trainer',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, uac_admin=False)
