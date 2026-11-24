import asyncio
import copy
import itertools
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
import torch
from ai_teacher.tests.test_strategic_teacher import setup
from ai_teacher.tests.test_collector_teacher import obj
from structured_control.contract import (Vocabulary,SnapshotBuilder,ROLES,VERSION,OBS_VERSION,
    digest,mask,factors,intent_of,revalidate,validate_action)
from structured_control.policy import StructuredPolicy,load_checkpoint
from structured_control.serial_teacher import SerialTeacher
from structured_control.data import validate,SCHEMA


def fixture():
    spec,obs,starts,soa=setup()
    obs['own_objects'] += [obj(4,'Allied Barracks','ABSTRACT_TYPE_BUILDING',ptr=903),
        obj(5,'Allied War Factory','ABSTRACT_TYPE_BUILDING',ptr=904),
        obj(6,'Allied Construction Vehicle','ABSTRACT_TYPE_UNIT',ptr=912),
        obj(7,'Allied Ore Refinery','ABSTRACT_TYPE_BUILDING',ptr=902,on_map=False,in_limbo=True)]
    obs['factories']=[{'object_ptr':7,'completed':True}]
    enemy=obj(1000,'Grizzly Battle Tank','ABSTRACT_TYPE_UNIT',cell=(110,120),ptr=910)
    enemy.update(owner='Russians',owner_ptr=2)
    obs['visible_enemies']=[enemy]
    vocab=Vocabulary.from_catalog(spec.all_types.values())
    return spec,obs,starts,soa,vocab


def snapshot():
    spec,obs,_,_,v=fixture()
    return SnapshotBuilder(v,'test').build(obs,spec),v


class ObservationContract(unittest.TestCase):
    def test_32_units_partition_without_workers(self):
        spec,obs,_,_,v=fixture()
        obs['own_objects']=[o for o in obs['own_objects'] if o['addr']<10]+[
            obj(200+i,'Grizzly Battle Tank','ABSTRACT_TYPE_UNIT',ptr=910) for i in range(32)]
        obs['own_objects'].append(obj(9000,'Chrono Miner','ABSTRACT_TYPE_UNIT'))
        s=SnapshotBuilder(v,'ep').build(obs,spec)
        actual=[m['addr'] for g in s['groups'] for m in g]
        self.assertEqual(len(actual),32);self.assertEqual(len(set(actual)),32)
        self.assertNotIn(9000,actual);self.assertNotIn(6,actual)

    def test_hidden_information_has_no_effect(self):
        spec,obs,_,_,v=fixture();changed=copy.deepcopy(obs)
        changed['truth']={'money':999999};changed['houses']=[{'money':999999,'owner':'enemy'}]
        hidden=copy.deepcopy(changed['visible_enemies'][0]);hidden.update(addr=8000,cell_shrouded=True)
        changed['visible_enemies'].append(hidden)
        self.assertEqual(SnapshotBuilder(v,'ep').build(obs,spec),SnapshotBuilder(v,'ep').build(changed,spec))

    def test_epoch_changes_after_disappearance_owner_change_and_reuse(self):
        spec,obs,_,_,v=fixture();b=SnapshotBuilder(v,'ep');first=b.build(obs,spec)
        original=next(e['track_id'] for e in first['entities'] if e['addr']==1000)
        gone=copy.deepcopy(obs);gone['frame']+=30;gone['visible_enemies']=[];b.build(gone,spec)
        back=copy.deepcopy(obs);back['frame']+=60
        second=b.build(back,spec);current=next(e['track_id'] for e in second['entities'] if e['addr']==1000)
        self.assertNotEqual(original,current)
        back['frame']+=30;back['visible_enemies'][0]['owner_ptr']=3
        third=b.build(back,spec);self.assertNotEqual(current,next(e['track_id'] for e in third['entities'] if e['addr']==1000))

    def test_stale_group_and_target_are_rejected(self):
        spec,obs,_,_,v=fixture();b=SnapshotBuilder(v,'ep');a=b.build(obs,spec)
        group=next(i for i,g in enumerate(a['groups']) if g)
        move={'family':5,'group':group,'x':100,'y':100}
        obs['frame']+=30;obs['own_objects']=[o for o in obs['own_objects'] if o['addr']!=10]
        newer=b.build(obs,spec)
        with self.assertRaises(ValueError):revalidate(a,move,newer)
        attack={'family':6,'group':group,'target':next(i for i,e in enumerate(newer['entities']) if e['targetable'])}
        obs['frame']+=30;obs['visible_enemies']=[];last=b.build(obs,spec)
        with self.assertRaises(ValueError):revalidate(newer,attack,last)

    def test_overflow_and_future_capacity_fail_closed(self):
        spec,obs,_,_,v=fixture()
        obs['own_objects']=[obj(2000+i,'GI','ABSTRACT_TYPE_INFANTRY') for i in range(257)]
        with self.assertRaises(ValueError):SnapshotBuilder(v,'ep').build(obs,spec)
        obs['map']['width']=257
        with self.assertRaises(ValueError):SnapshotBuilder(v,'ep').build(obs,spec)

    def test_pending_overflow_not_silently_truncated(self):
        spec,obs,_,_,v=fixture()
        for i in range(5):
            obs['own_objects'].append(obj(300+i,'Allied Ore Refinery','ABSTRACT_TYPE_BUILDING',ptr=902,on_map=False,in_limbo=True))
            obs['factories'].append({'object_ptr':300+i,'completed':True})
        with self.assertRaises(ValueError):SnapshotBuilder(v,'ep').build(obs,spec)

    def test_duplicate_frames_and_inactive_factors_rejected(self):
        spec,obs,_,_,v=fixture();b=SnapshotBuilder(v,'ep');s=b.build(obs,spec)
        with self.assertRaises(ValueError):b.build(obs,spec)
        with self.assertRaises(ValueError):validate_action(s,{'family':0,'target':0})

    def test_type_manifest_stable_across_python_hash_seeds(self):
        code="import sys;sys.path.insert(0,'scripts');from structured_control.contract import Vocabulary;print(Vocabulary(['z','a','a']).sha256)"
        hashes=[]
        for seed in ('2','876'):
            r=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,
                env={**os.environ,'PYTHONHASHSEED':seed},timeout=20)
            self.assertEqual(r.returncode,0,r.stderr);hashes.append(r.stdout.strip())
        self.assertEqual(hashes[0],hashes[1])
        self.assertEqual(Vocabulary(['a']).encode({'abstract':'ABSTRACT_TYPE_UNIT','type_name':'new'}),0)

    def test_no_enemy_or_army_does_not_all_mask_noop(self):
        spec,obs,_,_,v=fixture();obs.update(own_objects=[],visible_enemies=[],factories=[])
        s=SnapshotBuilder(v,'ep').build(obs,spec)
        self.assertEqual(s['family_mask'],[True,False,False,False,False,False,False])


