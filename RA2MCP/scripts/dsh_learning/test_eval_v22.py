"""Offline regression tests. All games/models here are inert temporary fixtures."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.append('E:/RA2MCP/scripts')
import eval_v22_support as support
import eval_v22_metrics as metrics
import eval_v22_parallel as runner
import eval_v22_aggregate as aggregate
from dsh_episode.schema import make_meta


INI=(b'[Settings]\r\nUIMapName=2peaks\r\nAIDifficulty=2\r\nCredits=20000\r\nUnitCount=8\r\n'
     b'Superweapons=No\r\nGameSpeed=1\r\nScenario=spawnmap.ini\r\nPlayerCount=2\r\nAIPlayers=1\r\n'
     b'Seed=7 ; preserve comment\r\n[HouseHandicaps]\r\nMulti2=2\r\n[Other]\r\nSeed=999\r\n')


def project(root,count=2):
    for slot in range(count):
        folder=support.game_dir(root,slot);folder.mkdir(parents=True)
        (folder/'spawn.ini').write_bytes(INI)
        (folder/'spawnmap.ini').write_bytes(b'inert-map')
        (folder/'gamemd-spawn-ra2yrcpp.exe').write_bytes(b'inert-never-executed')
        (folder/'libra2yrcpp.dll').write_bytes(b'inert-library')
    model=root/'model.zip';model.write_bytes(b'inert-model')
    return model


def frozen(expected='model'):
    return {'model_sha256_before':expected,'model_sha256_after':expected,
            'optimizer_updates_before':96,'optimizer_updates_after':96,
            'policy_sha256_before':'weights','policy_sha256_after':'weights'}


def row(case):
    return {**case,'kind':'defeat','is_win':False,'outcome_confirmed':True,'elapsed_s':100.,
            'attempt':0,'damage_dealt':20.,'own_hp_lost':40.,'damage_exchange':.5}


def result(case,attempt=0):
    value=row(case);value['attempt']=attempt
    return {'case_id':case['case_id'],'ok':True,'row':value,'frozen':frozen(),'recorded_engine_seed':case['engine_seed']}


def matrix():
    plan=support.cases(2,2,20000);protocol={'episode_seconds':300.,'policy_mode':'deterministic'}
    results=[result(c) for c in plan]
    return {'schema':support.SCHEMA,'model_key':'r1','model_sha256':'model','protocol':protocol,
            'protocol_hash':support.digest(protocol),'case_plan':plan,'case_plan_hash':support.digest(plan),
            'evaluation_complete':True,'frozen_verified':True,'training':False,'used_for_training':False,
            'attempts':[{**c,'attempt':0,'result':r} for c,r in zip(plan,results)],
            'modes':{'deterministic':{'mode':'deterministic','deterministic':True,'episodes':[r['row'] for r in results]}}}


class ConfigTests(unittest.TestCase):
    def test_seed_changes_only_settings_preserves_bytes(self):
        value=support.seed_bytes(INI,12345)
        self.assertEqual(value,INI.replace(b'Seed=7 ;',b'Seed=12345 ;'))
        self.assertEqual(support.seed_value(value),12345)

    def test_missing_duplicate_and_out_of_range_seed_rejected(self):
        for data in (INI.replace(b'Seed=7 ; preserve comment\r\n',b''),INI.replace(b'Seed=7',b'Seed=1\r\nSeed=7')):
            with self.assertRaises(support.EvaluationError):support.seed_bytes(data,1)
        for value in (-1,2**32,True):
            with self.assertRaises(support.EvaluationError):support.seed_bytes(INI,value)

    def test_seed_is_visible_to_attempt_then_original_restored_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'spawn.ini';path.write_bytes(INI)
            with self.assertRaisesRegex(RuntimeError,'fixture failure'):
                with support.SeedGuard(path,20000,Path(tmp)/'evidence'):
                    self.assertEqual(support.seed_value(path.read_bytes()),20000)
                    raise RuntimeError('fixture failure')
            self.assertEqual(path.read_bytes(),INI)

    def test_concurrent_config_edit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'spawn.ini';path.write_bytes(INI)
            with self.assertRaisesRegex(support.EvaluationError,'Concurrent'):
                with support.SeedGuard(path,2,Path(tmp)/'evidence'):path.write_bytes(b'user-edit')
            self.assertEqual(path.read_bytes(),b'user-edit')

    def test_plan_is_paired_unique_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);model=project(root)
            value=support.make_plan(root,{'a':model,'b':model},5,2,20000)
            self.assertEqual([c['engine_seed'] for c in value['case_plan']],list(range(20000,20005)))
            self.assertEqual([c['slot'] for c in value['case_plan']],[0,1,0,1,0])
            self.assertEqual((root/'test-game/spawn.ini').read_bytes(),INI)

    def test_different_game_maps_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project(root);(root/'test-game-1/spawnmap.ini').write_bytes(b'other')
            with self.assertRaisesRegex(support.EvaluationError,'different maps'):support.preflight(root,2)


class SchedulerTests(unittest.TestCase):
    def test_failed_case_keeps_seed_slot_and_id_on_retry(self):
        plan=support.cases(4,2,50);calls=[]
        def execute(group,attempt):
            calls.extend((copy.deepcopy(c),attempt) for c in group)
            return [result(c,attempt) for c in group if not(c['case_id']=='case-0001' and attempt==0)]
        accepted,attempts,pending=support.retry_cases(plan,execute,lambda:None,lambda:None,300.,1)
        self.assertFalse(pending);self.assertEqual(len(accepted),4)
        retried=[(c,a) for c,a in calls if c['case_id']=='case-0001']
        self.assertEqual(retried,[(plan[1],0),(plan[1],1)])

    def test_cleanup_failure_prevents_launch(self):
        calls=[]
        def cleanup():raise support.CleanupError('busy')
        with self.assertRaises(support.CleanupError):
            support.retry_cases(support.cases(1,1,1),lambda *a:calls.append(a),cleanup,lambda:None,300.,2)
        self.assertEqual(calls,[])

    def test_false_cleanup_return_prevents_launch(self):
        calls=[]
        with self.assertRaises(support.CleanupError):
            support.retry_cases(support.cases(1,1,1),lambda *a:calls.append(a),lambda:False,lambda:None,300.,1)
        self.assertEqual(calls,[])

    def test_cleanup_failure_prevents_next_wave(self):
        calls=[];n=[0]
        def cleanup():
            n[0]+=1
            if n[0]==2:raise support.CleanupError('busy after wave')
        def execute(group,attempt):calls.append(group);return [result(c) for c in group]
        with self.assertRaises(support.CleanupError):support.retry_cases(support.cases(3,1,1),execute,cleanup,lambda:None,300.,1)
        self.assertEqual(len(calls),1)

    def test_missing_worker_is_bounded_and_not_a_valid_episode(self):
        accepted,attempts,pending=support.retry_cases(support.cases(1,1,1),lambda *a:[],lambda:None,lambda:None,300.,1)
        self.assertFalse(accepted);self.assertEqual(len(attempts),2);self.assertEqual(len(pending),1)

    def test_duplicate_or_wrong_seed_result_rejected(self):
        case=support.cases(1,1,1)[0]
        with self.assertRaises(support.EvaluationError):
            support.retry_cases([case],lambda *a:[result(case),result(case)],lambda:None,lambda:None,300.,0)
        bad=result(case);bad['row']['engine_seed']=99
        with self.assertRaises(support.EvaluationError):
            support.retry_cases([case],lambda *a:[bad],lambda:None,lambda:None,300.,0)

    def test_model_file_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'model';p.write_bytes(b'a');expected=support.sha256(p);p.write_bytes(b'b')
            with self.assertRaises(support.FrozenViolation):support.frozen_file(p,expected)

    def test_loaded_update_counter_may_be_nonzero_but_must_not_change(self):
        self.assertTrue(support.validate_frozen(frozen(),'model'))
        value=frozen();value['optimizer_updates_after']+=1
        with self.assertRaises(support.FrozenViolation):support.validate_frozen(value,'model')
        value=frozen();value['policy_sha256_after']='changed'
        with self.assertRaises(support.FrozenViolation):support.validate_frozen(value,'model')


class ProcessTests(unittest.TestCase):
    def test_idle_port_does_not_allow_adopting_old_game_process(self):
        base=Path(tempfile.gettempdir())/'fixture-project/test-game'
        running={'pid':99,'parent':1,'path':str(base/'gamemd-spawn-ra2yrcpp.exe'),'created':'old'}
        with self.assertRaises(support.CleanupError):support.assert_copies_idle([running],[base])
        outside={**running,'path':str(Path(tempfile.gettempdir())/'original-game/gamemd.exe')}
        support.assert_copies_idle([outside],[base])

    def test_only_owned_game_path_and_creation_identity_can_be_stopped(self):
        base=Path(tempfile.gettempdir())/'fixture-project/test-game'
        own={'pid':11,'parent':10,'path':str(base/'gamemd-spawn-ra2yrcpp.exe'),'created':'a'}
        other={'pid':12,'parent':10,'path':str(Path(tempfile.gettempdir())/'original-game/gamemd.exe'),'created':'a'}
        snapshot=[own,other,{'pid':10,'parent':1,'path':'worker.exe','created':'a'}]
        self.assertEqual(support.owned_games(snapshot,[10],[base]),[own])
        stopped=[];support.stop_owned([own],[base],snapshot=lambda:snapshot,stop=lambda values:stopped.extend(values))
        self.assertEqual(stopped,[own])
        with self.assertRaises(support.CleanupError):support.verified_owned([other],snapshot,[base])
        reused={**own,'created':'b'}
        with self.assertRaises(support.CleanupError):support.verified_owned([own],[reused],[base])

    def test_occupied_port_fails_closed(self):
        with self.assertRaises(support.CleanupError):support.wait_ports([14621],timeout=0,check=lambda p:False)


class MetricTests(unittest.TestCase):
    def trace(self,folder,mode='full',action='MOVE',counter=0):
        path=folder/'episode.jsonl'
        meta=make_meta('ep','maskable_ppo_'+mode,{'fixed_economy':mode=='combat'}, {}) if mode!='unknown' else {}
        rows=[{'type':'meta','meta':meta}]+[{'type':'observation','t':t,'money':1000+t,'own_objects':[],'visible_enemies':[]} for t in (0.,1.)]
        if action:rows.append({'type':'action','action':action,'decision_source':'ppo','object_addresses':[1],'target_address':2})
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        result={'episode_id':'ep','kind':'defeat','is_win':False,'outcome_confirmed':True,'elapsed_s':1.,'metrics':{'damage_dealt':10,'own_hp_lost':20}}
        if counter is not None:result['metrics']['attacks_issued']=counter
        return path,result

    def test_zero_attack_is_zero_count_but_na_effectiveness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,result=self.trace(Path(tmp));value=metrics.episode_metrics(path,result)
            self.assertEqual(value['attack_evidence_status'],'ZERO_ATTACKS_OBSERVED')
            self.assertEqual(value['attacks_issued'],0);self.assertEqual(value['attacks_per_min'],0)
            self.assertIsNone(value['attack_effective6_rate']);self.assertIsNone(value['order_repeat_rate'])

    def test_missing_or_corrupt_trace_is_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,result=self.trace(Path(tmp),'unknown',None,None)
            value=metrics.episode_metrics(path,result)
            self.assertEqual(value['attack_evidence_status'],'UNKNOWN_MISSING_ACTION_EVIDENCE');self.assertIsNone(value['attacks_per_min'])
            path.write_text('{broken',encoding='utf-8')
            with self.assertRaises(ValueError):metrics.episode_metrics(path,result)

    def test_counter_trace_conflict_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,result=self.trace(Path(tmp),counter=5);value=metrics.episode_metrics(path,result)
            self.assertEqual(value['attack_evidence_status'],'CONFLICT_COUNTER_AND_TRACE');self.assertIsNone(value['attacks_issued'])

    def test_full_combat_and_unknown_economy_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode,status in [('full','OBSERVED_FULL_CONTROL'),('combat','INCOMPUTABLE_FIXED_SCRIPTED_ECONOMY'),('unknown','UNKNOWN_CONTROL_MODE')]:
                path,result=self.trace(Path(tmp),mode)
                self.assertEqual(metrics.economy_observed(path,result)['status'],status)

    def test_checkpoint_metadata_does_not_override_actual_nested_policy_params(self):
        meta=make_meta('ep','maskable_ppo_full',{'fixed_economy':False},{},model={'path':'checkpoint.zip'})
        _,mode=metrics.trace_context([{'type':'meta','meta':meta}])
        self.assertEqual(mode,'full')

    def test_count_threshold_never_claims_mastery(self):
        value=metrics.summarize([{'kind':'defeat','is_win':False}]*24)
        self.assertFalse(value['mastery_claim'])

    def test_pairing_is_by_identity_not_order(self):
        plan=support.cases(2,2,1)
        left=[{**c,'x':v} for c,v in zip(plan,(1.,2.))]
        right=[{**c,'x':v} for c,v in zip(plan,(1.5,1.))][::-1]
        result=metrics.paired_compare(left,right,'x')
        self.assertEqual(result['n_pairs'],2);self.assertEqual(result['mean_delta_stochastic_minus_deterministic'],-.25)


class AggregateTests(unittest.TestCase):
    def test_valid_models_share_exact_case_plan(self):
        a=matrix();b=copy.deepcopy(a);b['model_key']='r4ep004'
        value=aggregate.aggregate([('a',a),('b',b)])
        self.assertEqual(value['pairing_status'],'VERIFIED_CASE_PLAN')

    def test_bad_frozen_evidence_rejected_even_if_flag_is_true(self):
        value=matrix();value['attempts'][0]['result']['frozen']['optimizer_updates_after']+=1
        with self.assertRaises(support.FrozenViolation):aggregate.validate_matrix(value)

    def test_duplicate_missing_wrong_seed_and_different_protocol_rejected(self):
        for mutator in (
            lambda v:v['modes']['deterministic']['episodes'].append(copy.deepcopy(v['modes']['deterministic']['episodes'][0])),
            lambda v:v['modes']['deterministic']['episodes'].pop(),
            lambda v:v['modes']['deterministic']['episodes'][0].update(engine_seed=99),
            lambda v:v.update(frozen_verified=False),
        ):
            value=matrix();mutator(value)
            with self.assertRaises(support.EvaluationError):aggregate.validate_matrix(value)
        a=matrix();b=copy.deepcopy(a);b['model_key']='r4ep004';b['protocol']['new_setting']=True;b['protocol_hash']=support.digest(b['protocol'])
        with self.assertRaises(support.EvaluationError):aggregate.aggregate([('a',a),('b',b)])

    def test_legacy_is_explicitly_unverified(self):
        value={'schema':'old','model_key':'r1','modes':{'deterministic':{'episodes':[]}},'frozen_verified':True}
        with self.assertRaises(support.EvaluationError):aggregate.aggregate([('x',value)])
        result=aggregate.aggregate([('x',value)],allow_legacy=True)
        self.assertEqual(result['pairing_status'],'UNVERIFIED_LEGACY');self.assertFalse(result['games']['r1']['frozen_verified'])

    def test_duplicate_model_not_silently_merged(self):
        value=matrix()
        with self.assertRaises(support.EvaluationError):aggregate.aggregate([('x',value),('y',value)])


class EntryTests(unittest.TestCase):
    def test_partial_worker_start_failure_cleans_started_worker_and_restores_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';model=project(root);out=Path(tmp)/'out';out.mkdir()
            created=[]
            class Process:
                def __init__(self,**kwargs):self.pid=100+len(created);self.live=False;self.stopped=False;created.append(self)
                def start(self):
                    if len(created)==2:raise OSError('second worker start failed')
                    self.live=True
                def is_alive(self):return self.live
                def terminate(self):self.live=False;self.stopped=True
                def join(self,timeout=None):pass
                def kill(self):self.terminate()
            args=SimpleNamespace(episode_seconds=300.,mode='deterministic',wave_timeout_seconds=1.,cleanup_timeout_seconds=0,
                                 copy_evidence={r['slot']:r for r in support.preflight(root,2)})
            with patch.object(runner,'ROOT',root),patch.object(runner.mp,'get_context',return_value=SimpleNamespace(Process=Process)),patch.object(runner,'snapshot_processes',return_value=[]),patch.object(runner,'wait_ports',return_value=None):
                with self.assertRaisesRegex(OSError,'second worker'):
                    runner.run_wave(support.cases(2,2,1),0,model,support.sha256(model),out,args,time.monotonic()+1)
            self.assertTrue(created[0].stopped)
            self.assertEqual((root/'test-game/spawn.ini').read_bytes(),INI)
            self.assertEqual((root/'test-game-1/spawn.ini').read_bytes(),INI)

    def test_plan_only_never_starts_workers_or_changes_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';model=project(root)
            with patch.object(runner,'ROOT',root),patch.object(runner,'MODELS',{'r1':model}),patch.object(runner,'run_wave',side_effect=AssertionError('must not launch')):
                self.assertEqual(runner.main(['--models','r1','--episodes','2','--parallel','2','--plan-only','--out',str(Path(tmp)/'out')]),0)
            self.assertEqual((root/'test-game/spawn.ini').read_bytes(),INI)

    def test_polluted_output_is_rejected_before_worker_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';model=project(root);out=Path(tmp)/'out';out.mkdir()
            with patch.object(runner,'ROOT',root),patch.object(runner,'MODELS',{'r1':model}):
                with self.assertRaises(support.EvaluationError):runner.main(['--models','r1','--episodes','2','--parallel','2','--plan-only','--out',str(out)])


if __name__=='__main__':unittest.main(verbosity=2)
