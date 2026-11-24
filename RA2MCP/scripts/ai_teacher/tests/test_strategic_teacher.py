"""Behavioral regressions for real teacher selection and V3 provenance gates."""
import copy
import dataclasses
import json
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from ai_teacher.strategic_teacher import StrategicTeacher, StrategyConfig
from ai_teacher.tests.test_collector_teacher import _catalog, obj, row
from dsh_rl.full_spec import FullControlSpec, ACTION_SPACE_VERSION
from dual_channel.data import SidecarWriter, SCHEMA, verify, resolve_terminal
from dual_channel.skills import SkillRuntime


def setup():
    cat, groups = _catalog()
    spec = FullControlSpec(cat, groups, 0, 10, action_space_version=ACTION_SPACE_VERSION)
    cy = obj(1, "Allied Construction Yard", "ABSTRACT_TYPE_BUILDING", ptr=900)
    power = obj(2, "Allied Power Plant", "ABSTRACT_TYPE_BUILDING", ptr=901)
    tanks = [obj(10 + i, "Grizzly Battle Tank", "ABSTRACT_TYPE_UNIT", cell=(90+i, 123), ptr=910) for i in range(4)]
    infantry = [obj(20 + i, "GI", "ABSTRACT_TYPE_INFANTRY", cell=(87+i, 125), ptr=911) for i in range(4)]
    observation = row([cy, power] + tanks + infantry, t=60, money=10000)
    observation["houses"] = [{"current_player": True, "is_human": True, "power_output": 200, "power_drain": 50}]
    soa = types.SimpleNamespace(map_width=182, map_height=182)
    return spec, observation, [(90,127), (140,70)], soa


class TeacherBehavior(unittest.TestCase):
    def test_two_power_plants_do_not_starve_refinery(self):
        spec, observation, starts, soa = setup()
        observation["own_objects"].append(obj(3, "Allied Power Plant", "ABSTRACT_TYPE_BUILDING", ptr=901))
        t = StrategicTeacher()
        plans = []
        for i in range(24):
            observation = dict(observation, t=60+i*.5, frame=4000+i*30)
            plans.append(t.choose(observation, spec, starts, soa))
        refinery = [p for p in plans if p.get("intent", {}).get("production_type") == "Allied Ore Refinery"]
        self.assertGreaterEqual(len(refinery), 5)
        self.assertTrue(any(p["skill_id"] == "combat.advance" for p in plans))

    def test_single_slot_military_never_orders_miners_or_mcv(self):
        spec, observation, starts, soa = setup()
        observation["own_objects"] += [obj(90, "Chrono Miner", "ABSTRACT_TYPE_UNIT"), obj(91, "Allied Construction Vehicle", "ABSTRACT_TYPE_UNIT")]
        t = StrategicTeacher()
        for i in range(30):
            observation = dict(observation, t=80+i, frame=5000+i*60)
            p = t.choose(observation, spec, starts, soa)
            if p["queue"] == "combat":
                self.assertTrue(p["intent"]["object_addresses"])
                self.assertNotIn(90, p["intent"]["object_addresses"])
                self.assertNotIn(91, p["intent"]["object_addresses"])
                self.assertEqual(len(p["intent"]["object_addresses"]), 1)

    def test_movement_is_not_reissued_each_half_second(self):
        spec, observation, starts, soa = setup()
        t = StrategicTeacher()
        seen = {}
        for i in range(60):
            observation = dict(observation, t=60+i*.5, frame=4000+i*30)
            p = t.choose(observation, spec, starts, soa)
            if p["queue"] == "combat":
                a = p["intent"]["object_addresses"][0]
                if a in seen:
                    self.assertGreaterEqual(observation["t"] - seen[a], 7)
                seen[a] = observation["t"]
        self.assertGreater(len(seen), 1)

    def test_hidden_truth_does_not_change_decisions(self):
        spec, observation, starts, soa = setup()
        a = StrategicTeacher().choose(observation, spec, starts, soa)
        dirty = copy.deepcopy(observation)
        dirty["hidden_enemy_count"] = 999
        dirty["hidden_objects"] = [{"cell": [1,2]}]
        dirty["visible_enemies"] = [obj(99, "Enemy", "ABSTRACT_TYPE_UNIT", cell=(91,123)) | {"cell_shrouded": True}]
        cat, groups = _catalog()
        other_spec = FullControlSpec(cat, groups, 0, 10, action_space_version=ACTION_SPACE_VERSION)
        b = StrategicTeacher().choose(dirty, other_spec, starts, soa)
        self.assertEqual(a, b)

    def test_attack_target_and_actor_match_current_decoder(self):
        spec, observation, starts, soa = setup()
        observation["visible_enemies"] = [obj(99,"Enemy Tank","ABSTRACT_TYPE_UNIT",cell=(94,122)) | {"owner":"Computer","owner_ptr":2}]
        t = StrategicTeacher()
        attacks = []
        for i in range(20):
            r = dict(observation, t=70+i*.5, frame=4200+i*30)
            p = t.choose(r, spec, starts, soa)
            self.assertTrue(p["action_mask"][p["action"]])
            if p["intent"]:
                self.assertEqual(p["intent"], spec.intent(p["action"], StrategicTeacher.visible(r), starts, soa))
                if p["intent"]["action"] == "ATTACK":
                    attacks.append(p)
        self.assertTrue(attacks)
        self.assertTrue(all(a["intent"]["target_address"] == 99 for a in attacks))

    def test_illegal_or_nan_config_rejected(self):
        for values in ({"army_cap":1000},{"attack_after_seconds":float("nan")},{"army_cap":8.5}):
            with self.assertRaises(ValueError):
                StrategyConfig(**values)


