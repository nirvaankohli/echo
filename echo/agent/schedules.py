"""Durable, owner-scoped timers that enqueue work without model calls while idle."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from croniter import croniter

from echo.agent.tasks import TERMINAL


def clock_context():
    zone = os.environ.get('ECHO_TIMEZONE')
    if not zone:
        target = str(Path('/etc/localtime').resolve())
        zone = target.split('/zoneinfo/')[-1] if '/zoneinfo/' in target else 'UTC'
    return {'timezone': zone, 'now': datetime.now(ZoneInfo(zone)).isoformat()}


def next_cron(expression, zone, after):
    if not isinstance(expression, str) or len(expression) > 120 or len(expression.split()) != 5:
        raise ValueError('Use a five-field cron expression: minute hour day month weekday')
    return croniter(expression, datetime.fromtimestamp(after, ZoneInfo(zone)),
                    max_years_between_matches=8).get_next(datetime).timestamp()


FIELDS = {
    'title': {'type': 'string', 'description': 'Short name for this schedule.'},
    'timezone': {'type': 'string', 'description': 'IANA timezone, e.g. America/New_York. Use the supplied clock context unless the user specifies another.'},
    'at': {'type': ['string', 'null'], 'description': 'One-time future ISO 8601 datetime WITH UTC offset. Must agree with timezone. Null for recurring jobs.'},
    'cron': {'type': ['string', 'null'], 'description': 'Recurring five-field cron expression in timezone. Null for one-time jobs.'},
    'reminder': {'type': 'string', 'description': 'Exact reminder to text when due, or empty if no reminder requested.'},
    'prompt': {'type': 'string', 'description': 'Self-contained work to execute WHEN DUE, or empty for reminder-only. Include necessary context, target and constraints; do not execute it now.'},
}
SPECS = {
    'monitor_create': ('Create recurring read-only monitoring of a connected account. First discover and test its read tools. Save the exact account, read procedure, baseline/cursor strategy and importance criteria in prompt. Unchanged checks stay quiet. Manage with schedule_list/cancel; cancel and recreate to change monitoring.', {key: FIELDS[key] for key in ('title', 'timezone', 'cron', 'prompt')}),
    'schedule_create': ('Save a one-time reminder or recurring agent job. Exactly one of at/cron; at least one of reminder/prompt. Returns saved ID and next run. No shell cron or calendar setup needed.', FIELDS),
    'schedule_update': ('Replace the complete definition of an existing schedule. List first to find its ID. This re-enables it. Already-running occurrences are not changed.', {'id': {'type': 'string'}, **FIELDS}),
    'schedule_list': ('List your active schedules and recent inactive ones, including next run and last task status.', {}),
    'schedule_cancel': ('Disable a schedule and cancel its queued occurrences and unsent reminders. Already-running work may finish.', {'id': {'type': 'string'}}),
}


class Schedules:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS monitors (
                    schedule_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS monitor_alerts (
                    schedule_id TEXT NOT NULL, key TEXT NOT NULL,
                    PRIMARY KEY(schedule_id,key)
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY, sender TEXT NOT NULL, title TEXT NOT NULL,
                    timezone TEXT NOT NULL, at TEXT, cron TEXT, reminder TEXT NOT NULL,
                    prompt TEXT NOT NULL, source_task TEXT NOT NULL, authorization TEXT NOT NULL,
                    next_run REAL, enabled INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS schedules_due ON schedules(enabled,next_run);
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    schedule_id TEXT NOT NULL, due REAL NOT NULL, task_id TEXT UNIQUE NOT NULL,
                    definition TEXT NOT NULL, PRIMARY KEY(schedule_id,due)
                );
            ''')

    def tools(self):
        tools, routes = [], {}
        for name, (description, fields) in SPECS.items():
            tools.append(dict(type='function', name=name, description=description, strict=False,
                              parameters=dict(type='object', properties=fields,
                                              required=list(fields), additionalProperties=False)))
            routes[name] = (None, name, 'echo_scheduler', 'read' if name == 'schedule_list' else 'private_write')
        return tools, routes

    def listing(self, sender):
        with self.store.db() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT * FROM schedules WHERE lower(sender)=? ORDER BY enabled DESC,updated DESC LIMIT 100',
                (sender.casefold(),))]
            for row in rows:
                last = db.execute('SELECT t.id,t.status FROM schedule_runs r JOIN tasks t ON t.id=r.task_id '
                                  'WHERE r.schedule_id=? ORDER BY r.due DESC LIMIT 1', (row['id'],)).fetchone()
                row['last_run'] = dict(last) if last else None
                row['monitoring'] = bool(db.execute('SELECT 1 FROM monitors WHERE schedule_id=?', (row['id'],)).fetchone())
                if row['next_run'] is not None:
                    row['next_run'] = datetime.fromtimestamp(row['next_run'], ZoneInfo(row['timezone'])).isoformat()
                for key in ('sender', 'authorization', 'source_task'):
                    row.pop(key)
        return rows

    def call(self, name, args, task_id, action_id, now=None):
        now = time.time() if now is None else now
        if name not in SPECS or set(args) != set(SPECS[name][1]):
            raise ValueError('Invalid schedule tool arguments')
        monitoring = name == 'monitor_create'
        if monitoring:
            args = {**args, 'at': None, 'reminder': ''}
            if not args['cron'] or not isinstance(args['prompt'], str) or not args['prompt'].strip():
                raise ValueError('Monitoring requires a recurrence and read procedure')
            name = 'schedule_create'
        task = self.store.task(task_id)
        if name == 'schedule_list':
            return {'schedules': self.listing(task['sender']), **clock_context()}
        schedule_id = args.get('id') or hashlib.sha256(action_id.encode()).hexdigest()[:24]
        if not isinstance(schedule_id, str) or len(schedule_id) > 64:
            raise ValueError('Invalid schedule ID')
        if name != 'schedule_cancel':
            for field, maximum in (('title', 120), ('reminder', 2000), ('prompt', 8000)):
                if not isinstance(args[field], str) or len(args[field]) > maximum:
                    raise ValueError(f'{field} must be text, at most {maximum} characters')
            if not args['title'].strip() or not (args['reminder'].strip() or args['prompt'].strip()):
                raise ValueError('Provide a title and a reminder or work prompt')
            ZoneInfo(args['timezone'])
            if (args['at'] is None) == (args['cron'] is None):
                raise ValueError('Provide exactly one of at or cron')
            if args['at'] is not None:
                at = datetime.fromisoformat(args['at'])
                if at.tzinfo is None or at.utcoffset() != at.astimezone(ZoneInfo(args['timezone'])).utcoffset():
                    raise ValueError('at must include the correct UTC offset for the timezone')
                due = at.timestamp()
                if due <= now:
                    raise ValueError('That time has passed; choose a future time')
            else:
                due = next_cron(args['cron'], args['timezone'], now)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT cancel,status,revision FROM tasks WHERE id=?', (task_id,)).fetchone()
            if current['cancel'] or current['status'] in TERMINAL or current['revision'] != task['revision']:
                raise ValueError('The requesting task changed; propose the schedule again')
            existing = db.execute('SELECT * FROM schedules WHERE id=? AND lower(sender)=?',
                                  (schedule_id, task['sender'].casefold())).fetchone()
            if name == 'schedule_update' and db.execute('SELECT 1 FROM monitors WHERE schedule_id=?', (schedule_id,)).fetchone():
                raise ValueError('Cancel and recreate a monitor to change its definition')
            if name != 'schedule_create' and not existing:
                raise ValueError('Schedule not found for this owner')
            if name == 'schedule_create' and existing:
                return {'id': schedule_id, 'saved': True}  # Same action retried, never duplicate it.
            if name == 'schedule_cancel':
                db.execute('UPDATE schedules SET enabled=0,next_run=NULL,updated=? WHERE id=?', (now, schedule_id))
                self._cancel_queued(db, schedule_id)
                return {'id': schedule_id, 'enabled': False, 'note': 'Already-running work may finish.'}
            active = db.execute('SELECT COUNT(*) FROM schedules WHERE lower(sender)=? AND enabled=1',
                                (task['sender'].casefold(),)).fetchone()[0]
            if active >= 50 and not (existing and existing['enabled']):
                raise ValueError('Limit of 50 active schedules; cancel an old schedule first')
            updates = [r['text'] for r in db.execute('SELECT text FROM task_updates WHERE task_id=? ORDER BY revision', (task_id,))]
            authorization = {'request': task['text'], 'follow_ups': updates}
            if existing:
                previous = json.loads(existing['authorization'])
                previous['amendments'] = previous.get('amendments', [])[-19:] + [authorization]
                authorization = previous
                self._cancel_queued(db, schedule_id)
            db.execute('INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET title=excluded.title,timezone=excluded.timezone,at=excluded.at,'
                       'cron=excluded.cron,reminder=excluded.reminder,prompt=excluded.prompt,source_task=excluded.source_task,'
                       'authorization=excluded.authorization,next_run=excluded.next_run,enabled=1,updated=excluded.updated',
                       (schedule_id, task['sender'], args['title'], args['timezone'], args['at'], args['cron'],
                        args['reminder'], args['prompt'], task_id, json.dumps(authorization), due, 1, now, now))
            if monitoring:
                db.execute('INSERT OR IGNORE INTO monitors(schedule_id) VALUES (?)', (schedule_id,))
        return {'id': schedule_id, 'saved': True, 'timezone': args['timezone'],
                'next_run': datetime.fromtimestamp(due, ZoneInfo(args['timezone'])).isoformat()}

    @staticmethod
    def _cancel_queued(db, schedule_id):
        db.execute("UPDATE tasks SET cancel=1,status='cancelled' WHERE status IN ('queued','awaiting_input') AND id IN "
                   '(SELECT task_id FROM schedule_runs WHERE schedule_id=?)', (schedule_id,))
        stopped = ('SELECT r.task_id FROM schedule_runs r JOIN tasks t ON t.id=r.task_id '
                   "WHERE r.schedule_id=? AND t.status='cancelled'")
        db.execute("UPDATE actions SET status='cancelled' WHERE status IN ('approval_required','approved') "
                   'AND task_id IN (' + stopped + ')', (schedule_id,))
        db.execute("UPDATE events SET status='quiet' WHERE status='pending' AND task_id IN (" + stopped + ')', (schedule_id,))
        db.execute("UPDATE notifications SET status='cancelled' WHERE status='pending' AND task_id IN (" + stopped + ')', (schedule_id,))
        db.execute("UPDATE notifications SET status='cancelled' WHERE status='pending' AND id IN "
                   "(SELECT task_id||':reminder' FROM schedule_runs WHERE schedule_id=?)", (schedule_id,))

    def tick(self, now=None):
        """Atomically enqueue at most one catch-up occurrence per schedule; never overlap work."""
        now = time.time() if now is None else now
        count = 0
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 50', (now,)).fetchall():
                next_run = next_cron(row['cron'], row['timezone'], now) if row['cron'] else None
                db.execute('UPDATE schedules SET next_run=?,enabled=?,updated=? WHERE id=?',
                           (next_run, int(next_run is not None), now, row['id']))
                active = db.execute("SELECT 1 FROM schedule_runs r JOIN tasks t ON r.task_id=t.id WHERE r.schedule_id=? "
                                    "AND t.status IN ('queued','running','waiting','awaiting_input')", (row['id'],)).fetchone()
                if active:
                    continue
                task_id = 'schedule:' + row['id'] + ':' + str(int(row['next_run'] * 1000000))
                definition = dict(row)
                db.execute('INSERT INTO schedule_runs VALUES (?,?,?,?)',
                           (row['id'], row['next_run'], task_id, json.dumps(definition)))
                work = bool(row['prompt'].strip())
                db.execute('INSERT INTO tasks(id,sender,text,status,created,updated,result) VALUES (?,?,?,?,?,?,?)',
                           (task_id, row['sender'], row['prompt'] or row['reminder'], 'queued' if work else 'completed',
                            now, now, None if work else row['reminder']))
                if row['reminder'].strip():
                    self.store._notify(db, task_id + ':reminder', task_id, row['reminder'])
                count += 1
        return count

    def origin(self, task_id):
        with self.store.db() as db:
            row = db.execute('SELECT definition,due FROM schedule_runs WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            return None
        saved = json.loads(row['definition'])
        return {'schedule_id': saved['id'], 'title': saved['title'], 'timezone': saved['timezone'],
                'due': datetime.fromtimestamp(row['due'], ZoneInfo(saved['timezone'])).isoformat(),
                'authorization': json.loads(saved['authorization']), 'prompt': saved['prompt'],
                'reminder': saved['reminder']}

    def run(self, wake, stop):
        while not stop.is_set():
            if self.tick():
                wake.set()
            stop.wait(5)
