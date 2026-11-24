"""Regressions: whole-army coverage, exact semantics, and raw admission."""
import copy
import pathlib
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
from ai_teacher.formation_teacher import FormationTeacher, FormationConfig, exact_301
from ai_teacher.tests.test_strategic_teacher import setup
from ai_teacher.tests.test_collector_teacher import obj
from dual_channel.formation_data import validate,SCHEMA
from dual_channel.formation_data import FormationWriter


class FormationBehavior(unittest.TestCase):
    def test_32_units_all_assigned_and_commanded_without_economy_actors(self):
        spec, obs, starts, soa=setup()
        obs['own_objects']=[o for o in obs['own_objects'] if o['abstract']=='ABSTRACT_TYPE_BUILDING']
        obs['own_objects'] += [obj(100+i,'Grizzly Battle Tank','ABSTRACT_TYPE_UNIT',cell=(90,125),ptr=910) for i in range(32)]
        obs['own_objects'] += [obj(500,'Chrono Miner','ABSTRACT_TYPE_UNIT',ptr=910), obj(501,'Allied Construction Vehicle','ABSTRACT_TYPE_UNIT')]
        p=FormationTeacher().plan(obs,spec,starts,soa)
        assigned=[m['addr'] for members in p['assignments'].values() for m in members]
        self.assertEqual(set(assigned),set(range(100,132)))
        self.assertEqual(len(assigned),32)
        sent={a for c in p['commands'] if c['role']!='economy' for a in c['intent'].get('object_addresses',[])}
        self.assertEqual(sent,set(range(100,132)))
        self.assertGreater(len(p['outside_legacy_slots']),0)
        group=next(c for c in p['commands'] if c['role']=='front')
        self.assertEqual(group['mapping_301']['classification'],'UNSUPPORTED_FOR_301')
        self.assertIsNone(group['mapping_301']['action_idx'])

    def test_newly_produced_member_receives_group_order(self):
        spec, obs, starts, soa=setup();teacher=FormationTeacher()
        first=teacher.plan(obs,spec,starts,soa)
        next_obs=copy.deepcopy(obs);next_obs.update(t=63,frame=3780)
        next_obs['own_objects'].append(obj(999,'Grizzly Battle Tank','ABSTRACT_TYPE_UNIT',cell=(90,125),ptr=910))
        second=teacher.plan(next_obs,spec,starts,soa)
        self.assertTrue(any(999 in c['intent'].get('object_addresses',[]) for c in second['commands']))

    def test_identical_orders_do_not_repeat_every_half_second(self):
        spec, obs, starts, soa=setup();teacher=FormationTeacher()
        teacher.plan(obs,spec,starts,soa)
        p=teacher.plan(dict(obs,t=60.5,frame=3630),spec,starts,soa)
        self.assertFalse(any(c['role']!='economy' for c in p['commands']))

    def test_hidden_enemy_does_not_affect_plan(self):
        spec, obs, starts, soa=setup();a=FormationTeacher().plan(obs,spec,starts,soa)
        spec2, _, _, _=setup();dirty=copy.deepcopy(obs)
        dirty['hidden_enemy_count']=999
        dirty['visible_enemies']=[obj(900,'Enemy','ABSTRACT_TYPE_UNIT',cell=(91,122))|{'cell_shrouded':True}]
        b=FormationTeacher().plan(dirty,spec2,starts,soa)
        self.assertEqual(a,b)

    def test_different_formation_roles_never_share_actors(self):
        spec,obs,starts,soa=setup()
        obs['own_objects'].append(obj(600,'Prism Tank','ABSTRACT_TYPE_UNIT',cell=(89,124)))
        p=FormationTeacher().plan(obs,spec,starts,soa)
        ids=[a for c in p['commands'] if c['role']!='economy' for a in c['intent'].get('object_addresses',[])]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(any(c['role']=='fire' for c in p['commands']))

    def test_exact_macro_annotation_and_far_move_refusal(self):
        spec,obs,starts,soa=setup()
        slot=next(i for i,t in enumerate(spec.types) if 'Ore Refinery' in t['name'])
        intent=spec.intent(2+slot,obs,starts,soa)
        self.assertEqual(exact_301(intent,obs,spec,starts,soa)['action_idx'],2+slot)
        actor=spec.units(obs)[0]
        move={'action':'MOVE','object_addresses':[actor['addr']],'coordinates':[170*256+128,10*256+128]}
        self.assertEqual(exact_301(move,obs,spec,starts,soa)['classification'],'UNSUPPORTED_FOR_301')

    def test_address_reuse_after_gap_gets_new_track(self):
        spec,obs,starts,soa=setup();teacher=FormationTeacher()
        teacher.plan(obs,spec,starts,soa);old=teacher.tracks[10]['track_id']
        fewer=copy.deepcopy(obs);fewer['own_objects']=[o for o in fewer['own_objects'] if o['addr']!=10]
        teacher.plan(dict(fewer,t=61,frame=3660),spec,starts,soa)
        teacher.plan(dict(obs,t=62,frame=3720),spec,starts,soa)
        self.assertNotEqual(old,teacher.tracks[10]['track_id'])

    def test_invalid_parameters(self):
        for kwargs in ({'min_army':1},{'launch_after_s':float('nan')},{'command_refresh_s':.5}):
            with self.assertRaises(ValueError):FormationConfig(**kwargs)


