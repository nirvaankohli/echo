"""Local lifecycle journal, startup acknowledgement, and isolated Git publishing."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
LOGS = Path.home() / 'Library/Logs/Echo'
BRANCH = 'deployment-logs'


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd or ROOT, check=True, capture_output=True,
                          text=True, timeout=60,
                          env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.strip()


def record(event, **details):
    LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = {'time': datetime.now(timezone.utc).isoformat(), 'event': event, **details}
    # A single append write keeps bot and updater entries from interleaving.
    with (LOGS / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(entry) + '\n')
    return entry


def startup(revision):
    """Acknowledge a watcher that survived startup; not proof of message delivery."""
    entry = record('watcher_started', commit=revision, pid=os.getpid(), run_id=uuid4().hex)
    temporary = LOGS / f'started-{os.getpid()}.tmp'
    temporary.write_text(json.dumps(entry))
    temporary.replace(LOGS / 'started.json')


def wait_for_start(revision, since, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            entry = json.loads((LOGS / 'started.json').read_text())
            if entry['commit'] == revision and datetime.fromisoformat(entry['time']).timestamp() >= since:
                os.kill(entry['pid'], 0)
                return entry['run_id']
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
    raise TimeoutError('New watcher did not acknowledge startup')


def publish():
    """Push only lifecycle data to a separate branch. Retry next poll on failure."""
    journal = LOGS / 'events.jsonl'
    if not journal.exists():
        return
    identity = LOGS / 'instance-id'
    if not identity.exists():
        identity.write_text(uuid4().hex)
    relative = Path('logs') / f'{identity.read_text().strip()}.jsonl'
    remote = git('remote', 'get-url', 'origin')
    with tempfile.TemporaryDirectory(prefix='echo-log-publish-') as folder:
        repo = Path(folder)
        git('init', '-b', BRANCH, cwd=repo)
        git('remote', 'add', 'origin', remote, cwd=repo)
        if git('ls-remote', '--heads', 'origin', f'refs/heads/{BRANCH}', cwd=repo):
            git('fetch', '--depth=1', 'origin', BRANCH, cwd=repo)
            git('checkout', '-B', BRANCH, 'FETCH_HEAD', cwd=repo)
        output = repo / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        content = journal.read_bytes()
        if output.exists() and output.read_bytes() == content:
            return
        output.write_bytes(content)
        git('add', str(relative), cwd=repo)
        git('-c', 'user.name=Echo', '-c', 'user.email=echo@localhost',
            'commit', '-m', 'Record Echo lifecycle events', cwd=repo)
        git('push', 'origin', f'HEAD:refs/heads/{BRANCH}', cwd=repo)
