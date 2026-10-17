"""Small, durable Codex job runner. No messaging or response generation here."""

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def job_root():
    path = Path(os.environ.get('ECHO_JOBS_DIR', '~/Library/Application Support/Echo/jobs')).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def job_path(job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job ID')
    return job_root() / job_id


def save(path, data):
    temp = path / 'state.tmp'
    temp.write_text(json.dumps(data))
    temp.replace(path / 'state.json')


def get_job(job_id: str) -> dict:
    """Read status and the last part of the final answer for an Echo-created job."""
    path = job_path(job_id)
    data = json.loads((path / 'state.json').read_text())
    if data['status'] in ('queued', 'running'):
        # The inherited lock lives for the worker's entire lifetime, including startup.
        with (path / 'alive.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                data = json.loads((path / 'state.json').read_text())
                if data['status'] in ('queued', 'running'):
                    data['status'] = 'interrupted'
    answer = path / 'answer.txt'
    if data['status'] == 'completed' and answer.exists():
        with answer.open('rb') as stream:
            stream.seek(max(0, answer.stat().st_size - 12000))
            data['result'] = stream.read().decode('utf-8', errors='replace')
    return data


def list_jobs() -> list[dict]:
    """Return the 20 most recent Echo jobs, newest first."""
    paths = sorted(job_root().glob('*/state.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{k: v for k, v in get_job(p.parent.name).items() if k != 'result'} for p in paths[:20]]


def start_job(task: str) -> dict:
    """Start one coding task in ECHO_CODEX_WORKSPACE; return immediately with its ID."""
    if not task.strip() or len(task) > 16000:
        raise ValueError('Task must contain 1–16000 characters')
    configured = os.environ.get('ECHO_CODEX_WORKSPACE', '').strip()
    if not configured:
        raise ValueError('Set ECHO_CODEX_WORKSPACE first')
    workspace = Path(configured).expanduser().resolve(strict=True)
    if not workspace.is_dir() or workspace == ROOT or ROOT.is_relative_to(workspace) or workspace.is_relative_to(ROOT):
        raise ValueError('Use a separate project folder, outside the running Echo repository')
    executable = shutil.which('codex')
    if not executable:
        raise ValueError('Install Codex CLI and sign in in the Echo Mac login')
    with (job_root() / 'start.lock').open('a') as gate:
        fcntl.flock(gate, fcntl.LOCK_EX)
        for state in job_root().glob('*/state.json'):
            if get_job(state.parent.name)['status'] in ('queued', 'running'):
                raise ValueError('A Codex job is already running; check its status first')
        path = job_path(uuid.uuid4().hex)
        path.mkdir(mode=0o700)
        data = dict(job_id=path.name, status='queued', created_at=time.time(), workspace=str(workspace))
        with (path / 'alive.lock').open('a') as lock, (path / 'worker.log').open('a') as log:
            fcntl.flock(lock, fcntl.LOCK_EX)
            save(path, data)
            try:
                # Pass the lifetime lock to a detached worker so MCP can close immediately.
                worker = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), path.name, str(lock.fileno()), executable],
                    cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log,
                    start_new_session=True, pass_fds=(lock.fileno(),),
                    env={k: v for k, v in os.environ.items() if k not in (
                        'OPENAI_API_KEY', 'BLUEBUBBLES_PASSWORD')},
                )
                worker.stdin.write(task.encode())
                worker.stdin.close()
                threading.Thread(target=worker.wait, daemon=True).start()
            except OSError:
                data['status'] = 'failed'
                save(path, data)
                raise
        return data


def run_worker(job_id, executable, task):
    path = job_path(job_id)
    data = json.loads((path / 'state.json').read_text())
    data.update(status='running', started_at=time.time())
    save(path, data)
    command = [executable, '--no-daemon', '-a', 'never', 'exec', '--sandbox', 'workspace-write',
               '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
               '--cd', data['workspace'], '--output-last-message', str(path / 'answer.txt'), '-']
    try:
        # Keep the CLI and its children in a killable group; cap unattended work at 30 minutes.
        with (path / 'codex.log').open('w') as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                       start_new_session=True)
            try:
                process.communicate(task.encode(), timeout=1800)
                answer = path / 'answer.txt'
                data['status'] = ('completed' if process.returncode == 0
                                  and answer.exists() and answer.stat().st_size else 'failed')
                data['exit_code'] = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                data['status'] = 'timed_out'
    except OSError as exc:
        data.update(status='failed', error=type(exc).__name__)
    data['finished_at'] = time.time()
    save(path, data)
    notify_completion(job_id)


def notify_completion(job_id):
    """Best-effort wake-up; durable job state and active-job polling are the fallback."""
    from urllib.request import Request, ProxyHandler, build_opener
    try:
        state = Path(os.environ.get('ECHO_STATE_DIR', '~/Library/Application Support/Echo')).expanduser()
        config = json.loads((state / 'settings.json').read_text())
        request = Request(f"http://127.0.0.1:{int(config['port'])}/v2/events/codex",
                          data=json.dumps({'job_id': job_id}).encode(),
                          headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=2):
            pass
    except Exception:
        pass  # Main login may be offline; its saved job watcher will reconcile later.


if __name__ == '__main__':
    os.umask(0o077)
    # argv[2] is intentionally kept open: its flock marks this worker as alive.
    run_worker(sys.argv[1], sys.argv[3], sys.stdin.read())
