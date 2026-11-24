import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Option:
    def __init__(self, caption, description, value, *args):
        self.CurrentValue = value


class BonusTests(unittest.TestCase):
    def setUp(self):
        sdk = types.ModuleType('unrealsdk')
        sdk.Log = Mock()
        sdk.RegisterHook = Mock()
        def run_hook(target, name, callback):
            if not isinstance(callback, types.FunctionType):
                raise TypeError('Object passed to hook is not a function')
        sdk.RunHook = Mock(side_effect=run_hook)
        sdk.RemoveHook = Mock()
        menu = types.ModuleType('Mods.ModMenu')
        menu.SDKMod = object
        menu.ModTypes = types.SimpleNamespace(Utility=1)
        menu.EnabledSaveType = types.SimpleNamespace(LoadWithSettings=1)
        menu.Keybind = lambda *a: a
        menu.RegisterMod = Mock()
        menu.Options = types.SimpleNamespace(Field=object, Boolean=Option, Slider=Option, Spinner=Option)
        mods = types.ModuleType('Mods')
        root = Path(__file__).resolve().parents[1]/'launcher-assets/qol/SessionInspector'
        spec = importlib.util.spec_from_file_location('test_inspector', root/'__init__.py', submodule_search_locations=[str(root)])
        self.module = importlib.util.module_from_spec(spec)
        self.patch = patch.dict(sys.modules, {'unrealsdk': sdk, 'Mods': mods,
            'Mods.ModMenu': menu, 'test_inspector': self.module})
        self.patch.start(); self.addCleanup(self.patch.stop)
        spec.loader.exec_module(self.module)
        self.mod = self.module.SessionInspector()
        self.pc = types.SimpleNamespace(Pawn=types.SimpleNamespace(GetExpLevel=lambda: 4))
        self.module.player = lambda: self.pc
        self.mod.actions.identity = lambda pc: {'save_id': 1}
        self.mod.allow_changes.CurrentValue = True
        self.mod.xp_percent.CurrentValue = 50
        self.params = types.SimpleNamespace(Exp=100, Source=0, ExpType=0)

    def test_bonus_does_not_replace_original_and_prevents_recursion(self):
        calls = []
        def earn(amount, source, exp_type):
            calls.append(amount)
            self.mod.bonus_hook(self.pc, None, self.params)
        self.pc.ExpEarn = earn
        self.assertTrue(self.mod.bonus_hook(self.pc, None, self.params))
        self.assertEqual(calls, [])
        self.mod.flush_bonus(self.pc)
        self.assertEqual(calls, [50])
        self.assertFalse(self.mod.bonus_in_progress)
        self.assertFalse(self.mod.pending_bonus)

    def test_disabled_full_level_and_identity_change_skip(self):
        self.pc.ExpEarn = Mock()
        self.mod.bonus_hook(self.pc, None, self.params)
        self.mod.actions.identity = lambda pc: {'save_id': 2}
        self.mod.flush_bonus(self.pc)
        self.pc.ExpEarn.assert_not_called()
        self.pc.Pawn.GetExpLevel = lambda: 80
        self.mod.bonus_hook(self.pc, None, self.params)
        self.assertFalse(self.mod.pending_bonus)
        self.pc.Pawn.GetExpLevel = lambda: 4
        self.mod.allow_changes.CurrentValue = False
        self.mod.bonus_hook(self.pc, None, self.params)
        self.assertFalse(self.mod.pending_bonus)

    def test_failed_bonus_is_not_retried_and_clears_guard(self):
        self.pc.ExpEarn = Mock(side_effect=RuntimeError('test'))
        self.mod.bonus_hook(self.pc, None, self.params)
        with self.assertRaises(RuntimeError):
            self.mod.flush_bonus(self.pc)
        self.assertFalse(self.mod.bonus_in_progress)
        self.assertFalse(self.mod.pending_bonus)

    def test_local_hotkey_works_without_mcp(self):
        self.mod.allow_mcp.CurrentValue = False
        self.mod.local_action.CurrentValue = '设置剩余技能点'
        self.mod.skill_points.CurrentValue = 25
        self.pc.PlayerReplicationInfo = types.SimpleNamespace(GeneralSkillPoints=0)
        self.mod.GameInputPressed(types.SimpleNamespace(Name='Apply selected toolbox action'))
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 25)

    def test_enable_passes_plain_functions_to_legacy_sdk(self):
        self.mod.Enable()
        sdk = sys.modules['unrealsdk']
        self.assertEqual(sdk.RunHook.call_count, 2)
        self.assertEqual(sdk.RunHook.call_args.args[0], 'Engine.PlayerController.PlayerTick')

    def test_local_hotkey_respects_mutation_switch(self):
        self.mod.allow_changes.CurrentValue = False
        self.mod.local_action.CurrentValue = '设置剩余技能点'
        self.pc.PlayerReplicationInfo = types.SimpleNamespace(GeneralSkillPoints=0)
        self.mod.GameInputPressed(types.SimpleNamespace(Name='Apply selected toolbox action'))
        self.assertEqual(self.pc.PlayerReplicationInfo.GeneralSkillPoints, 0)
