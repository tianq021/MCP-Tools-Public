import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import apply_verified_adjustments as adjust


class BackupTests(unittest.TestCase):
    def test_backup_preserves_bytes_and_nested_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source'; file=source/'account/Profiles/client/1.sav'
            file.parent.mkdir(parents=True); file.write_bytes(b'original save bytes')
            with patch.object(adjust,'ROOT',root/'project'):
                target=adjust.backup_saves(source)
            self.assertEqual((target/'SaveGames/account/Profiles/client/1.sav').read_bytes(),file.read_bytes())
            manifest=json.loads((target/'manifest.json').read_text())
            self.assertTrue(manifest['game_running'])
            self.assertEqual(len(manifest['sha256']),1)

    def test_save_changed_during_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source';source.mkdir();file=source/'1.sav';file.write_bytes(b'old')
            original=adjust.shutil.copy2
            def copy_then_change(src,dst):
                original(src,dst);Path(src).write_bytes(b'new')
            with patch.object(adjust,'ROOT',root/'project'),patch.object(adjust.shutil,'copy2',side_effect=copy_then_change):
                with self.assertRaisesRegex(RuntimeError,'changed during backup'):
                    adjust.backup_saves(source)

    def test_empty_save_root_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError,'No save files'):
                adjust.backup_saves(Path(temp))


if __name__=='__main__':unittest.main()