class RawAdmission(unittest.TestCase):
    def test_rpc_transport_ok_does_not_count_native_rejection_as_accepted_control(self):
        spec,obs,starts,soa=setup();plan=FormationTeacher().plan(obs,spec,starts,soa)
        command=next(c for c in plan['commands'] if c['role']!='economy')
        plan['commands']=[command];plan['decision_301_eligible']=False
        record={'schema':SCHEMA,'source':'formation_teacher','teacher_sha256':'test','decision_id':'d0',
            'observation':obs,'observation_frame':obs['frame'],'next_frame':obs['frame']+1,'plan':plan,
            'decision_301_eligible':False,'actor_eligible':False,'effect_label_eligible':False,'formal_training_eligible':False,
            'native_commands':[{'action_id':'a0','command_type':'UNIT_ORDER',
                'actor_unit_ids':command['intent']['object_addresses'],'transport_result':{'ok':True}}],
            'command_outcomes':[{'action_id':'a0','classification':'STALE_REJECTED'}]}
        with tempfile.TemporaryDirectory(dir=ROOT/'_work') as temp:
            pathlib.Path(temp).resolve().relative_to(ROOT)
            writer=FormationWriter(pathlib.Path(temp)/'capture',{})
            writer.append(record);summary=writer.finish(obs,'interrupted',failure='synthetic test')
            self.assertEqual(summary['distinct_sent_unit_actors'],0)
            self.assertEqual(summary['native_command_outcomes']['STALE_REJECTED'],1)

    def test_single_unsupported_command_cannot_receive_301_label(self):
        spec,obs,starts,soa=setup();plan=FormationTeacher().plan(obs,spec,starts,soa)
        command=next(c for c in plan['commands'] if c['role']!='economy')
        plan['commands']=[command]
        record={'schema':SCHEMA,'source':'formation_teacher','teacher_sha256':'test','decision_id':'d0',
            'observation':obs,'observation_frame':obs['frame'],'next_frame':obs['frame']+1,'plan':plan,
            'decision_301_eligible':True,'actor_eligible':False,'effect_label_eligible':False,'formal_training_eligible':False}
        self.assertIn('unsupported_command_as_single_301',validate(record))

    def test_dead_objects_do_not_get_classified_as_transport_success(self):
        from dual_channel.collect_formation import rejection_class
        self.assertEqual(rejection_class('native_error: object not found'),'STALE_REJECTED')
        self.assertEqual(rejection_class('native_error: target object 42 no longer exists'),'STALE_REJECTED')
        self.assertEqual(rejection_class('RPC timeout'),'TRANSPORT_FAILURE')
        self.assertEqual(rejection_class('native_error: unbuildable'),'OTHER_REJECTED')
        self.assertEqual(rejection_class(None),'ACCEPTED')

    def test_multi_command_cannot_be_single_301_and_actor_reuse_rejected(self):
        spec,obs,starts,soa=setup();p=FormationTeacher().plan(obs,spec,starts,soa)
        record={'schema':SCHEMA,'source':'formation_teacher','teacher_sha256':'test','decision_id':'d0',
                'observation':obs,'observation_frame':obs['frame'],'next_frame':obs['frame']+1,
                'plan':p,'decision_301_eligible':False,'actor_eligible':False,'effect_label_eligible':False,'formal_training_eligible':False}
        self.assertEqual(validate(record),[])
        record['decision_301_eligible']=True
        self.assertIn('multi_command_as_single_301',validate(record))
        record['decision_301_eligible']=False
        combat=next(c for c in p['commands'] if c['role']!='economy')
        p['commands'].append(copy.deepcopy(combat))
        self.assertIn('actor_conflict_or_not_own',validate(record))


if __name__=='__main__':unittest.main()
