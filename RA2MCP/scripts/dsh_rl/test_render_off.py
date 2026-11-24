"""Offline checks for the opt-in render-off mode; no game process is patched.

These tests cover the safety contract only:
  * validation rejects non-game / dead / outside-workspace targets,
  * the on-disk prologue is verified and a tampered executable is refused,
  * the watchdog detects stalls / read failures / action failures,
  * the target constants still match the verified disassembly evidence,
  * an EpisodeRunner without a launched process refuses render-off.

The real patch/unpatch round trip on a live game is covered by
scripts/headless/render_off_runtime.py and the integration smoke run
(reports/headless-render-off-integration.md).
"""
import pathlib
import shutil
import sys
import time
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import render_off  # noqa: E402
from dsh_episode.render_off import (RenderOffController, RenderOffError,  # noqa: E402
                                    validate_target_process, verify_disk_prologue)
import render_patch  # noqa: E402  (scripts/headless is added by render_off)

GAME_COPIES = [ROOT / "test-game-headless", ROOT / "test-game-headless-2",
               ROOT / "test-game", ROOT / "test-game-1"]
TMP_ROOT = ROOT / "reports" / "headless" / ".tmp" / "render-off-tests"


class TestTargetConstants(unittest.TestCase):
    def test_target_constants_match_verified_disassembly(self):
        spec = render_patch.TARGETS["render"]
        self.assertEqual(spec["va"], 0x4F4480)
        self.assertEqual(spec["expect"], b"\x83\xec\x08")
        self.assertEqual(spec["patch"], b"\xc3\x90\x90")

    def test_three_patch_modes_still_present(self):
        self.assertEqual(set(render_patch.TARGETS),
                         {"render", "blit", "tactical"})


class TestProcessValidation(unittest.TestCase):
    def test_dead_pid_is_rejected(self):
        with self.assertRaises(RenderOffError):
            validate_target_process(99999999)

    def test_non_game_process_is_rejected(self):
        # the test runner itself is not gamemd-spawn-ra2yrcpp.exe
        with self.assertRaises(RenderOffError):
            validate_target_process(  # our own PID is alive but not a game
                __import__("os").getpid())

    def test_outside_workspace_is_rejected(self):
        # E:\hj is never allowed; when the path does not exist it must still be
        # refused (missing executable / outside workspace), never silently ok.
        outside = pathlib.Path(r"E:\hj\Yuris Revenge\Game")
        with self.assertRaises(RenderOffError):
            verify_disk_prologue(outside)

    def test_episode_runner_refuses_mode_without_launched_game(self):
        from dsh_episode.runner import EpisodeRunner
        runner = EpisodeRunner(types.SimpleNamespace(
            episode_id="unit-test-render-off",
            episodes_dir=str(ROOT / "reports" / "headless" / ".tmp" / "episodes")))
        with self.assertRaises(RuntimeError):
            runner.enable_render_off()


