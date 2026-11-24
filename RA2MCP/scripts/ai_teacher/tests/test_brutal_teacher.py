import copy,pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'scripts'))
from ai_teacher.tests.test_strategic_teacher import setup
from ai_teacher.tests.test_collector_teacher import obj
from ai_teacher.brutal_teacher import BrutalTeacher,match

class BrutalTests(unittest.TestCase):
    def test_no_early_starter_suicide(self):
        spec,row,starts,soa=setup();plan=BrutalTeacher().plan(row,spec,starts,soa)
        self.assertEqual(plan['phase'],'BUILDUP')
    def test_new_units_all_commandable_economic_actors_excluded(self):
        spec,row,starts,soa=setup();row['own_objects']+= [obj(200+i,'Grizzly Battle Tank','ABSTRACT_TYPE_UNIT',ptr=910) for i in range(32)]
        row['own_objects'].append(obj(999,'Chrono Miner','ABSTRACT_TYPE_UNIT'))
        plan=BrutalTeacher().plan(row,spec,starts,soa)
        actors=[x for c in plan['commands'] if c['intent']['action'] in ('MOVE','ATTACK') for x in c['intent']['object_addresses']]
        self.assertEqual(len(actors),40);self.assertEqual(len(set(actors)),40);self.assertNotIn(999,actors)
    def test_hidden_enemy_invariance(self):
        spec,row,starts,soa=setup();other=copy.deepcopy(row)
        e=obj(1000,'Rhino Heavy Tank','ABSTRACT_TYPE_UNIT');e.update(owner='Russians',owner_ptr=2,cell_shrouded=True)
        other['visible_enemies']=[e]
        self.assertEqual(BrutalTeacher().plan(row,spec,starts,soa),BrutalTeacher().plan(other,spec,starts,soa))
    def test_gi_does_not_match_engineer(self):
        self.assertTrue(match('GI',('GI',)));self.assertFalse(match('Engineer',('GI',)));self.assertFalse(match('Guardian GI',('GI',)))
    def test_individual_prism_with_newborn_at_base_has_valid_goal(self):
        spec,row,starts,soa=setup();row['own_objects'] += [obj(300,'Prism Tank','ABSTRACT_TYPE_UNIT',cell=(92,124)),obj(301,'Prism Tank','ABSTRACT_TYPE_UNIT',cell=(125,80))]
        plan=BrutalTeacher().plan(row,spec,starts,soa)
        fire=[c for c in plan['commands'] if c['role'].startswith('fire-')]
        self.assertEqual(len(fire),2);self.assertTrue(all(len(c['intent']['object_addresses'])==1 for c in fire))
    def test_visible_nearby_threat_attacked(self):
        spec,row,starts,soa=setup();e=obj(1000,'Rhino Heavy Tank','ABSTRACT_TYPE_UNIT',cell=(98,125));e.update(owner='Russians',owner_ptr=2)
        row['visible_enemies']=[e];p=BrutalTeacher().plan(row,spec,starts,soa)
        self.assertTrue(any(c['intent'].get('target_address')==1000 for c in p['commands']))
    def test_repeated_orders_debounced(self):
        spec,row,starts,soa=setup();t=BrutalTeacher();t.plan(row,spec,starts,soa)
        row['t']+=.5;row['frame']+=30;p=t.plan(row,spec,starts,soa)
        self.assertFalse(any(c['role'] in ('front','support') for c in p['commands']))

if __name__=='__main__':unittest.main()
