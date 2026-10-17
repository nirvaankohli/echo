"""Validate origin/main in isolation, fast-forward a clean checkout, then restart."""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def run(*args, cwd=ROOT, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=300)


def update():
    # Never stash, reset, or overwrite someone's edits. Include untracked files.
    if run('git', 'status', '--porcelain', capture=True).stdout.strip():
        print('Update skipped: checkout has local changes.', flush=True)
        return
    if run('git', 'branch', '--show-current', capture=True).stdout.strip() != 'main':
        print('Update skipped: checkout is not on main.', flush=True)
        return
    run('git', 'fetch', 'origin', 'main')
    target = run('git', 'rev-parse', 'origin/main', capture=True).stdout.strip()
    current = run('git', 'rev-parse', 'HEAD', capture=True).stdout.strip()
    if target == current:
        return
    run('git', 'merge-base', '--is-ancestor', current, target)
    with tempfile.TemporaryDirectory(prefix='echo-update-') as folder:
        candidate = Path(folder) / 'candidate'
        run('git', 'worktree', 'add', '--detach', str(candidate), target)
        try:
            run(sys.executable, '-m', 'venv', str(candidate / '.venv'))
            python = str(candidate / '.venv/bin/python')
            run(python, '-m', 'pip', 'install', '-r', 'requirements.txt', cwd=candidate)
            run(python, '-m', 'unittest', 'discover', '-s', 'tests', cwd=candidate)
            # Recheck after validation in case someone edited the live checkout.
            if (run('git', 'status', '--porcelain', capture=True).stdout.strip()
                    or run('git', 'rev-parse', 'HEAD', capture=True).stdout.strip() != current):
                print('Update skipped: checkout changed during validation.', flush=True)
                return
            # Install verified dependencies before changing the running source.
            run(str(ROOT / '.venv/bin/python'), '-m', 'pip', 'install', '-r',
                str(candidate / 'requirements.txt'))
            run('git', 'merge', '--ff-only', target)
            run('launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/local.echo.bot')
            print('Updated and restarted Echo.', flush=True)
        finally:
            run('git', 'worktree', 'remove', '--force', str(candidate))


if __name__ == '__main__':
    lock_path = Path.home() / 'Library/Logs/Echo/update.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        try:
            update()
        except (subprocess.SubprocessError, OSError) as exc:
            print(f'Update failed ({type(exc).__name__}); inspect updater log.', flush=True)
            sys.exit(1)
