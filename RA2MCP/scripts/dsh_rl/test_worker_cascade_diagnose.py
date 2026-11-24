"""P0-3 Worker 级联分析器的离线测试（合成日志，不启动游戏）。"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/dsh_rl"))

import worker_cascade_diagnose as diag  # noqa: E402


class WorkerCascadeTests(unittest.TestCase):
    def test_detects_simultaneous_relaunch_and_full_rebuild(self):
        lines = []
        for index in range(6):
            lines.append(f"[01:42:20] PPO launched pid={100 + index} episode=ep{index}")
        lines.append("[01:42:26] render-off enabled: pid=100 va=0x4f4480 "
                     "bytes_after=c3 evidence=reports\\rl\\run\\workers\\0\\render-off\\ep0")
        lines.append("[01:42:27] real game failed to relaunch after 4 attempts: "
                     "RenderOffError('no game window found for pid 106')")
        lines.append("[01:42:28] learner IPC failure 1: EOFError(); rebuilding workers")
        for index in range(6):
            lines.append(f"[01:42:30] PPO launched pid={200 + index} episode=epb{index}")
        lines.append("[01:43:30] learner IPC failure 2: EOFError(); rebuilding workers")
        for index in range(6):
            lines.append(f"[01:43:32] PPO launched pid={300 + index} episode=epc{index}")
        for index in range(3, 7):
            lines.append(f"[01:44:0{index}] learner IPC failure {index}: EOFError(); "
                         "rebuilding workers")
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "synthetic.log"
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = diag.analyze_log(log)
        ids = [finding["id"] for finding in result["findings"]]
        self.assertIn("SIMULTANEOUS_RELAUNCH_BURST", ids)
        self.assertIn("FULL_VEC_REBUILD_ON_SINGLE_WORKER_FAILURE", ids)
        self.assertIn("RENDER_OFF_WINDOW_RACE", ids)
        self.assertIn("RECOVERY_ABORT", ids)
        self.assertEqual(result["ppo_launches"], 18)
        self.assertEqual(result["ipc_failures"], 6)
        self.assertEqual(result["relaunch_failures"], 1)
        self.assertEqual(result["status"], "FAILED_CASCADE")

    def test_healthy_log_has_no_cascade_findings(self):
        lines = []
        for index in range(6):
            lines.append(f"[01:00:0{index}] PPO launched pid={100 + index} episode=ep{index}")
        for index in range(120):
            lines.append(f"[01:0{index // 60}:{index % 60:02d}] ingame frame={index} objects=28")
        lines.append("[01:05:00] PPO launched pid=400 episode=next1")
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "healthy.log"
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = diag.analyze_log(log)
        self.assertEqual(result["ipc_failures"], 0)
        self.assertEqual(result["relaunch_failures"], 0)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["status"], "HEALTHY_OR_SINGLE_INCIDENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
