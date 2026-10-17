"""Durable message states. Never replay an action whose outcome is uncertain."""
import json
import sqlite3
import time
from contextlib import contextmanager


class Queue:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, error TEXT, created REAL NOT NULL, due REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS pending ON messages(status, due, created);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            ''')
        path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, message_id, payload, cursor=None):
        encoded = json.dumps(payload, sort_keys=True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM messages WHERE id=?', (message_id,)).fetchone()
            if row and row['payload'] != encoded:
                raise ValueError('Message ID reused with different content')
            db.execute('INSERT OR IGNORE INTO messages(id,payload,status,created) VALUES (?,?,?,?)',
                       (message_id, encoded, 'queued', time.time()))
            if cursor:
                self._cursor(db, *cursor)
        return self.get(message_id)

    def get(self, message_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
        return dict(row) if row else None

    def pending(self, statuses=('queued',)):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                f'SELECT * FROM messages WHERE status IN ({",".join("?" for _ in statuses)}) '
                'AND due<=? ORDER BY created LIMIT 100', (*statuses, time.time()))]

    def update(self, message_id, status, *, result=None, error=None, delay=0):
        with self.db() as db:
            db.execute('UPDATE messages SET status=?,result=?,error=?,due=? WHERE id=?',
                       (status, result, error, time.time() + delay, message_id))

    def recover(self, active):
        with self.db() as db:
            db.execute('UPDATE messages SET status="uncertain",error="Interrupted during action" WHERE status=?',
                       (active,))

    @staticmethod
    def _cursor(db, key, value):
        db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE '
                   'SET value=MAX(value,excluded.value)', (key, value))

    def cursor(self, key, value=None):
        with self.db() as db:
            if value is not None:
                self._cursor(db, key, value)
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return row['value'] if row else None

    def counts(self):
        with self.db() as db:
            return {row['status']: row['n'] for row in db.execute(
                'SELECT status,COUNT(*) AS n FROM messages GROUP BY status')}
