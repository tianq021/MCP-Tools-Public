"""Offline tests for the model registry (champion/candidate/promote/rollback)."""
import pathlib
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

import dsh_model_registry as registry  # noqa: E402


class TestModelRegistry(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.td.name) / "registry.json"
        self.m1 = pathlib.Path(self.td.name) / "m1"
        self.m2 = pathlib.Path(self.td.name) / "m2"
        for m in (self.m1, self.m2):
            m.mkdir()
            (m / "weights.npz").write_bytes(b"fake-weights")
        self.state = {"champion": None, "candidates": [], "history": [],
                      "checkpoints": [], "updated_at": None}

    def tearDown(self):
        self.td.cleanup()

    def test_register_promote_rollback(self):
        registry.register(self.state, self.m1, "candidate",
                          {"val_accuracy": 0.5}, path=self.path)
        self.assertEqual(len(self.state["candidates"]), 1)
        registry.promote(self.state, self.m1, path=self.path)
        self.assertEqual(self.state["champion"]["model_dir"], str(self.m1))
        self.assertEqual(len(self.state["candidates"]), 0)

        registry.register(self.state, self.m2, "candidate",
                          {"val_accuracy": 0.7}, path=self.path)
        registry.promote(self.state, self.m2, path=self.path)
        self.assertEqual(self.state["champion"]["model_dir"], str(self.m2))
        self.assertEqual(len(self.state["history"]), 1)

        old = registry.rollback(self.state, path=self.path)
        self.assertEqual(old["model_dir"], str(self.m1))
        self.assertEqual(self.state["champion"]["model_dir"], str(self.m1))
        # m2 goes back to candidates
        self.assertTrue(any(c["model_dir"] == str(self.m2)
                            for c in self.state["candidates"]))

    def test_promote_unregistered_fails(self):
        with self.assertRaises(ValueError):
            registry.promote(self.state, self.m1, path=self.path)

    def test_rollback_without_history_fails(self):
        with self.assertRaises(ValueError):
            registry.rollback(self.state, path=self.path)

    def test_checkpoints_capped_and_persisted(self):
        for i in range(3):
            registry.add_checkpoint(self.state, f"ep-{i}", self.m1,
                                    {"damage": i}, path=self.path)
        loaded = registry.load(self.path)
        self.assertEqual(len(loaded["checkpoints"]), 3)
        self.assertEqual(loaded["checkpoints"][0]["episode_id"], "ep-0")
        self.assertEqual(loaded["checkpoints"][0]["weights_sha256"],
                         registry.sha256_file(self.m1 / "weights.npz"))

    def test_changed_candidate_is_not_promoted(self):
        registry.register(self.state, self.m1, path=self.path)
        (self.m1 / "weights.npz").write_bytes(b"different")
        with self.assertRaises(ValueError):
            registry.promote(self.state, self.m1, path=self.path)
        self.assertIsNone(self.state["champion"])

    def test_missing_weights_cannot_be_registered(self):
        (self.m1 / "weights.npz").unlink()
        with self.assertRaises(ValueError):
            registry.register(self.state, self.m1, path=self.path)
        self.assertEqual(self.state["candidates"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
