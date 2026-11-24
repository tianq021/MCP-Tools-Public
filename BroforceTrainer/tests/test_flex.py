import ctypes
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import trainer_gui as gui


class FlexPowerTests(unittest.TestCase):
    def setUp(self):
        self.memory = ctypes.create_string_buffer(gui.SHARED_MEMORY_SIZE)
        self.view = ctypes.addressof(self.memory)

    def test_flex_region_is_absolute_and_isolated(self):
        for player in range(4):
            gui.set_flex_power(self.view, 10 + player, player)
            ctypes.c_int32.from_address(gui.flex_status_address(self.view, player)).value = player
        for player in range(4):
            self.assertEqual(gui.read_flex_request(self.view, player), 10 + player)
            self.assertEqual(gui.read_flex_status(self.view, player), player)
        # The flex region must not overlap the V14 player slots (0..511).
        self.assertEqual(self.memory.raw[:gui.FLEX_REQUEST_BASE_OFFSET],
                         bytes(gui.FLEX_REQUEST_BASE_OFFSET))
        self.assertEqual(gui.SHARED_MEMORY_SIZE, 640)

    def test_invalid_power_and_player_are_rejected_without_writing(self):
        before = self.memory.raw
        for pickup in (-1, 1, 9, 15, 99):
            with self.assertRaises(ValueError):
                gui.set_flex_power(self.view, pickup, 0)
        for player in (-1, 4):
            with self.assertRaises(ValueError):
                gui.set_flex_power(self.view, 11, player)
            with self.assertRaises(ValueError):
                gui.read_flex_request(self.view, player)
            with self.assertRaises(ValueError):
                gui.read_flex_status(self.view, player)
        self.assertEqual(self.memory.raw, before)

    def test_close_stops_maintaining_flex_and_keeps_other_features(self):
        for player in range(4):
            gui.set_flex_power(self.view, 11, player)
            gui.request_hero_lock(self.view, 10 + player, player)
        gui.pulse_combat_client(self.view)
        gui.stop_combat_assists(self.view)
        self.assertEqual(gui.read_shared_uint64(self.view, gui.CLIENT_HEARTBEAT_OFFSET), 0)
        for player in range(4):
            self.assertEqual(gui.read_flex_request(self.view, player), 0)
            self.assertEqual(gui.read_hero_lock_target(self.view, player), 10 + player)

    def test_button_grants_then_toggles_off(self):
        app = SimpleNamespace(pid=1, shared_view=self.view, selected_player=1,
                              flex_choice_var=Mock(), result_var=Mock(), connecting=False)
        app.flex_choice_var.get.return_value = gui.FLEX_POWER_LABELS[12]
        with patch.object(gui, 'monitor_is_ready', return_value=True):
            gui.TrainerApp.apply_flex_power(app)
            self.assertEqual(gui.read_flex_request(self.view, 1), 12)
            self.assertEqual(gui.read_flex_request(self.view, 0), 0)
            # Pressing again with the same choice cancels it.
            gui.TrainerApp.apply_flex_power(app)
            self.assertEqual(gui.read_flex_request(self.view, 1), 0)
            # Switching selection while another power is held replaces it.
            app.flex_choice_var.get.return_value = gui.FLEX_POWER_LABELS[11]
            gui.TrainerApp.apply_flex_power(app)
            self.assertEqual(gui.read_flex_request(self.view, 1), 11)
            app.flex_choice_var.get.return_value = gui.FLEX_POWER_LABELS[14]
            gui.TrainerApp.apply_flex_power(app)
            self.assertEqual(gui.read_flex_request(self.view, 1), 14)

    def test_unready_gui_never_publishes_flex_command(self):
        app = SimpleNamespace(pid=1, shared_view=self.view, selected_player=0,
                              flex_choice_var=Mock(), result_var=Mock(), connecting=False)
        app.flex_choice_var.get.return_value = gui.FLEX_POWER_LABELS[11]
        with patch.object(gui, 'monitor_is_ready', return_value=False):
            gui.TrainerApp.apply_flex_power(app)
        self.assertEqual(self.memory.raw, bytes(len(self.memory.raw)))

    def test_f11_focus_and_hold_are_single_press(self):
        app = SimpleNamespace(pid=1, game_window=123, was_f6_down=False, was_f7_down=False,
                              was_f8_down=False, was_f9_down=False, was_f10_down=False,
                              was_f11_down=False, root=Mock(), _hotkey_tick=Mock(),
                              add_life=Mock(), add_ironbro_life=Mock(), spawn_villager=Mock(),
                              spawn_companion=Mock(), refill_ammo=Mock(), apply_flex_power=Mock())
        with patch.object(gui.user32, 'GetAsyncKeyState',
                          side_effect=lambda key: 0x8000 if key == gui.VK_F11 else 0), \
             patch.object(gui.user32, 'GetForegroundWindow', return_value=456) as foreground:
            gui.TrainerApp._hotkey_tick(app)
            foreground.return_value = 123
            gui.TrainerApp._hotkey_tick(app)
            app.apply_flex_power.assert_not_called()
            app.was_f11_down = False
            gui.TrainerApp._hotkey_tick(app)
            gui.TrainerApp._hotkey_tick(app)
            app.apply_flex_power.assert_called_once()

    def test_alluring_option_is_hidden_from_the_ui_but_protocol_still_accepts_it(self):
        self.assertNotIn(14, [power_id for power_id, unused in gui.FLEX_POWER_VISIBLE_OPTIONS])
        self.assertIn(14, gui.FLEX_POWER_IDS_VALID)
        gui.set_flex_power(self.view, 14, 0)  # bridge protocol still supports it
        self.assertEqual(gui.read_flex_request(self.view, 0), 14)
        root = tk.Tk()
        root.withdraw()
        app = gui.TrainerApp(root)
        app.pid = 1
        app.shared_view = self.view
        ctypes.c_uint64.from_address(self.view + gui.MONITOR_READY_OFFSET).value = 1
        try:
            app._refresh_combat_controls()
            self.assertNotIn(gui.FLEX_POWER_LABELS[14], app.flex_combo.cget('values'))
            # A legacy request for the hidden power must not leak into the combo.
            self.assertNotEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[14])
            # Selecting any visible option still overrides the hidden one.
            app.flex_choice_var.set(gui.FLEX_POWER_LABELS[11])
            app._on_flex_choice_changed()
            app.apply_flex_power()
            self.assertEqual(gui.read_flex_request(self.view, 0), 11)
        finally:
            app.shared_view = 0
            root.destroy()

    def test_pending_selection_is_not_overwritten_by_periodic_refresh(self):
        root = tk.Tk()
        root.withdraw()
        app = gui.TrainerApp(root)
        app.pid = 1
        app.shared_view = self.view
        ctypes.c_uint64.from_address(self.view + gui.MONITOR_READY_OFFSET).value = 1
        try:
            app._refresh_combat_controls()
            self.assertEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[0])
            # User picks a power; the 250 ms refresh must keep the pending choice.
            app.flex_choice_var.set(gui.FLEX_POWER_LABELS[12])
            app._on_flex_choice_changed()
            for _ in range(3):
                app._refresh_combat_controls()
            self.assertEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[12])
            # Applying sends it, and afterwards the refresh mirrors the real request.
            app.apply_flex_power()
            self.assertEqual(gui.read_flex_request(self.view, 0), 12)
            ctypes.c_int32.from_address(gui.flex_status_address(self.view, 0)).value = 1
            app._refresh_combat_controls()
            self.assertEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[12])
            self.assertIn('已生效', app.flex_status_var.get())
            # Switching players shows the other player's actual state instead.
            app.player_choice_var.set('玩家 2')
            app._on_player_selected()
            self.assertEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[0])
        finally:
            app.shared_view = 0
            root.destroy()

    def test_ui_flex_controls_reflect_shared_state(self):
        root = tk.Tk()
        root.withdraw()
        app = gui.TrainerApp(root)
        app.pid = 1
        app.shared_view = self.view
        ctypes.c_uint64.from_address(self.view + gui.MONITOR_READY_OFFSET).value = 1
        try:
            gui.set_flex_power(self.view, 11, 0)
            ctypes.c_int32.from_address(gui.flex_status_address(self.view, 0)).value = 1
            app._refresh_combat_controls()
            self.assertEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[11])
            self.assertIn('已生效', app.flex_status_var.get())
            app.player_choice_var.set('玩家 2')
            app._on_player_selected()
            self.assertEqual(app.flex_choice_var.get(), gui.FLEX_POWER_LABELS[0])
            self.assertIn('未启用', app.flex_status_var.get())
        finally:
            app.shared_view = 0
            root.destroy()


if __name__ == '__main__':
    unittest.main()
