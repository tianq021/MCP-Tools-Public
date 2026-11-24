"""Durable command deduplication and evidence-backed skill outcomes."""
import hashlib
import json
import sqlite3
import time


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT);
            CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, skill TEXT, outcome TEXT, evidence TEXT, created REAL);
        ''')
        # A crash after send cannot be safely retried.
        self.db.execute("UPDATE commands SET result=? WHERE result IS NULL", (json.dumps({'status': 'outcome_unknown', 'reason': 'service restarted'}),))
        self.db.commit()

    def begin(self, request_id, params):
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        old = self.db.execute('SELECT hash,result FROM commands WHERE id=?', (request_id,)).fetchone()
        if old:
            if old[0] != digest:
                raise ValueError('request_id reused with different arguments')
            return json.loads(old[1]) if old[1] else {'status': 'outcome_unknown'}
        self.db.execute('INSERT INTO commands VALUES (?,?,NULL)', (request_id, digest))
        self.db.commit()
        return None

    def finish(self, request_id, result):
        self.db.execute('UPDATE commands SET result=? WHERE id=?', (json.dumps(result), request_id))
        self.db.commit()
        return result

    def record(self, skill, outcome, evidence):
        self.db.execute('INSERT INTO episodes(skill,outcome,evidence,created) VALUES (?,?,?,?)', (skill, outcome, json.dumps(evidence), time.time()))
        self.db.commit()

    def skills(self):
        return [dict(skill=r[0], outcome=r[1], count=r[2]) for r in self.db.execute('SELECT skill,outcome,COUNT(*) FROM episodes GROUP BY skill,outcome')]
