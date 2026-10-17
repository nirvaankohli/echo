"""Useful status without printing messages, keys, or pairing tokens."""
import json
import os
from pathlib import Path
import subprocess
import requests

from echo.runtime.config import settings, state_dir
from echo.runtime.queue import Queue


def show():
    config = settings()
    print(f"This login runs: {config['role']}")
    for label in ('local.echo.bot', 'local.echo.update', 'local.echo.codex'):
        result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'],
                                capture_output=True, text=True, timeout=10)
        lines = [line.strip() for line in result.stdout.splitlines()
                 if line.strip().startswith(('state =', 'pid =', 'last exit code ='))]
        print(f'{label}: ' + (', '.join(lines) if result.returncode == 0 else 'not installed'))
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(f"http://127.0.0.1:{config['port']}/v1/health",
                                   headers={'Authorization': 'Bearer ' + config['token']}, timeout=3)
            response.raise_for_status()
            print('Agent reachable; legacy message counts:', response.json()['queue'])
            print('Tasks and notifications:', response.json().get('tasks', 'Agent update required'))
    except (requests.RequestException, ValueError, KeyError):
        print('Agent unreachable or pairing rejected. Pending messages will wait.')
    path = state_dir() / 'bridge.sqlite3'
    if path.exists():
        print('Bridge message counts:', Queue(path).counts())
    delivery = state_dir() / 'deliveries.sqlite3'
    if delivery.exists():
        print('Notification delivery counts:', Queue(delivery).counts())
    mcp = state_dir() / 'mcp.json'
    if mcp.exists():
        enabled = [name for name, value in json.loads(mcp.read_text())['servers'].items()
                   if value.get('enabled', True)]
        print('Configured MCP servers (not a connection test):', ', '.join(enabled) or 'none enabled')
    print(f'Logs: {Path.home() / "Library/Logs/Echo"}')
    acknowledgement = Path.home() / 'Library/Logs/Echo/started.json'
    if acknowledgement.exists():
        data = json.loads(acknowledgement.read_text())
        print('Last acknowledged revision:', data['commit'][:12], 'at', data['time'])
    print('failed/uncertain messages require inspection; they are not automatically replayed.')
