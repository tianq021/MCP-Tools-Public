"""Regression tests for evidence corruption and controller interference."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "dsh_train"))
import dsh_ab_eval as ab
import dsh_archive_episodes as archival
import dataset_v2
import spec_v2 as spec
from dsh_episode import lockfile, schema, model_policy
from dsh_episode import quality, obs as capture
from dsh_episode.runner import EpisodeRunner
from test_model_policy import FakeModel, obs_row, make_ctx, enemy


class TestReliability(unittest.TestCase):
    def test_failed_place_query_is_recordable_without_crashing_episode(self):
        import asyncio
        runner = EpisodeRunner(SimpleNamespace(episode_id="place-test"))
        runner.client = SimpleNamespace(exec_command=mock.AsyncMock(return_value=SimpleNamespace(
            result_code=1, error_message="no valid cell")))
        records = asyncio.run(runner.execute_place(None, SimpleNamespace(self=1), {
            "production_type_ptr": 100, "building_address": 9, "anchor": [91, 58]}))
        self.assertTrue(records[0]["error"])
        self.assertEqual(schema.validate_action(records[0]), [])

    def test_live_old_lock_is_not_stolen(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "game.lock"
            path.write_text(json.dumps({"pid": os.getpid(), "created_at_epoch": 0}))
            with self.assertRaises(RuntimeError):
                lockfile.acquire(path)
            self.assertEqual(json.loads(path.read_text())["pid"], os.getpid())

    def test_corrupt_lock_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "game.lock"
            path.write_text("{")
            with self.assertRaises(RuntimeError):
                lockfile.acquire(path)
            self.assertEqual(path.read_text(), "{")

    def test_rejected_contender_never_kills_game(self):
        runner = EpisodeRunner(SimpleNamespace(episode_id="blocked", keep_game=False, attach=False))
        with mock.patch("dsh_episode.runner.kill_games") as kill:
            runner.cleanup()
            kill.assert_not_called()

    def test_cleanup_releases_lock_after_termination(self):
        runner = EpisodeRunner(SimpleNamespace(episode_id="owner", keep_game=False, attach=False))
        runner.lock_path = pathlib.Path("unused.lock")
        events = []
        with mock.patch("dsh_episode.runner.kill_games", side_effect=lambda: events.append("kill")), \
             mock.patch("dsh_episode.lockfile.release", side_effect=lambda p: events.append("release")):
            runner.cleanup()
        self.assertEqual(events, ["kill", "release"])

    def test_partial_recording_cannot_be_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            meta = schema.make_meta("ep", "rule", {}, {})
            writer = schema.EpisodeWriter(td, "ep", meta)
            writer.add_row({"type": "note", "message": "evidence"})
            writer.abort()
            before = writer.part_path.read_bytes()
            with self.assertRaises(schema.SchemaError):
                schema.EpisodeWriter(td, "ep", meta)
            self.assertEqual(before, writer.part_path.read_bytes())

    def test_resume_uses_actual_flushed_rows_not_old_checkpoint_count(self):
        with tempfile.TemporaryDirectory() as td:
            meta = schema.make_meta("ep", "rule", {}, {})
            writer = schema.EpisodeWriter(td, "ep", meta)
            writer.add_row({"type": "note", "message": "a"})
            writer.abort()
            ckpt = json.loads(writer.ckpt_path.read_text(encoding="utf-8"))
            ckpt["row_count"] = 0
            writer.ckpt_path.write_text(json.dumps(ckpt), encoding="utf-8")
            resumed = schema.EpisodeWriter(td, "ep", meta, resume=True)
            self.assertEqual(resumed.row_count, 1)
            self.assertEqual(len(resumed.rows), 1)
            resumed.abort()

    def test_archive_preserves_bytes_and_never_fabricates_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            raw = b'{"type":"meta"}\n{partial'
            (root / "ep.jsonl.part").write_bytes(raw)
            (root / "ep.ckpt.json").write_text("{}")
            self.assertEqual(len(archival.archive(root)), 1)
            self.assertTrue((root / "ep.jsonl.part").exists())
            rec = archival.archive(root, True, root / "game.lock")[0]
            self.assertEqual(rec["state"], "archived")
            self.assertFalse(rec["trainable"])
            self.assertEqual((root / "_interrupted/ep/ep.jsonl.part").read_bytes(), raw)
            self.assertFalse((root / "ep.jsonl").exists())
            self.assertEqual(archival.archive(root, True, root / "game.lock"), [])

    def test_archive_refuses_active_controller(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "ep.jsonl.part").write_bytes(b"raw")
            held = lockfile.acquire(root / "game.lock")
            try:
                with self.assertRaises(RuntimeError):
                    archival.archive(root, True, held)
                self.assertTrue((root / "ep.jsonl.part").exists())
            finally:
                lockfile.release(held)

    def test_model_noop_does_not_emit_rule_order(self):
        ctx = make_ctx(obs_row())
        p = model_policy.ModelPolicy(FakeModel({"class_id": spec.ACTION_CLASS_IDS["NOOP"]}), ctx["type_catalog"])
        with mock.patch.object(p.rule, "step", return_value=[{"action": "MOVE"}]):
            self.assertEqual(p.step(ctx), [])
        self.assertEqual(p.stats["model_noops"], 1)

    def test_fallback_advances_rule_state_only_once(self):
        ctx = make_ctx(obs_row())
        p = model_policy.ModelPolicy(FakeModel({"class_id": 0, "confidence": 0.1}), ctx["type_catalog"])
        with mock.patch.object(p.rule, "step", return_value=[{"action": "STOP"}]) as step:
            self.assertEqual(p.step(ctx), [{"action": "STOP"}])
            self.assertEqual(step.call_count, 1)
        self.assertEqual(p.decision_source, "rule")

    def test_attack_label_matches_feature_and_inference_sort(self):
        row = obs_row(enemies=[enemy(901, (110, 90)), enemy(900, (92, 60))])
        label = spec.action_label({"action": "ATTACK", "target_address": 900}, row, {}, 6)
        self.assertEqual(label["target_slot"], 0)

    def test_failed_actions_are_excluded_without_turning_them_into_noop(self):
        row = obs_row()
        episodes = [{"rows": [row, {"type": "action", "action": "MOVE", "coordinates": [1, 1],
                                    "error": "invalid cell"}, row]}]
        import numpy as np
        samples = dataset_v2.collect_samples(episodes, {}, 182, 182, 1.0, np.random.default_rng(1))
        self.assertEqual(samples["dropped_errors"], 1)
        self.assertEqual(len(samples["X"]), 0)

    def test_dataset_rejects_unfinished_crashed_and_synthetic_files(self):
        with tempfile.TemporaryDirectory() as td:
            for name, kind, synthetic in [("missing", None, False), ("crash", "crash", False),
                                          ("manual", "interrupted", False), ("fake", "timeout", True),
                                          ("real", "timeout", False)]:
                recs = [{"type": "meta", "meta": {"schema_version": "episode-v2", "episode_id": name,
                                                  "synthetic": synthetic}}, obs_row()]
                if kind:
                    recs.append({"type": "result", "result": {"kind": kind}})
                (pathlib.Path(td) / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
            self.assertEqual([e["meta"]["episode_id"] for e in dataset_v2.load_episodes(td)], ["real"])

    def test_ab_does_not_treat_missing_or_crashed_match_as_valid(self):
        for record in ({"returncode": 0, "result": {}},
                       {"returncode": 2, "result": {"kind": "crash"}}):
            self.assertTrue(ab.fatal_record(record))
        verdict = ab.evaluate([], 1)
        self.assertFalse(verdict["valid"])
        self.assertFalse(verdict["auto_promote"])

    def test_neutral_damage_cannot_satisfy_opponent_combat_gate(self):
        with tempfile.TemporaryDirectory() as td:
            meta = schema.make_meta("ep", "rule", {}, {})
            writer = schema.EpisodeWriter(td, "ep", meta)
            first = obs_row(enemies=[enemy(900, (92, 60), owner="Neutral")])
            first["t"] = 10.0
            writer.add_row(first)
            writer.add_row(schema.make_action("ATTACK", [3], target_address=900))
            second = obs_row(enemies=[enemy(900, (92, 60), owner="Neutral")])
            second["t"], second["frame"] = 11.0, 610
            second["visible_enemies"][0]["health"] = 10
            writer.add_row(second)
            metrics = schema.derive_metrics(writer.rows)
            self.assertEqual(metrics["damage_dealt"], 0)
            self.assertEqual(metrics["neutral_hp_lost"], 90)
            path = writer.finalize({"kind": "timeout", "rows": writer.row_count})
            q = quality.inspect_episode(path)
            self.assertFalse(q["all_pass"])
            self.assertFalse(q["gates"]["opponents_only"])
            self.assertFalse(q["gates"]["contact_attack_damage"])

    def test_capture_excludes_neutral_and_special_before_policy(self):
        from test_episode_offline import fake_state, fake_obj, fake_map
        from ra2yrproto import ra2yr
        st = fake_state([fake_obj(10, 1, "GI", ra2yr.ABSTRACT_TYPE_INFANTRY, 2, (91, 60)),
                         fake_obj(11, 1, "Oil", ra2yr.ABSTRACT_TYPE_BUILDING, 3, (91, 60)),
                         fake_obj(12, 1, "Building", ra2yr.ABSTRACT_TYPE_BUILDING, 4, (91, 60))])
        row = capture.capture_observation(st, 1, {1: "GI"},
                                          {1: "<human player>", 2: "Computer", 3: "Neutral", 4: "Special"},
                                          fake_map(), "now")
        self.assertEqual([e["addr"] for e in row["visible_enemies"]], [10])

    def test_scout_uses_public_spawn_instead_of_unreachable_edge(self):
        from dsh_episode.policy import RulePolicy
        row = obs_row(base=(89, 126))
        ctx = make_ctx(row, t=100)
        ctx["public_start_cells"] = [[90, 127], [92, 59]]
        p = RulePolicy({"build_order": []}, ctx["type_catalog"])
        moves = [i for i in p.step(ctx) if i["action"] == "MOVE"]
        self.assertEqual(moves[0]["coordinates"], [92 * 256 + 128, 59 * 256 + 128])

    def test_lock_prevents_a_second_process(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            held = lockfile.acquire(pathlib.Path(td) / "game.lock")
            code = ("import sys;sys.path.insert(0,sys.argv[1]);from dsh_episode import lockfile;"
                    "lockfile.acquire(sys.argv[2])")
            try:
                child = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(held)],
                                       capture_output=True, text=True, timeout=15)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn("another collector", child.stderr)
            finally:
                lockfile.release(held)

    def test_ab_checks_pair_assets_and_executed_model_orders(self):
        import copy
        env = {k: "same" for k in ("game_exe_sha256", "bridge_dll_sha256", "spawn_ini_sha256", "map_sha256")}
        env["collector_code_sha256"] = {"policy.py": "same"}
        def record(side):
            return {"side": side, "episode_id": side, "returncode": 0,
                    "initial_cell": [91, 58],
                    "quality": {"all_pass": True, "errors": [], "model_order_ratio": 0.7,
                                "gates": dict.fromkeys(("schema_result_checkpoint", "real_complete", "visibility",
                                                        "opponents_only", "metrics_match_raw", "action_errors"), True)},
                    "result": {"kind": "timeout", "environment": copy.deepcopy(env)}}
        rows = [record("rule"), record("model")]
        self.assertTrue(ab.evaluate(rows, 1)["valid"])
        rows[1]["quality"]["model_order_ratio"] = 0.1
        self.assertFalse(ab.evaluate(rows, 1)["valid"])
        rows[1]["quality"]["model_order_ratio"] = 0.7
        rows[1]["result"]["environment"]["map_sha256"] = "different"
        self.assertFalse(ab.evaluate(rows, 1)["valid"])

    def test_archive_resumes_after_only_raw_file_was_moved(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            raw = root / "ep.jsonl.part"
            raw.write_bytes(b"unfinished")
            (root / "ep.ckpt.json").write_bytes(b"{}")
            rec = archival.archive(root)[0]
            dest = root / "_interrupted/ep"
            dest.mkdir(parents=True)
            archival.save_manifest(dest / "archive.json", rec)
            os.replace(raw, dest / raw.name)
            recovered = archival.archive(root, True, root / "game.lock")
            self.assertEqual(recovered[0]["state"], "archived")
            self.assertTrue((dest / "ep.ckpt.json").exists())


if __name__ == "__main__":
    unittest.main()
