import tempfile
import unittest
from pathlib import Path

from app.timing import JobTiming, historical_seconds, timing_signature


class TimingTests(unittest.TestCase):
    def test_slow_encoding_is_excluded_and_skipped_poll_steps_are_counted(self):
        timing = JobTiming(0)
        timing.begin_image(1, 8)
        self.assertIsNone(timing.snapshot(200)['sampling_remaining_seconds'])
        timing.observe(1, 8, 210)
        self.assertIsNone(timing.snapshot(210)['sampling_remaining_seconds'])
        timing.observe(3, 8, 216)
        estimate = timing.snapshot(217)
        self.assertEqual(estimate['sampling_remaining_seconds'], 14)
        self.assertEqual(estimate['elapsed_seconds'], 217)
        self.assertIsNone(estimate['estimated_total_seconds'])

    def test_duplicate_steps_do_not_change_rate_and_stalls_invalidate_estimate(self):
        timing = JobTiming(0)
        timing.begin_image(1, 30)
        timing.observe(1, 30, 100)
        timing.observe(2, 30, 103)
        timing.observe(2, 30, 110)
        self.assertEqual(timing.snapshot(110)['sampling_remaining_seconds'], 77)
        self.assertIsNone(timing.snapshot(119)['sampling_remaining_seconds'])
        timing.observe(3, 30, 123)
        self.assertGreater(timing.snapshot(123)['sampling_remaining_seconds'], 0)

    def test_batch_resets_and_finishing_has_no_sampling_countdown(self):
        timing = JobTiming(0)
        timing.begin_image(1, 3)
        timing.observe(1, 3, 100)
        timing.observe(2, 3, 103)
        self.assertEqual(timing.snapshot(103)['sampling_remaining_seconds'], 3)
        self.assertIsNone(timing.snapshot(108)['sampling_remaining_seconds'])
        timing.observe(3, 3, 109)
        self.assertIn('解码', timing.snapshot(110)['note'])
        self.assertIsNone(timing.snapshot(110)['sampling_remaining_seconds'])
        timing.begin_image(2, 3)
        self.assertEqual(timing.snapshot(111)['image_index'], 2)
        self.assertIsNone(timing.snapshot(111)['sampling_remaining_seconds'])
        timing.observe(1, 3, 200)
        self.assertIsNone(timing.snapshot(200)['sampling_remaining_seconds'])

    def test_single_step_does_not_invent_sampling_speed(self):
        timing = JobTiming(0)
        timing.begin_image(1, 1)
        timing.observe(1, 1, 200)
        self.assertIsNone(timing.snapshot(200)['sampling_remaining_seconds'])

    def test_history_excludes_failed_legacy_and_other_configs(self):
        def job(created, duration, **changes):
            return dict(created=created, started=10, finished=10+duration,
                        status='completed', timing_signature='same') | changes
        jobs = [job(i, 100+i*10) for i in range(6)]
        jobs += [job(10, 9999, status='failed'), job(11, 9999, timing_signature='different'),
                 job(12, 9999, timing_signature=None), job(13, -1)]
        self.assertEqual(historical_seconds(jobs, 'same'), (130, 5))
        self.assertEqual(historical_seconds(jobs, 'absent'), (None, 0))

    def test_history_counts_down_without_false_zero_after_overrun(self):
        timing = JobTiming(1000, history=240, sample_count=2)
        self.assertEqual(timing.snapshot(1030)['remaining_seconds'], 210)
        self.assertEqual(timing.snapshot(1030)['history_samples'], 2)
        estimate = timing.snapshot(1241)
        self.assertEqual(estimate['estimated_total_seconds'], 240)
        self.assertIsNone(estimate['remaining_seconds'])
        self.assertTrue(estimate['history_exceeded'])

    def test_signature_matches_seed_changes_but_separates_workloads_and_cache(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            model = Path(folder) / 'model.gguf'
            model.write_bytes(b'model')
            config = dict(prompt='cabin', width=512, height=512, steps=8, seed=1, batch_count=1)
            settings = dict(diffusion_model=str(model), text_encoder_cpu=True, max_vram=4)
            signature = timing_signature(config, settings, False, {})
            self.assertEqual(signature, timing_signature(config | {'seed': 123}, settings, False, {}))
            for changes in ({'prompt': ''}, {'width': 1024}, {'steps': 9}, {'batch_count': 2}, {'input_id': 'input'}):
                self.assertNotEqual(signature, timing_signature(config | changes, settings, False, {}))
            self.assertNotEqual(signature, timing_signature(config, settings | {'text_encoder_cpu': False}, False, {}))
            self.assertNotEqual(signature, timing_signature(config, settings, True, {}))
            stat = model.stat()
            cache = {(str(model), stat.st_size, stat.st_mtime_ns): 'hash'}
            self.assertNotEqual(signature, timing_signature(config, settings, False, cache))
            self.assertEqual(signature, timing_signature(config, settings, False, {('unrelated', 1, 1): 'hash'}))
            model.write_bytes(b'new model')
            self.assertNotEqual(signature, timing_signature(config, settings, False, {}))


if __name__ == '__main__':
    unittest.main()