class ConditionalPolicy(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(20261102);torch.set_num_threads(2)
        self.s,self.v=snapshot();self.model=StructuredPolicy(self.v)

    def test_sample_and_teacher_forced_log_prob_identical_for_all_families(self):
        s=self.s
        for f in range(7):
            single=copy.deepcopy(s);single['family_mask']=[i==f for i in range(7)]
            result=self.model([single]);action=result['actions'][0]
            replay=self.model([single],actions=[action])
            self.assertTrue(torch.equal(result['log_prob'],replay['log_prob']))
            self.assertEqual(set(result['factor_log_probs'][0]),{'family',*factors(f)})
            self.assertEqual(intent_of(single,action)['action'] if f else 'NOOP',('NOOP','DEPLOY','PRODUCE','PLACE','STOP','MOVE','ATTACK')[f])
            self.assertTrue(torch.equal(result['log_prob'][0],torch.stack(list(result['factor_log_probs'][0].values())).sum()))

    def test_joint_probability_normalizes_by_enumerating_all_tiny_actions(self):
        s=copy.deepcopy(self.s);s['map_width']=2;s['map_height']=2
        s['groups']=[s['groups'][0]]+[[] for _ in range(7)]
        for p in s['production']:p['legal']=p['slot']<2
        actions=[]
        for f in range(7):
            dims=factors(f);sizes=[(i for i,v in enumerate(mask(s,d,{'family':f})) if v) for d in dims]
            for values in itertools.product(*sizes):actions.append({'family':f,**dict(zip(dims,values))})
        result=self.model([s]*len(actions),actions=actions)
        self.assertAlmostEqual(float(result['log_prob'].exp().sum()),1,places=5)

    def test_inactive_head_does_not_affect_attack_likelihood(self):
        target=next(i for i,e in enumerate(self.s['entities']) if e['targetable'])
        a={'family':6,'group':0,'target':target};old=self.model([self.s],actions=[a])['log_prob']
        with torch.no_grad():self.model.x_head.weight.add_(100)
        new=self.model([self.s],actions=[a])['log_prob'];self.assertTrue(torch.equal(old,new))

    def test_y_distribution_depends_on_selected_x(self):
        a={'family':5,'group':0,'x':10,'y':20};b={**a,'x':11}
        pa=self.model([self.s],actions=[a])['factor_log_probs'][0]['y']
        pb=self.model([self.s],actions=[b])['factor_log_probs'][0]['y']
        self.assertNotEqual(float(pa),float(pb))

    def test_recurrent_state_replays_and_gradients_cross_sequence(self):
        first=self.model([self.s]);h=first['h_next'];h.retain_grad()
        second=self.model([self.s],h0=h,actions=[{'family':0}]);loss=-second['log_prob'].mean()+second['value'].square().mean()
        loss.backward();self.assertIsNotNone(h.grad);self.assertTrue(torch.isfinite(h.grad).all())
        replay=self.model([self.s],h0=first['h_next'].detach(),actions=second['actions'])
        self.assertTrue(torch.equal(second['log_prob'],replay['log_prob']))
        reset=self.model([self.s],actions=first['actions']);self.assertTrue(torch.equal(first['log_prob'],reset['log_prob']))

    def test_checkpoint_strict_namespace_and_vocabulary(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'_work') as d:
            path=pathlib.Path(d)/'initial.pt';torch.save(self.model.checkpoint(self.v),path)
            loaded=load_checkpoint(path,self.v)
            a=[{'family':0}]
            self.assertTrue(torch.equal(self.model([self.s],actions=a)['log_prob'],loaded([self.s],actions=a)['log_prob']))
            with self.assertRaises(ValueError):load_checkpoint(path,Vocabulary(['different']))

    def test_unknown_schema_and_illegal_masked_action_refused(self):
        s=copy.deepcopy(self.s);s['action_contract']='full_ground_v2p1'
        with self.assertRaises(ValueError):self.model([s])
        with self.assertRaises(ValueError):self.model([self.s],actions=[{'family':6,'group':0,'target':256}])


class SerialPipeline(unittest.TestCase):
    def test_existing_three_second_production_guard_replays_from_actual_ack(self):
        from structured_control.audit_serial import advance_production_guard
        spec,obs,_,_,v=fixture()
        slot=spec.slot_by_ptr[911]
        self.assertTrue(spec.can_produce(slot,obs))
        record={'intent':{'action':'PRODUCE','production_type_ptr':911},'observation':obs,
                'execution':{'ack_records':[{'type':'action','error':None}]}}
        advance_production_guard(spec,record)
        self.assertFalse(spec.can_produce(slot,obs))
        obs['t']+=3.1
        self.assertTrue(spec.can_produce(slot,obs))

    def test_episode_partitions_cannot_leak_rows_across_splits(self):
        from structured_control.dataset import check_partitions
        self.assertTrue(check_partitions({'train':['ep1'],'val':['ep2'],'test':['ep3']}))
        with self.assertRaises(ValueError):check_partitions({'train':['ep1'],'val':['ep1']})

    def test_unselected_roles_not_marked_as_sent(self):
        spec,obs,starts,soa,v=fixture();s=SnapshotBuilder(v,'ep').build(obs,spec)
        t=SerialTeacher();choice=t.choose(obs,spec,starts,soa,s)
        self.assertIsNotNone(choice['intent'])
        for role in t.planner.issued:self.assertEqual(role,choice['role'])
        self.assertEqual(choice['intent'],intent_of(s,choice['action']))

    def test_execute_one_observes_after_exactly_one_command(self):
        from structured_control.collect_serial import execute_one
        from dsh_m1 import command_capture
        ext=types.SimpleNamespace(records=[])
        class Env:
            _dec_acks=[]
            frame=1
            async def _commands(self,intents,source):
                self.count=len(intents);self._dec_acks=[{'type':'action','action':'MOVE','error':None}]
                ext.records.append({'command_id':'native-1'});return 0
            async def _capture(self):self.frame=2
        s,v=snapshot();action={'family':5,'group':0,'x':100,'y':100}
        choice={'intent':intent_of(s,action),'role':'front','action':action}
        env=Env()
        with patch.object(command_capture,'extractor',return_value=ext),patch.object(command_capture,'set_context',return_value=True),patch.object(command_capture,'status',return_value={'errors':[]}):
            result=asyncio.run(execute_one(env,s,choice,'d1'))
        self.assertEqual(env.count,1);self.assertEqual(env.frame,2)
        self.assertEqual(len(result['native_commands']),1);self.assertEqual(result['classification'],'ACCEPTED')

    def test_serial_validator_rejects_batch_and_fake_reward(self):
        s,v=snapshot();r={'schema':SCHEMA,'snapshot':s,'action':{'family':0},'intent':None,
            'observation':{'frame':s['frame']},'next_observation':{'frame':s['frame']+30},'decision_id':'d',
            'execution':{'native_commands':[],'classification':'NOOP'},'reward':None,
            'actor_eligible':False,'formal_training_eligible':False,'effect_eligible':False,'ppo_eligible':False}
        self.assertEqual(validate(r),[])
        r['execution']['native_commands']=[{},{}];r['reward']=0
        self.assertTrue(validate(r))


if __name__=='__main__':unittest.main()
