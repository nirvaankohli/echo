"""Durable tasks, tool checkpoints and a separately acknowledged notification outbox."""
import json
import time

from echo.runtime.queue import Queue

TERMINAL = ('completed', 'failed', 'uncertain', 'cancelled')


class Tasks(Queue):
    def __init__(self, path):
        super().__init__(path)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, sender TEXT NOT NULL, text TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    plan TEXT, history TEXT, result TEXT, error TEXT, cancel INTEGER DEFAULT 0,
                    rounds INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, server TEXT NOT NULL,
                    tool TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, server TEXT NOT NULL,
                    job_id TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                    due REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    text TEXT NOT NULL, promised INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, model TEXT NOT NULL,
                    created REAL NOT NULL, input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL, dollars REAL NOT NULL,
                    status TEXT NOT NULL
                );
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(events)')}
            if 'due' not in columns:
                db.execute('ALTER TABLE events ADD COLUMN due REAL NOT NULL DEFAULT 0')
            if 'attempts' not in columns:
                db.execute('ALTER TABLE events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')

    @staticmethod
    def _notify(db, key, task_id, text):
        db.execute('INSERT OR IGNORE INTO notifications(id,task_id,text,created) VALUES (?,?,?,?)',
                   (key, task_id, text[:12000], time.time()))

    def submit(self, message_id, sender, text, acknowledgement, status="queued"):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE id=?', (message_id,)).fetchone()
            if row:
                if row['sender'] != sender or row['text'] != text:
                    raise ValueError('Conflicting task ID')
                return dict(row)
            now = time.time()
            db.execute('INSERT INTO tasks(id,sender,text,status,created,updated) VALUES (?,?,?,?,?,?)',
                       (message_id, sender, text, status, now, now))
            if acknowledgement:
                self._notify(db, message_id + ':ack', message_id, acknowledgement)
        return self.task(message_id)

    def task(self, task_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            return dict(row) if row else None

    def latest(self, exclude=None):
        with self.db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id!=? AND status!='control' ORDER BY created DESC LIMIT 1",
                             (exclude or '',)).fetchone()
            return dict(row) if row else None

    def active(self, awaiting=False):
        with self.db() as db:
            condition = "status='awaiting_input'" if awaiting else "status IN ('queued','running','waiting','awaiting_input')"
            row = db.execute('SELECT * FROM tasks WHERE cancel=0 AND ' + condition +
                             ' ORDER BY created DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def recent(self, limit=8):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT id,text,status,result,created FROM tasks '
                                                   'ORDER BY created DESC LIMIT ?', (limit,))][::-1]

    def change(self, task_id, **values):
        allowed = {'status', 'plan', 'history', 'result', 'error', 'cancel', 'rounds', 'tool_calls'}
        if not values or not set(values) <= allowed:
            raise ValueError('Invalid task fields')
        with self.db() as db:
            db.execute('UPDATE tasks SET ' + ','.join(k + '=?' for k in values) + ',updated=? WHERE id=?',
                       (*values.values(), time.time(), task_id))

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE tasks SET status='running',updated=? WHERE id=?", (time.time(), row['id']))
            return dict(row) if row else None

    def finish(self, task_id, status, text):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT status,cancel FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not current or current['status'] in TERMINAL:
                return
            if current['cancel']:
                status, text = 'cancelled', 'Task cancelled. Already-started external actions may still have taken effect.'
            db.execute('UPDATE tasks SET status=?,result=?,updated=? WHERE id=?',
                       (status, text, time.time(), task_id))
            db.execute('INSERT OR IGNORE INTO events(id,task_id,kind,text,promised,status,created) VALUES (?,?,?,?,?,?,?)',
                       (task_id + ':final', task_id, status, text[:12000], 1, 'pending', time.time()))

    def cancel(self, task_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE tasks SET cancel=1 WHERE id=? AND status NOT IN (?,?,?,?)',
                       (task_id, *TERMINAL))
            db.execute("UPDATE tasks SET status='cancelled' WHERE id=? AND status IN ('queued','waiting','awaiting_input')", (task_id,))
            db.execute("UPDATE actions SET status='cancelled' WHERE task_id=? AND status='approval_required'", (task_id,))
            db.execute("UPDATE events SET status='quiet' WHERE task_id=? AND kind='approval' AND status='pending'", (task_id,))
            db.execute("UPDATE notifications SET status='cancelled' WHERE task_id=? AND status='pending'", (task_id,))

    def recover_tasks(self):
        # Tool calls in progress have unknown external outcomes. Never replay their task.
        with self.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM tasks WHERE status='running'")]
        for row in rows:
            self.finish(row['id'], 'uncertain',
                        'Echo restarted during this task. Its outcome needs inspection; I have not repeated any actions.')

    def outbox(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT id,text,task_id FROM notifications "
                       "WHERE status='pending' ORDER BY created,id LIMIT 20")]

    def acknowledge(self, key, status):
        if status not in ('sent', 'uncertain'):
            raise ValueError('Invalid delivery status')
        with self.db() as db:
            db.execute("UPDATE notifications SET status=? WHERE id=? AND status='pending'", (status, key))

    def event(self, event_id, task_id, kind, text, promised=False):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO events(id,task_id,kind,text,promised,status,created) VALUES (?,?,?,?,?,?,?)',
                       (event_id, task_id, kind, text[:12000], int(promised), 'pending', time.time()))

    def events(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE status='pending' AND due<=? "
                         "ORDER BY promised DESC,created LIMIT 20", (time.time(),))]

    def defer_event(self, event_id):
        """Back off failed decisions; retain exhausted events for inspection, not endless API calls."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT attempts FROM events WHERE id=? AND status='pending'", (event_id,)).fetchone()
            if row:
                attempts = row['attempts'] + 1
                delay = min(3600, 60 * 2 ** min(attempts - 1, 6))
                db.execute('UPDATE events SET attempts=?,due=?,status=? WHERE id=?',
                           (attempts, time.time() + delay, 'deferred' if attempts >= 5 else 'pending', event_id))

    def decide_event(self, event_id, send):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM events WHERE id=? AND status='pending'", (event_id,)).fetchone()
            if not row:
                return
            if send or row['promised']:
                self._notify(db, row['id'], row['task_id'], row['text'])
            db.execute('UPDATE events SET status=? WHERE id=?', ('sent' if send or row['promised'] else 'quiet', event_id))

    def summary(self):
        with self.db() as db:
            return {table: {row['status']: row['n'] for row in db.execute(
                    f'SELECT status,COUNT(*) n FROM {table} GROUP BY status')}
                    for table in ('tasks', 'notifications', 'jobs', 'events')}
