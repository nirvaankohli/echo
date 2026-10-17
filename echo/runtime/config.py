"""Per-login configuration and state, separate from the auto-updated source."""
from contextlib import contextmanager
import fcntl
import json
import os
import logging
import signal
import threading
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def state_dir():
    path = Path(os.environ.get('ECHO_STATE_DIR', '~/Library/Application Support/Echo')).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def load_env():
    load_dotenv(state_dir() / '.env')
    load_dotenv(ROOT / '.env')


def settings():
    load_env()
    return json.loads((state_dir() / 'settings.json').read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path) + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
    os.replace(str(path) + '.tmp', path)
    path.chmod(0o600)


def background(target, *args):
    """A dead worker must restart the process, not leave a falsely healthy server."""
    def supervise():
        try:
            target(*args)
        except Exception as exc:
            logging.critical('Background worker failed (%s); restarting', type(exc).__name__)
            os.kill(os.getpid(), signal.SIGTERM)
    thread = threading.Thread(target=supervise, daemon=True)
    thread.start()
    return thread


@contextmanager
def single_instance(role):
    with (state_dir() / f'{role}.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'{role} is already running') from None
        yield