class TestDiskPrologue(unittest.TestCase):
    def test_all_test_copies_have_expected_prologue(self):
        checked = 0
        for copy in GAME_COPIES:
            if not (copy / "gamemd-spawn-ra2yrcpp.exe").exists():
                continue
            evidence = verify_disk_prologue(copy)
            self.assertEqual(evidence["prologue_hex"], "83ec08")
            self.assertEqual(evidence["patch_hex"], "c39090")
            checked += 1
        self.assertGreater(checked, 0, "no test copies found to verify")

    def test_tampered_executable_is_refused(self):
        src = ROOT / "test-game-headless" / "gamemd-spawn-ra2yrcpp.exe"
        if not src.exists():
            self.skipTest("test-game-headless copy not available")
        tmp_dir = TMP_ROOT / "tampered-copy"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        dst = tmp_dir / "gamemd-spawn-ra2yrcpp.exe"
        shutil.copyfile(src, dst)
        offset = render_off.pe_file_offset(dst, render_patch.TARGETS["render"]["va"])
        data = bytearray(dst.read_bytes())
        data[offset + 1] = 0x00  # corrupt the `sub esp, imm8` prologue
        dst.write_bytes(bytes(data))
        with self.assertRaises(RenderOffError):
            verify_disk_prologue(tmp_dir)

    def test_already_patched_disk_copy_is_refused(self):
        src = ROOT / "test-game-headless" / "gamemd-spawn-ra2yrcpp.exe"
        if not src.exists():
            self.skipTest("test-game-headless copy not available")
        tmp_dir = TMP_ROOT / "patched-copy"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        dst = tmp_dir / "gamemd-spawn-ra2yrcpp.exe"
        shutil.copyfile(src, dst)
        offset = render_off.pe_file_offset(dst, render_patch.TARGETS["render"]["va"])
        data = bytearray(dst.read_bytes())
        data[offset:offset + 3] = render_patch.TARGETS["render"]["patch"]
        dst.write_bytes(bytes(data))
        with self.assertRaises(RenderOffError):
            verify_disk_prologue(tmp_dir)

    def test_non_pe_file_is_refused(self):
        tmp_dir = TMP_ROOT / "not-a-pe"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "gamemd-spawn-ra2yrcpp.exe").write_bytes(b"not a pe image")
        with self.assertRaises(RenderOffError):
            verify_disk_prologue(tmp_dir)


class TestWatchdog(unittest.TestCase):
    def make(self, **kwargs):
        defaults = dict(pid=0, evidence_dir=None, stall_seconds=2.0,
                        read_fail_limit=3, action_fail_limit=10)
        defaults.update(kwargs)
        return RenderOffController(**defaults)

    def test_frame_stall_detected_and_cleared(self):
        ctrl = self.make(stall_seconds=2.0)
        self.assertIsNone(ctrl.note_state(100))
        ctrl._frame_at = time.time() - 10.0
        violation = ctrl.note_state(100)
        self.assertEqual(violation[0], "frame_stall")
        # a new frame clears the stall
        self.assertIsNone(ctrl.note_state(200))

    def test_read_failure_streak(self):
        ctrl = self.make(read_fail_limit=3)
        self.assertIsNone(ctrl.note_read(False))
        self.assertIsNone(ctrl.note_read(False))
        violation = ctrl.note_read(False)
        self.assertEqual(violation[0], "read_fail_streak")
        self.assertEqual(violation[1], 3)
        self.assertIsNone(ctrl.note_read(True))  # success resets

    def test_action_failure_streak(self):
        ctrl = self.make(action_fail_limit=10)
        self.assertIsNone(ctrl.note_actions(5, 4))
        self.assertIsNone(ctrl.note_actions(1, 4))
        violation = ctrl.note_actions(1, 4)
        self.assertEqual(violation[0], "action_fail_streak")
        self.assertIsNone(ctrl.note_actions(5, 0))  # success resets

    def test_check_health_combines_and_ignores_healthy_sequence(self):
        ctrl = self.make(stall_seconds=1.0, read_fail_limit=2)
        self.assertEqual(ctrl.check_health(1, True, ok_count=3, invalid_count=0), [])
        ctrl._frame_at = time.time() - 5.0
        self.assertIn("frame_stall",
                      [v[0] for v in ctrl.check_health(1, False)])
        self.assertIn("read_fail_streak",
                      [v[0] for v in ctrl.check_health(2, False)])


class TestPatchStateGuards(unittest.TestCase):
    def test_assert_patch_intact_requires_patch(self):
        ctrl = RenderOffController(pid=0, evidence_dir=None)
        with self.assertRaises(RenderOffError):
            ctrl.assert_patch_intact()

    def test_disable_without_patch_is_noop(self):
        ctrl = RenderOffController(pid=0, evidence_dir=None)
        self.assertTrue(ctrl.disable().get("restored"))


if __name__ == "__main__":
    unittest.main()
