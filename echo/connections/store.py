"""Private local credentials and connection metadata, never inside the repository."""
import hashlib
import json
import secrets
import time

from echo.runtime.config import state_dir
from echo.runtime.queue import Queue
from echo.connections.catalog import BY_ID


class Connections(Queue):
    def __init__(self, path=None):
        super().__init__(path or state_dir() / 'connections.sqlite3')
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, label TEXT NOT NULL,
                url TEXT NOT NULL, auth TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'setup', tool_count INTEGER NOT NULL DEFAULT 0,
                checked REAL, error TEXT, created REAL NOT NULL)''')
            db.execute('CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS browser_tokens (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, expires REAL NOT NULL)')

    def secret(self, key, value=None, *, connection=None):
        with self.db() as db:
            if value is not None:
                db.execute('BEGIN IMMEDIATE')
                if connection and not db.execute('SELECT 1 FROM connections WHERE id=?', (connection,)).fetchone():
                    raise ValueError('Connection was removed')
                db.execute('INSERT INTO secrets VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
            row = db.execute('SELECT value FROM secrets WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else None

    def get(self, id):
        with self.db() as db:
            row = db.execute('SELECT * FROM connections WHERE id=?', (id,)).fetchone()
        if not row:
            raise ValueError('Connection not found')
        return dict(row)

    def all(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM connections ORDER BY created')]

    def _label(self, db, provider, label, *, exclude=None, automatic=False):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise ValueError('Use an account label of 1–80 characters')
        label = label.strip()
        used = {r['label'].casefold() for r in db.execute(
            'SELECT id,label FROM connections WHERE provider=?', (provider,)) if r['id'] != exclude}
        candidate, number = label, 2
        while candidate.casefold() in used:
            if not automatic:
                raise ValueError('That account label is already used for this service. Choose another, such as School or Home.')
            suffix = f' {number}'
            candidate = label[:80-len(suffix)] + suffix
            number += 1
        return candidate

    def add(self, provider, label, url, auth, *, automatic_label=False):
        id = secrets.token_hex(12)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            label = self._label(db, provider, label, automatic=automatic_label)
            db.execute('INSERT INTO connections(id,provider,label,url,auth,created) VALUES (?,?,?,?,?,?)',
                       (id, provider, label, url, auth, time.time()))
        return id

    def rename(self, id, label):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT provider FROM connections WHERE id=?', (id,)).fetchone()
            if not row:
                raise ValueError('Connection not found')
            label = self._label(db, row['provider'], label, exclude=id)
            db.execute('UPDATE connections SET label=? WHERE id=?', (label, id))

    def update(self, id, **values):
        if not values or not set(values) <= {'enabled', 'status', 'tool_count', 'checked', 'error'}:
            raise ValueError('Invalid connection update')
        with self.db() as db:
            db.execute('UPDATE connections SET ' + ','.join(key+'=?' for key in values) + ' WHERE id=?', (*values.values(), id))

    def remove(self, id):
        with self.db() as db:
            db.execute('DELETE FROM connections WHERE id=?', (id,))
            db.execute("DELETE FROM secrets WHERE key LIKE ?", (id+':%',))

    def browser_token(self, kind, ttl):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM browser_tokens WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO browser_tokens VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), kind, time.time()+ttl))
        return token

    def valid_token(self, token, kind, consume=False):
        if not isinstance(token, str) or len(token) > 200:
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT 1 FROM browser_tokens WHERE hash=? AND kind=? AND expires>?', (digest, kind, time.time())).fetchone()
            if consume:
                db.execute('DELETE FROM browser_tokens WHERE hash=? AND kind=?', (digest, kind))
        return row is not None

    def configs(self):
        return {'account_'+r['id']: {**({'native_google': r['id']} if r['auth']=='google' else
                {'url': r['url'], 'auth_connection': r['id']}),
                'provider': r['provider'],
                'display_name': f'{BY_ID.get(r["provider"], {}).get("name", "Custom MCP")} / {r["label"]}'}
                for r in self.all() if r['enabled'] and r['status']=='connected'}
