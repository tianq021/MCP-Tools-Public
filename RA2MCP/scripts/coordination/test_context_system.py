"""Self-tests for the coordination context store and MCP tool functions.

Runs against an isolated temporary project root copied from the real
coordination/ directory, so it never mutates the live context or project files.

Usage:
    python scripts/coordination/test_context_system.py
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

REAL_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_COORD = REAL_ROOT / 'coordination'


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def make_sandbox():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ra2ctx-'))
    shutil.copytree(REAL_COORD, tmp / 'coordination',
                    ignore=shutil.ignore_patterns('.context.lock'))
    os.environ['RA2_CONTEXT_ROOT'] = str(tmp)
    os.environ['RA2_COORD_DIR'] = str(tmp / 'coordination')
    return tmp


class ContextStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_sandbox()
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        global store, server
        import context_store as store
        import mcp_context_server as server
        cls.store, cls.server = store, server
        cls.originals = {name: sha(REAL_COORD / name)
                         for name in ('通用上下文记忆规范_v1.0.md',
                                      'RA2_AI_CURRENT_CONTEXT_v1.0.md',
                                      'RA2_AI_PROJECT_STATE_v1.0.json')}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_state_schema_and_version(self):
        state = store.load_state()
        self.assertEqual(store.validate_state(state), [])
        self.assertGreaterEqual(state['context_version'], 2)
        self.assertIn('model_artifacts', state)
        for name in ('R1', 'R2', 'R3'):
            self.assertRegex(state['model_artifacts'][name]['sha256'], r'^[0-9a-f]{64}$')

    def test_02_mcp_read_tools(self):
        got = server.context_get(events_limit=3)
        self.assertTrue(got['ok'])
        self.assertEqual(got['context_version'], store.current_context_version())
        self.assertIn('current_context_md', got)
        searched = server.context_search(query='R3', limit=5)
        self.assertTrue(searched['ok'])
        self.assertGreater(searched['matches'], 0)
        paths = server.files_search(query='PROJECT_STATE', limit=5)
        self.assertTrue(paths['ok'])
        self.assertGreater(paths['matches'], 0)
        diff = server.context_diff(from_version=1)
        self.assertTrue(diff['ok'])
        self.assertGreaterEqual(diff['to_version'], 2)

    def test_03_events_are_append_only(self):
        before = len(store.read_events(limit=0))
        version = store.current_context_version()
        result = server.events_append('test_event_append', 'append only test',
                                      base_context_version=version)
        self.assertTrue(result['ok'], result)
        after = len(store.read_events(limit=0))
        self.assertEqual(after, before + 1)
        self.assertEqual(store.current_context_version(), version + 1)
        # appending again with the stale base version must fail
        bad = server.events_append('test_stale', 'stale version', base_context_version=version)
        self.assertFalse(bad['ok'])
        self.assertIn('base_context_version', bad['error'])

    def test_04_task_cycle_and_snapshot(self):
        version = store.current_context_version()
        created = server.tasks_create('sandbox task', 'sandbox objective',
                                      base_context_version=version, owner='DSH')
        self.assertTrue(created['ok'], created)
        task_id = created['task']['task_id']
        self.assertEqual(store.current_context_version(), version + 1)
        updated = server.tasks_update(task_id, base_context_version=version + 1,
                                      status='COMPLETED', result_summary='ok')
        self.assertTrue(updated['ok'], updated)
        self.assertEqual(updated['task']['status'], 'COMPLETED')
        self.assertTrue(store.snapshot_path(store.current_context_version()).exists())

    def test_05_artifact_register_safety(self):
        version = store.current_context_version()
        rel = 'coordination/README.md'
        result = server.artifacts_register(rel, base_context_version=version,
                                           artifact_type='context_file', role='readme')
        self.assertTrue(result['ok'], result)
        entry = next(a for a in store.load_artifacts()['artifacts'] if a['path'] == rel)
        self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
        outside = server.artifacts_register(r'C:\Windows\win.ini',
                                           base_context_version=store.current_context_version())
        self.assertFalse(outside['ok'])

    def test_06_originals_untouched(self):
        for name, digest in self.originals.items():
            self.assertEqual(sha(REAL_COORD / name), digest,
                             f'provided original changed: {name}')
            self.assertEqual(sha(self.tmp / 'coordination' / name), digest,
                             f'sandbox copy unexpectedly changed: {name}')

    def test_07_state_json_roundtrip(self):
        state = json.loads((self.tmp / 'coordination' / 'PROJECT_STATE.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(state['context_version'], store.current_context_version())
        for rel in ('TASK_QUEUE.json', 'ARTIFACT_INDEX.json'):
            json.loads((self.tmp / 'coordination' / rel).read_text(encoding='utf-8'))


if __name__ == '__main__':
    failed = 0
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContextStoreTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