class ProvenanceAndLifecycle(unittest.TestCase):
    def test_candidates_preserve_episode_and_cannot_auto_promote(self):
        from dual_channel.dataset import export_candidates
        from dual_channel.evolution import propose
        with tempfile.TemporaryDirectory(dir=ROOT/'_work') as temp:
            base=pathlib.Path(temp)
            base.resolve().relative_to(ROOT)
            run=base/'run';run.mkdir()
            w=SidecarWriter(run/'dual-channel','ep',{'config':StrategyConfig().as_dict()})
            observation={'frame':1,'own_objects':[],'houses':[]}
            record={'schema':SCHEMA,'source':'collector_teacher','teacher_sha256':'test-synthetic',
                    'episode_id':'ep','decision_id':'ep:d0','skill_instance_id':'s0','skill_id':'economy.bootstrap',
                    'action_idx':2,'action_mask':[True]*301,'observation_frame':1,'next_frame':2,
                    'observation':observation,'next_observation':dict(observation,frame=2),
                    'slot_bindings':[],
                    'intent':{'action':'PRODUCE'},'features':[0.]*1801,
                    'feedback':{},'admission':{'bc_label_eligible':True}}
            w.append(record);w.finish(dict(observation,frame=2),'timeout')
            dataset=export_candidates(run,base/'dataset')
            self.assertFalse(dataset['formal_training_eligible'])
            self.assertFalse(dataset['ppo_eligible'])
            self.assertEqual(dataset['episode_split']['unassigned'],['ep'])
            candidate=propose(run,base/'candidate.json')
            self.assertFalse(candidate['promotion']['approved'])
            self.assertFalse(candidate['generated_python_executed'])
            self.assertEqual(candidate['status'],'CANDIDATE_NOT_PROMOTED')
            with self.assertRaises(FileExistsError):
                propose(run,base/'candidate.json')

    def test_reset_failure_without_kind_still_saves_and_closes(self):
        from dual_channel.run_teacher import finish_attempt
        env=types.SimpleNamespace()
        env.close=lambda: setattr(env,'closed',True)
        with tempfile.TemporaryDirectory(dir=ROOT/'_work') as temp:
            pathlib.Path(temp).resolve().relative_to(ROOT)
            result={'status':'FAILED','failure':'launch failed'}
            finish_attempt(env,pathlib.Path(temp),result,failure='launch failed')
            self.assertTrue(env.closed)
            self.assertEqual(json.loads((pathlib.Path(temp)/'RESULT.json').read_text())['end_kind'],'launch_failed')

    def test_terminal_is_from_flags_not_process_exit(self):
        self.assertEqual(resolve_terminal({"houses":[]}, "win")["kind"], "UNKNOWN")
        obs={"frame":1,"houses":[{"current_player":True,"is_human":True,"is_loser":True}]}
        self.assertTrue(resolve_terminal(obs,"win")["conflict"])
        self.assertFalse(resolve_terminal(obs,"win")["terminated"])
        self.assertEqual(resolve_terminal(obs,"defeat")["kind"], "defeat")

    def test_deploy_success_is_state_derived_not_direct(self):
        spec, obs, starts, soa=setup()
        runtime=SkillRuntime('ep')
        p={"skill_id":"economy.deploy","intent":{"action":"DEPLOY"},"slot_bindings":[]}
        before=row([], t=0)
        sid=runtime.start(p,before,'d0')
        runtime.observe(obs)
        events=runtime.drain()
        self.assertEqual(events[-1]['state'],'SUCCEEDED')
        self.assertEqual(events[-1]['evidence_level'],'STATE_DERIVED')
        self.assertFalse(events[-1]['effect_actor_eligible'])
        self.assertNotIn(sid,runtime.active)

    def test_trace_integrity_and_source_validation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '_work') as temp:
            pathlib.Path(temp).resolve().relative_to(ROOT)
            out=pathlib.Path(temp)/'data'
            w=SidecarWriter(out,'ep',{})
            observation={"frame":1,"own_objects":[],"houses":[]}
            r={"schema":SCHEMA,"source":"collector_teacher","teacher_sha256":"abc",
               "decision_id":"ep:d0","skill_instance_id":"s0","skill_id":"wait.progress",
               "action_idx":0,"action_mask":[True]+[False]*300,"observation_frame":1,"next_frame":2,
               "observation":observation,"next_observation":dict(observation,frame=2),"intent":None,
               "feedback":{},"admission":{"bc_label_eligible":False}}
            w.append(r)
            w.finish(dict(observation,frame=2),'timeout')
            self.assertTrue(verify(out)['pass'])
            (out/'transitions.jsonl').write_text('[]',encoding='utf-8')
            self.assertFalse(verify(out)['pass'])


if __name__ == '__main__':
    unittest.main()
