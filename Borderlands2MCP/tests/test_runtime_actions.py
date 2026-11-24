import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime_actions_test', ROOT / 'launcher-assets/qol/SessionInspector/runtime_actions.py')
module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'unrealsdk': types.SimpleNamespace()}):
    spec.loader.exec_module(module)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.actions = module.Actions(self.folder)
        self.identity = {'session': 'test', 'save_id': 1, 'map': 'test'}
        self.actions.identity = Mock(return_value=self.identity)
        self.pc = types.SimpleNamespace(PlayerReplicationInfo=types.SimpleNamespace(GeneralSkillPoints=2))

    def command(self, token='a'*32, **changes):
        inbox = self.folder / 'commands'; inbox.mkdir(exist_ok=True)
        data = {'id': token, 'time': time.time(), 'operation': 'skill_points',
                'expected': self.identity, 'arguments': {'value': 7}}
        data.update(changes)
        (inbox / (token+'.json')).write_text(json.dumps(data))
        return inbox / (token+'.result')

    def test_repeated_request_executes_once(self):
        response = self.command()
        self.actions.poll(self.pc, True)
        self.assertEqual(json.loads(response.read_text())['status'], 'completed')
        self.pc.PlayerReplicationInfo.GeneralSkillPoints = 9
        self.actions.poll(self.pc, True)
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 9)

    def test_completed_requests_do_not_starve_new_requests(self):
        for i in range(9):
            response = self.command(token=format(i, '032x'))
            self.actions.poll(self.pc, True)
            self.assertEqual(json.loads(response.read_text())['status'], 'completed')

    def test_disabled_and_expired_never_mutate(self):
        response = self.command()
        self.actions.poll(self.pc, False)
        self.assertEqual(json.loads(response.read_text())['status'], 'rejected')
        self.command('b'*32, time=time.time()-60)
        self.actions.poll(self.pc, True)
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 2)

    def test_changed_identity_rejected(self):
        self.command(expected={})
        self.actions.poll(self.pc, True)
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 2)

    def test_claim_survives_crash_without_replay(self):
        self.command()
        (self.folder/'commands'/('a'*32+'.claimed')).write_text('claimed')
        self.actions.poll(self.pc, True)
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 2)

    def test_bad_value_never_mutates(self):
        for value in (True, -1, 1000, 2.5):
            with self.assertRaises(ValueError):
                self.actions.execute(self.pc, {'operation':'skill_points',
                    'expected':self.identity,'arguments':{'value':value}})
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 2)

    def test_backpack_200_idempotent_and_preserves_larger_bags(self):
        manager = types.SimpleNamespace(InventorySlotMax_Misc=39, Backpack=[object()]*20)
        pc = types.SimpleNamespace(GetPawnInventoryManager=lambda: manager)
        self.assertTrue(module.ensure_backpack(pc)['verified'])
        self.assertEqual(manager.InventorySlotMax_Misc, 200)
        self.assertEqual(module.ensure_backpack(pc)['before'], 200)
        manager.InventorySlotMax_Misc = 250
        module.ensure_backpack(pc)
        self.assertEqual(manager.InventorySlotMax_Misc, 250)
        manager.InventorySlotMax_Misc = 39
        manager.Backpack = [object()]*220
        module.ensure_backpack(pc)
        self.assertEqual(manager.InventorySlotMax_Misc, 220)

    def test_client_archives_expired_unclaimed_without_replay(self):
        from runtime_client import request
        self.command(time=time.time()-60)
        result = request(timeout=0, folder=self.folder)
        self.assertEqual(result['status'], 'unavailable')
        self.assertTrue((self.folder/'commands/archive'/('a'*32+'.json')).exists())
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 2)

    def test_preview_has_full_definition(self):
        from runtime_client import item_definition
        # Use a known standard weapon fixture instead of relying on catalog shape.
        code = 'BL2(hwAAAABc0wCE5wRABgGF6sNohWEQowQKQgyEGB4wEGIcxNjEkgnl)'
        result = item_definition(code, 4)
        self.assertEqual(len(result['definition']), 17)
        self.assertEqual(result['definition'][3], 4)

    def test_upgrade_keeps_settings_and_installs_new_module(self):
        import hashlib
        import shutil
        import launcher_qol
        base = self.folder/'project'; base.mkdir()
        shutil.copytree(ROOT/'launcher-assets/qol', base/'launcher-assets/qol')
        mods = self.folder/'game/Mods'
        (mods/'ModMenu').mkdir(parents=True)
        (mods/'ModMenu/__init__.py').write_text('')
        observer = mods/'SessionInspector'; observer.mkdir()
        (observer/'__init__.py').write_text('old')
        (observer/'settings.json').write_text('{"custom":true}')
        upgrade = base/'launcher-assets/qol/upgrades.json'
        data = json.loads(upgrade.read_text())
        data['SessionInspector']['__init__.py'].append(hashlib.sha256(b'old').hexdigest())
        upgrade.write_text(json.dumps(data))
        with patch.object(launcher_qol.subprocess, 'check_output', return_value=b''):
            launcher_qol.prepare_qol(base, self.folder/'game/Borderlands2.exe')
        self.assertEqual((observer/'settings.json').read_text(), '{"custom":true}')
        self.assertTrue((observer/'runtime_actions.py').is_file())
        self.assertTrue(list((base/'research-codex').glob('installed-observer-backup-*')))


if __name__ == '__main__':
    unittest.main()
