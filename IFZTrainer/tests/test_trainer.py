from __future__ import annotations

import ctypes
from pathlib import Path
import re
import sys
import unittest


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import trainer_gui


class ResourceValidationTests(unittest.TestCase):
    def test_known_resource_and_integer_quantity(self) -> None:
        self.assertEqual(
            trainer_gui.validate_resource_request("木材", "250"),
            ("res_wood", 250.0),
        )

    def test_decimal_quantity(self) -> None:
        self.assertEqual(
            trainer_gui.validate_resource_request("燃料", "12.5"),
            ("res_fuel", 12.5),
        )

    def test_unknown_resource_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            trainer_gui.validate_resource_request("未知", "100")

    def test_weapon_and_mission_resource_are_available(self) -> None:
        self.assertEqual(
            trainer_gui.validate_resource_request("突击步枪", "5"),
            ("eq_assault_rifle", 5.0),
        )
        self.assertEqual(
            trainer_gui.validate_resource_request("科学家样本（任务）", "1"),
            ("res_scientists_sample", 1.0),
        )

    def test_gui_and_bridge_allowlists_match(self) -> None:
        bridge_source = (
            PROJECT / "managed" / "InfectionFreeZoneBridge.cs"
        ).read_text(encoding="utf-8")
        bridge_ids = set(re.findall(r'"((?:res|eq)_[a-z0-9_]+)"', bridge_source))
        gui_ids = {resource_id for _, resource_id in trainer_gui.RESOURCE_OPTIONS}
        self.assertEqual(gui_ids, bridge_ids)

    def test_internal_entity_ids_are_not_exposed(self) -> None:
        ids = {resource_id for _, resource_id in trainer_gui.RESOURCE_OPTIONS}
        self.assertNotIn("res_unknown", ids)
        self.assertFalse(any(resource_id.startswith("eq_inf_") for resource_id in ids))
        self.assertFalse(any(resource_id.startswith("eq_animal_") for resource_id in ids))
        self.assertFalse(any(resource_id.startswith("virt_") for resource_id in ids))

    def test_nonpositive_and_oversized_values_are_rejected(self) -> None:
        for value in ("0", "-1", "nan", "inf", "1000001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trainer_gui.validate_resource_request("罐头", value)


class SharedProtocolTests(unittest.TestCase):
    def test_status_parser(self) -> None:
        self.assertEqual(
            trainer_gui.parse_bridge_status("OK|1234|res_wood|100"),
            ("OK", 1234, ["res_wood", "100"]),
        )
        self.assertEqual(
            trainer_gui.parse_bridge_status("broken"),
            ("INVALID", None, []),
        )

    def test_resource_request_is_written_before_sequence(self) -> None:
        shared = ctypes.create_string_buffer(trainer_gui.SHARED_MEMORY_SIZE)
        address = ctypes.addressof(shared)

        trainer_gui.write_resource_request(address, "res_wood", 321.5)

        sequence = ctypes.c_uint64.from_address(
            address + trainer_gui.RESOURCE_SEQUENCE_OFFSET
        ).value
        quantity = ctypes.c_double.from_address(
            address + trainer_gui.RESOURCE_QUANTITY_OFFSET
        ).value
        raw_id = ctypes.string_at(
            address + trainer_gui.RESOURCE_ID_OFFSET,
            trainer_gui.RESOURCE_ID_CHARACTERS * 2,
        )
        resource_id = raw_id.decode("utf-16-le").split("\0", 1)[0]

        self.assertEqual(sequence, 1)
        self.assertEqual(quantity, 321.5)
        self.assertEqual(resource_id, "res_wood")

    def test_monitor_health_thresholds(self) -> None:
        self.assertEqual(trainer_gui.classify_monitor_health(True, 0), "HEALTHY")
        self.assertEqual(trainer_gui.classify_monitor_health(True, 4), "BUSY")
        self.assertEqual(trainer_gui.classify_monitor_health(True, 15), "RECOVER")
        self.assertEqual(trainer_gui.classify_monitor_health(False, 0), "RECOVER")

    def test_extra_command_protocol(self) -> None:
        shared = ctypes.create_string_buffer(trainer_gui.SHARED_MEMORY_SIZE)
        address = ctypes.addressof(shared)

        trainer_gui.write_extra_command(address, 10, 1)

        sequence = ctypes.c_uint64.from_address(
            address + trainer_gui.EXTRA_COMMAND_SEQUENCE_OFFSET
        ).value
        command_code = ctypes.c_int64.from_address(
            address + trainer_gui.EXTRA_COMMAND_CODE_OFFSET
        ).value
        command_argument = ctypes.c_int64.from_address(
            address + trainer_gui.EXTRA_COMMAND_ARGUMENT_OFFSET
        ).value
        self.assertEqual(sequence, 1)
        self.assertEqual(command_code, 10)
        self.assertEqual(command_argument, 1)
        with self.assertRaises(ValueError):
            trainer_gui.write_extra_command(address, 999)

    def test_extra_command_codes_match_bridge_switch(self) -> None:
        bridge_source = (
            PROJECT / "managed" / "InfectionFreeZoneBridge.cs"
        ).read_text(encoding="utf-8")
        bridge_codes = {
            int(value) for value in re.findall(r"case\s+(\d+)\s*:", bridge_source)
        }
        gui_codes = set(trainer_gui.EXTRA_COMMANDS) | set(trainer_gui.TOGGLE_COMMANDS)
        self.assertEqual(gui_codes, bridge_codes)

    def test_stable_toggle_commands_are_explicit(self) -> None:
        self.assertEqual(
            trainer_gui.TOGGLE_COMMANDS,
            {
                10: "快速建造",
                11: "快速研究",
                12: "免费研究",
                13: "快速搜刮",
                14: "无限弹药",
                15: "无饥饿",
                16: "完美心情",
                17: "完美天气",
            },
        )

    def test_hotkey_only_fires_on_new_press_in_foreground_game(self) -> None:
        self.assertTrue(trainer_gui.should_trigger_hotkey(True, False, 100, 100, True))
        self.assertFalse(trainer_gui.should_trigger_hotkey(True, True, 100, 100, True))
        self.assertFalse(trainer_gui.should_trigger_hotkey(True, False, 200, 100, True))
        self.assertFalse(trainer_gui.should_trigger_hotkey(True, False, 100, 100, False))
        self.assertFalse(trainer_gui.should_trigger_hotkey(False, False, 100, 100, True))


if __name__ == "__main__":
    unittest.main()
