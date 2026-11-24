import struct
import unittest
from unittest.mock import Mock

import memory_scan as m
from loot_weights import LootWeights, RARITIES, parse_scale


class FakeProcess:
    pid=12
    def __init__(self):
        self.values={i:struct.pack('<f',1+i/10) for i in range(5)}
        self.writes=[]
        self.fail_once=None
    def write(self,address,data,expected):
        if address==self.fail_once:
            self.fail_once=None
            raise m.TrainerError('simulated write failure')
        if self.values[address]!=expected:
            raise m.TrainerError('prewrite conflict')
        self.values[address]=data
        self.writes.append(address)
    def close(self):
        pass


def make_loot():
    obj=object.__new__(LootWeights)
    obj.process=FakeProcess()
    obj.pool=100
    obj.pool_id=(100,1,7)
    obj.active={}
    obj.ref=Mock()
    obj.ref.identity.return_value=obj.pool_id
    obj._entries=lambda discover=True:{key:{'address':i,'value':obj.process.values[i],
        'guard':(obj.pool_id,i)} for i,key in enumerate(RARITIES)}
    return obj


class LootWeightsTests(unittest.TestCase):
    def test_valid_scales_include_zero_and_decimals(self):
        for inp,out in [('0',0),('10.5',10.5),('1000',1000),(' 2 ',2)]:
            self.assertEqual(parse_scale(inp),out)

    def test_bad_inputs_rejected(self):
        for value in ('nan','inf','-inf',-1,1001,True,'10%',None,'abc'):
            with self.assertRaises(m.TrainerError):parse_scale(value)

    def test_read_is_not_a_probability_and_does_not_write(self):
        obj=make_loot()
        result=obj.read_current()
        self.assertIsNone(result['probability_percent'])
        self.assertAlmostEqual(result['scales']['Legendary'],1.4,places=6)
        self.assertEqual(obj.process.writes,[])

    def test_only_purple_orange_changed_and_original_restored(self):
        obj=make_loot()
        before=dict(obj.process.values)
        result=obj.set_scales(5,10)
        self.assertEqual(result['scales']['VeryRare'],5)
        self.assertEqual(result['scales']['Legendary'],10)
        self.assertEqual(set(obj.process.writes),{3,4})
        obj.set_scales(2,3)
        self.assertEqual(obj.read_current()['scales']['Legendary'],3)
        obj.restore()
        self.assertEqual(obj.process.values,before)
        self.assertFalse(obj.active)

    def test_second_write_failure_rolls_back_first(self):
        obj=make_loot()
        before=dict(obj.process.values)
        obj.process.fail_once=4
        with self.assertRaises(m.TrainerError):obj.set_scales(5,10)
        self.assertEqual(obj.process.values,before)
        self.assertFalse(obj.active)

    def test_failed_reapply_preserves_previous_recovery(self):
        obj=make_loot()
        original=dict(obj.process.values)
        obj.set_scales(5,10)
        applied=dict(obj.process.values)
        obj.process.fail_once=4
        with self.assertRaises(m.TrainerError):obj.set_scales(20,30)
        self.assertEqual(obj.process.values,applied)
        obj.restore()
        self.assertEqual(obj.process.values,original)

    def test_external_change_preserved_other_field_restored(self):
        obj=make_loot()
        original=dict(obj.process.values)
        obj.set_scales(5,10)
        obj.process.values[4]=struct.pack('<f',15)
        result=obj.restore()
        self.assertEqual(result['status'],'external_change')
        self.assertEqual(obj.process.values[4],struct.pack('<f',15))
        self.assertEqual(obj.process.values[3],original[3])

    def test_recycled_pool_never_written(self):
        obj=make_loot()
        obj.set_scales(5,10)
        count=len(obj.process.writes)
        obj.ref.identity.return_value=(100,1,8)
        obj.restore()
        self.assertEqual(len(obj.process.writes),count)
        self.assertFalse(obj.active)
        self.assertEqual(obj.pool,0)

    def test_restore_failure_retains_pending_field(self):
        obj=make_loot()
        original=dict(obj.process.values)
        obj.set_scales(5,10)
        obj.process.fail_once=4
        with self.assertRaises(m.TrainerError):obj.restore()
        self.assertEqual(set(obj.active),{'Legendary'})
        obj.restore()
        self.assertEqual(obj.process.values,original)


if __name__=='__main__':unittest.main()
