"""Unit tests for the observer data pipeline (offline only, no game launch).

Run:
  $env:PYTHONPATH='E:\RA2MCP\scripts'
  F:\conda\envs\python_mmx_LLM\python.exe -m unittest observer.tests.test_observer -v
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(r"E:\RA2MCP")
sys.path.insert(0, str(ROOT / "scripts"))

from observer import config, extract, store  # noqa: E402


def _house(idx, name, money=1000, power_out=100, power_drain=50,
           defeated=False, loser=False, winner=False):
    return [idx, name, money, power_out, power_drain,
            1 if defeated else 0, 1 if loser else 0, 1 if winner else 0]


def _obj(owner, type_name, x, y, health=100, on_map=1, limbo=0, mission=5):
    return [owner, type_name, health, x, y, on_map, limbo, mission]


def _sample(frame, houses, objects, wall=None):
    return {"frame": frame, "wall": wall if wall is not None else 1000.0 + frame / 62.0,
            "houses": houses, "objects": objects}


def synthetic_samples() -> list[dict]:
    """3 AI participants + observer + Neutral/Special; house 3 dies at 600, house 2 at 1200."""
    samples = []
    for frame in (0, 62, 124, 600, 1200):
        houses = [
            _house(0, "<human player>", defeated=True),          # observer
            _house(1, "Computer", defeated=False),
            _house(2, "Computer", defeated=frame >= 1200),
            _house(3, "Computer", defeated=frame >= 600),
            _house(4, "Neutral"),
            _house(5, "Special"),
        ]
        objects = [
            # house 1 builds 2 buildings and has 2 moving tanks
            _obj(1, "Allied Construction Yard", 50, 50),
            _obj(1, "Allied Power Plant", 52, 50),
            _obj(1, "Grizzly Battle Tank", 60 + frame // 62, 70),
            _obj(1, "Grizzly Battle Tank", 61 + frame // 62, 71, mission=2),
            # house 2 one building one tank
            _obj(2, "Soviet Construction Yard", 150, 150),
            _obj(2, "Rhino Heavy Tank", 140, 160 + frame // 62),
            # house 3 buildings (dies at 600)
            _obj(3, "Soviet Construction Yard", 100, 100),
        ]
        samples.append(_sample(frame, houses, objects))
    # duplicate/frozen frames must be dropped
    samples.append(_sample(1200, samples[-1]["houses"], samples[-1]["objects"]))
    samples.append(_sample(1200, samples[-1]["houses"], samples[-1]["objects"]))
    return samples


class ConfigTests(unittest.TestCase):
    def test_spawn_ini_text(self):
        text = config.build_observer_spawn_ini(seed=1234)
        for needle in ("IsSpectator=Yes", "PlayerCount=8", "AIPlayers=7",
                       "AIDifficulty=0", "GameSpeed=1", "Credits=10000",
                       "UnitCount=0", "Superweapons=No", "FogOfWar=No",
                       "Seed=1234", "DefeatedBecomesObserver=Yes"):
            self.assertIn(needle, text)
        self.assertIn("[HouseColors]", text)
        self.assertIn("Multi8=7", text)
        self.assertIn("[HouseHandicaps]", text)

    def test_spawn_ini_validation(self):
        with self.assertRaises(ValueError):
            config.build_observer_spawn_ini(seed=1, ai_count=8)
        with self.assertRaises(ValueError):
            config.build_observer_spawn_ini(seed=1, game_speed=0)
        # GameSpeed > 1 is legal but slower; it must not raise.
        self.assertIn("GameSpeed=3", config.build_observer_spawn_ini(seed=1, game_speed=3))

    def test_install_config_in_fake_copy(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="observer-cfg-",
                                            dir=str(ROOT / "reports/observer-collect-2026-11-03")))
        try:
            game = tmp / "test-game-selftest"
            game.mkdir()
            (game / "gamemd-spawn-ra2yrcpp.exe").write_bytes(b"fake-exe")
            (game / "libra2yrcpp.dll").write_bytes(b"fake-dll")
            fp = config.install_observer_config(
                game, map_source=config.DEFAULT_MAP, seed=42, ai_count=7)
            self.assertTrue((game / "spawn.ini").is_file())
            self.assertTrue((game / "spawnmap.ini").is_file())
            self.assertEqual(fp["spawn_ini_sha256"],
                             config.sha256_file(game / "spawn.ini"))
            self.assertIn("AIPlayers=7", (game / "spawn.ini").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                config.install_observer_config(tmp, map_source=config.DEFAULT_MAP, seed=1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="observer-extract-",
                                                 dir=str(ROOT / "reports/observer-collect-2026-11-03")))
        self.recording = self.tmp / "states_full.jsonl"
        with open(self.recording, "w", encoding="utf-8") as fh:
            for sample in synthetic_samples():
                fh.write(json.dumps(sample) + "\n")
        self.result = extract.extract_recording(
            self.recording, match_id="selftest-match-001", map_name="test-map", seed=7,
            observer_indices=(0,), exclude_names=frozenset({"Neutral", "Special"}),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dedup_and_counts(self):
        self.assertEqual(self.result["sample_count"], 5)  # duplicate frames dropped
        self.assertEqual(self.result["frame_start"], 0)
        self.assertEqual(self.result["frame_end"], 1200)

    def test_participants_and_winner(self):
        self.assertEqual(self.result["participants"], [1, 2, 3])
        self.assertEqual(self.result["winner_house"], 1)
        self.assertEqual(self.result["winner_name"], "Computer")
        self.assertEqual(self.result["end_reason"], "WINNER")

    def test_first_defeated(self):
        self.assertEqual(self.result["first_defeated_house"], 3)
        self.assertEqual(self.result["first_defeat_frame"], 600)
        ranks = [(e["house_index"], e["elimination_rank"])
                 for e in self.result["elimination_order"]]
        self.assertEqual(ranks, [(3, 1), (2, 2)])

    def test_build_orders(self):
        orders = {row["house_index"]: row for row in self.result["houses"]}
        self.assertEqual(orders[1]["build_count"], 2)
        names = [b["type_name"] for b in orders[1]["build_order"]]
        self.assertEqual(names, ["Allied Construction Yard", "Allied Power Plant"])

    def test_timeline_has_units_and_buildings(self):
        last = self.result["timeline"][-1]["houses"][1]
        self.assertEqual(last["buildings"], 2)
        self.assertEqual(last["units"], 2)
        # mission=2 (Move) is an offensive/active mission
        self.assertEqual(self.result["houses"][0]["house_index"], 1)
        self.assertIsNotNone(self.result["houses"][0]["first_attack_frame"])

    def test_artifacts_written(self):
        arts = extract.write_extract_artifacts(self.result, self.tmp / "out")
        self.assertTrue((self.tmp / "out" / "match.json").is_file())
        self.assertTrue((self.tmp / "out" / "first_defeat" / "house.json").is_file())
        self.assertTrue((self.tmp / "out" / "winner_bundle" / "status.json").is_file())
        self.assertEqual(arts["first_defeat_house"], 3)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="observer-store-",
                                                 dir=str(ROOT / "reports/observer-collect-2026-11-03")))
        self.recording = self.tmp / "states_full.jsonl"
        with open(self.recording, "w", encoding="utf-8") as fh:
            for sample in synthetic_samples():
                fh.write(json.dumps(sample) + "\n")
        self.result = extract.extract_recording(
            self.recording, match_id="selftest-match-002", map_name="test-map", seed=8,
            observer_indices=(0,), exclude_names=frozenset({"Neutral", "Special"}))
        self.conn = store.connect(self.tmp / "observer.sqlite")
        store.insert_match(self.conn, self.result)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["match_id"], "selftest-match-002")
        self.assertEqual(row["winner_house"], 1)
        n_houses = self.conn.execute("SELECT COUNT(*) FROM houses").fetchone()[0]
        self.assertEqual(n_houses, 3)
        n_samples = self.conn.execute("SELECT COUNT(*) FROM house_samples").fetchone()[0]
        self.assertEqual(n_samples, 5 * 3)
        n_build = self.conn.execute("SELECT COUNT(*) FROM build_orders").fetchone()[0]
        self.assertGreaterEqual(n_build, 3)

    def test_csv_export_and_report(self):
        files = store.export_csv(self.conn, self.tmp / "csv")
        self.assertEqual(len(files), 4)
        for path in files:
            self.assertTrue(pathlib.Path(path).is_file())
        report = store.match_report(self.result)
        self.assertIn("首个被击败", report)
        self.assertIn("Computer", report)


class ExistingRecordingRegression(unittest.TestCase):
    """Regression on a real recording already in the repo (no new game)."""

    RECORDING = (ROOT / "reports/ai-teacher/wdzs-godstoy/live-full/"
                 "20261030-183814-godstoy/states_full.jsonl")

    def test_extract_real_recording(self):
        if not self.RECORDING.is_file():
            self.skipTest(f"existing recording not found: {self.RECORDING}")
        result = extract.extract_recording(
            self.RECORDING, match_id="regression-godstoy-183814",
            map_name="God's Toy / unknown", seed=None,
            observer_indices=(), exclude_names=frozenset({"Neutral", "Special"}),
        )
        self.assertGreater(result["sample_count"], 100)
        self.assertGreater(result["frame_end"], result["frame_start"])
        # the historical recording shows the first AI being eliminated around frame 16024
        self.assertIsNotNone(result["first_defeat_frame"])
        self.assertGreaterEqual(result["first_defeat_frame"], 15000)
        self.assertLessEqual(result["first_defeat_frame"], 16500)
        self.assertIn("elimination_order", result)
        self.assertTrue(result["houses"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
