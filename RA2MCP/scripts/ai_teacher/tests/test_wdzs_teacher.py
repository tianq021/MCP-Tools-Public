"""Offline tests of the executable adapter using real catalog/episode schemas."""
import copy
import json
import pathlib
import sys
import types
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"scripts"))
from ai_teacher.wdzs_teacher import WdzsTeacher,TEAMS
from dsh_rl.full_spec import FullControlSpec

BUNDLE=ROOT/"reports/wdzs-advisor-2026-11-02/round-02"
PUBLIC=json.loads((ROOT/"_runs_g0l1/run-wdzs-advice-02/public-catalog.json").read_text(encoding="utf-8"))
EPISODE=next((ROOT/"_runs_g0l1/run-wdzs-advice-02/episodes").glob("*.jsonl.part"))
OBS=[r for r in (json.loads(l) for l in EPISODE.read_text(encoding="utf-8").splitlines()) if r.get("type")=="observation"]
assert OBS,"real observations required; never pass an empty replay"

class TeacherTests(unittest.TestCase):
    def setUp(self):
        self.teacher=WdzsTeacher(BUNDLE)
        self.spec=FullControlSpec(PUBLIC["types"],PUBLIC["groups"],PUBLIC["country_index"],PUBLIC["tech_level"],action_space_version="full_ground_v2p1")
        self.map=types.SimpleNamespace(map_width=128,map_height=128)
        self.starts=[[92,59],[25,65]]
    def plan(self,row):return self.teacher.plan(row,self.spec,self.starts,self.map)
    def test_deploy_initial_base(self):
        p=self.plan(copy.deepcopy(OBS[0]))
        self.assertEqual(p["phase"],"DEPLOY_BASE")
        self.assertEqual(p["intents"][0]["action"],"DEPLOY")
    def test_exact_source_teams_are_loaded(self):
        self.assertEqual(self.teacher.library["teams"][TEAMS[0]]["name"],"Allied Grizzly Rush Medium")
        self.assertEqual(self.teacher.library["teams"][TEAMS[1]]["name"],"Allied Bombard")
    def test_history_never_repeats_barracks_as_missing_refinery_fallback(self):
        row=copy.deepcopy(OBS[-2]);row["t"]=100
        p=self.plan(row)
        buildings=[i.get("production_type") for i in p["intents"] if i["action"]=="PRODUCE"]
        self.assertNotIn("Allied Barracks",buildings)
    def test_production_obeys_real_mask(self):
        for row in OBS[::9]:
            p=self.plan(copy.deepcopy(row));mask=self.spec.masks(row,self.starts,self.map)
            for i in p["intents"]:
                if i["action"]=="PRODUCE":self.assertTrue(mask[2+self.spec.slot_by_ptr[i["production_type_ptr"]]])
    def test_faction_construction_and_mod_units(self):
        allowed={t["name"] for t in self.spec.types}
        for row in OBS[::10]:
            for i in self.plan(copy.deepcopy(row))["intents"]:
                if i["action"]=="PRODUCE":
                    self.assertIn(i["production_type"],allowed)
                    self.assertNotIn(i["production_type"],("Soviet Tesla Reactor","Particle Cannon Tank","Fortress"))
    def test_no_observation_mutation(self):
        row=copy.deepcopy(OBS[-1]);before=copy.deepcopy(row)
        self.plan(row);self.assertEqual(row,before)
    def test_hidden_fields_cannot_change_plan(self):
        a=copy.deepcopy(OBS[-1]);b=copy.deepcopy(a);b["hidden_enemy_count"]=999;b["future_win"]=True
        self.assertEqual(self.plan(a),WdzsTeacher(BUNDLE).plan(b,self.spec,self.starts,self.map))
    def test_raw_commands_never_pretend_to_be_301_labels(self):
        self.assertIsNone(self.plan(copy.deepcopy(OBS[-1]))["student_301_label"])
    def test_excludes_miners_and_mcv_from_army(self):
        row=copy.deepcopy(OBS[-1]);row["t"]=120
        row["own_objects"].append({"addr":999,"type_name":"Chrono Miner","abstract":"ABSTRACT_TYPE_UNIT","health":100,"on_map":True,"in_limbo":False,"cell":[92,59],"type_ptr":0})
        p=self.plan(row)
        for i in p["intents"]:
            if i["action"] in ("MOVE","ATTACK"):self.assertNotIn(999,i["object_addresses"])
    def test_single_scout_not_entire_army(self):
        row=copy.deepcopy(OBS[-1]);row["t"]=20
        p=self.plan(row)
        for i,g in zip(p["intents"],p["groups"]):
            if g=="scout":self.assertEqual(len(i["object_addresses"]),1)
    def test_squad_does_not_spam_identical_move_every_tick(self):
        row=copy.deepcopy(OBS[-1]);row["t"]=120
        a=self.plan(row);row=copy.deepcopy(row);row["t"]+=.5;b=self.plan(row)
        self.assertTrue(any(i["action"]=="MOVE" for i in a["intents"]))
        self.assertFalse(any(i["action"]=="MOVE" for i in b["intents"]))

if __name__=="__main__":unittest.main()
