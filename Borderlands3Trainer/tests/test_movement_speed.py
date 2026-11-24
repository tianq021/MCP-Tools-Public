import math
import struct
import unittest
from unittest.mock import Mock

import memory_scan as m
from movement_speed import MovementSpeed, scaled_value


class FakeProcess:
    pid = 42

    def __init__(self):
        self.value = struct.pack('<f', 1.25)
        self.writes = []

    def read(self, address, size):
        return self.value

    def write(self, address, data, expected):
        if self.value != expected:
            raise m.TrainerError('changed before write')
        self.writes.append(data)
        self.value = data


def make_speed():
    speed = object.__new__(MovementSpeed)
    speed.process = FakeProcess()
    speed.active = None
    target = {'identity': (100, 12, 3), 'pawn': 200, 'controller':300, 'address':400}
    speed._target = Mock(return_value=target)
    speed._same_object = Mock(return_value=True)
    return speed


class MovementSpeedTests(unittest.TestCase):
    def test_two_to_three_uses_original_not_compounded(self):
        speed = make_speed()
        speed.set_multiplier(2)
        self.assertEqual(struct.unpack('<f',speed.process.value)[0],2.5)
        speed.set_multiplier(3)
        self.assertEqual(struct.unpack('<f',speed.process.value)[0],3.75)
        speed.set_multiplier(1)
        self.assertEqual(struct.unpack('<f',speed.process.value)[0],1.25)
        self.assertIsNone(speed.active)

    def test_invalid_or_nonfinite_values_never_written(self):
        speed = make_speed()
        for value in (0,4,-1,2.0,True,float('nan')):
            with self.assertRaises(m.TrainerError):
                speed.set_multiplier(value)
        for value in (0,float('nan'),float('inf'),-2):
            with self.assertRaises(m.TrainerError):
                scaled_value(struct.pack('<f',value),2)
        self.assertEqual(speed.process.writes,[])

    def test_character_switch_during_resolution_rejects_write(self):
        speed = make_speed()
        first = speed._target.return_value
        speed._target.side_effect = [first, dict(first, identity=(101,13,4))]
        with self.assertRaises(m.TrainerError):
            speed.set_multiplier(2)
        self.assertEqual(speed.process.writes,[])

    def test_recycled_object_is_not_restored(self):
        speed = make_speed()
        speed.set_multiplier(2)
        speed._same_object.return_value = False
        speed.set_multiplier(1)
        self.assertEqual(len(speed.process.writes),1)
        self.assertIsNone(speed.active)

    def test_game_changed_value_not_overwritten_on_restore(self):
        speed = make_speed()
        speed.set_multiplier(2)
        speed.process.value = struct.pack('<f',1.5)
        record = speed.restore()
        self.assertEqual(record['status'],'game_changed')
        self.assertEqual(struct.unpack('<f',speed.process.value)[0],1.5)

    def test_game_change_stops_monitor_without_freezing(self):
        speed = make_speed()
        speed.set_multiplier(2)
        speed.process.value = struct.pack('<f',1.5)
        self.assertIsNotNone(speed.check())
        self.assertIsNone(speed.active)
        self.assertEqual(len(speed.process.writes),1)

    def test_map_change_restores_old_live_object(self):
        speed = make_speed()
        speed.set_multiplier(3)
        speed._target.return_value = dict(speed._target.return_value,identity=(101,13,4))
        self.assertIsNotNone(speed.check())
        self.assertEqual(struct.unpack('<f',speed.process.value)[0],1.25)
        self.assertIsNone(speed.active)

    def test_restore_failure_keeps_recovery_state(self):
        speed = make_speed()
        speed.set_multiplier(2)
        speed.process.write = Mock(side_effect=m.TrainerError('write failed'))
        with self.assertRaises(m.TrainerError):
            speed.restore()
        self.assertIsNotNone(speed.active)


if __name__ == '__main__':
    unittest.main()
