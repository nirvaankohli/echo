"""Interactive per-login setup. Pairing files are readable only by the named Echo user."""
import getpass
import json
import os
import pwd
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time

from dotenv import set_key
import requests

from echo.runtime.config import ROOT, load_env, settings, state_dir, write_json
from echo.runtime.service import install_service
from echo.runtime import deployment_log


def service_config(role, *, update=False):
    label = 'local.echo.update' if update else ('local.echo.codex' if role == 'codex' else 'local.echo.bot')
    command = 'update' if update else role
    logs = Path.home() / 'Library/Logs/Echo'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    return dict(Label=label, ProgramArguments=[str(ROOT / '.venv/bin/python'), '-u', '-m', 'echo', command],
                WorkingDirectory=str(ROOT), RunAtLoad=True, LimitLoadToSessionType='Aqua',
                EnvironmentVariables={'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                                      'GIT_TERMINAL_PROMPT': '0'},
                StandardOutPath=str(logs / f'{command}.log'),
                StandardErrorPath=str(logs / f'{command}.log'), Umask=0o077, ThrottleInterval=30,
                **({'StartInterval': 300} if update else {'KeepAlive': True}))


def install(role):
    started = time.time()
    install_service(service_config(role), Path.home())
    if role != 'codex':
        install_service(service_config(role, update=True), Path.home())
        try:
            deployment_log.wait_for_start(deployment_log.git('rev-parse', 'HEAD'), started)
        except TimeoutError:
            raise RuntimeError(f'{role} did not confirm startup. Choose Show status; '
                               'for Messages access errors choose Repair Messages access.') from None


def secret_env(name, value):
    path = state_dir() / '.env'
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    set_key(str(path), name, value)


def export_pairing(config, username):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', username) or username == getpass.getuser():
        raise ValueError('Enter the other macOS login short name, e.g. echo')
    subprocess.run(['id', '-u', username], check=True, capture_output=True)
    fd, name = tempfile.mkstemp(prefix='Echo-pairing-', suffix='.json', dir='/Users/Shared')
    path = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({**config, 'bridge_user': username, 'protocol': 1}, stream)
        subprocess.run(['chmod', '+a', f'user:{username} allow read', str(path)], check=True)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def setup_agent():
    previous = state_dir() / 'settings.json'
    old = json.loads(previous.read_text()) if previous.exists() else {}
    if old.get('role') == 'bridge':
        raise ValueError('Switch to your main Mac login to set up the agent')
    load_env()
    key = os.environ.get('OPENAI_API_KEY') or getpass.getpass('OpenAI API key (hidden): ').strip()
    if not key:
        raise ValueError('An OpenAI API key is required')
    owner = input('Your iMessage number/email [+19089691989]: ').strip() or '+19089691989'
    username = input('Echo macOS login short name [echo]: ').strip() or 'echo'
    config = dict(role='agent', owner=owner, token=old.get('token') or secrets.token_urlsafe(32),
                  port=8877, codex_port=8878)
    pairing = export_pairing(config, username)
    secret_env('OPENAI_API_KEY', key)
    secret_env('ECHO_WORKER_AUTH', 'Bearer ' + config['token'])
    write_json(state_dir() / 'settings.json', config)
    mcp = state_dir() / 'mcp.json'
    if not mcp.exists():
        write_json(mcp, {'servers': {'echo_codex': {
            'enabled': False, 'url': 'http://127.0.0.1:8878/mcp',
            'header_env': {'Authorization': 'ECHO_WORKER_AUTH'}}}})
    install('agent')
    print(f'Agent installed. Pairing saved for {username}: {pairing.name}')
    print('Now switch to the Echo login, open scripts/Setup Echo.command there, and choose Messages bridge.')


def setup_bridge():
    main_user = input('Your MAIN macOS login short username: ').strip()
    try:
        main_uid = pwd.getpwnam(main_user).pw_uid
    except KeyError:
        raise ValueError('That main Mac login was not found') from None
    if main_uid == os.getuid():
        raise ValueError('Switch to the Echo login to set up its Messages bridge')
    candidates = []
    for path in Path('/Users/Shared').glob('Echo-pairing-*.json'):
        try:
            data = json.loads(path.read_text())
            if (path.stat().st_uid == main_uid and data.get('bridge_user') == getpass.getuser()
                    and data.get('protocol') == 1):
                candidates.append((path.stat().st_mtime, data))
        except (OSError, ValueError):
            continue
    if not candidates:
        raise ValueError('No pairing for this login. Run Agent setup on your main login first.')
    data = max(candidates, key=lambda item: item[0])[1]
    if not shutil.which('imsg'):
        raise ValueError('Install imsg first: brew install steipete/tap/imsg')
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(f"http://127.0.0.1:{int(data['port'])}/v1/health",
                               headers={'Authorization': 'Bearer ' + data['token']}, timeout=5)
        response.raise_for_status()
        if response.json().get('protocol') != 1:
            raise ValueError('Agent protocol mismatch; update both checkouts')
    config = {key: data[key] for key in ('owner', 'token', 'port', 'codex_port')}
    write_json(state_dir() / 'settings.json', {**config, 'role': 'bridge'})
    install('bridge')
    print('Messages bridge installed. Send a NEW message after status reports the bridge is running.')
    print('If Messages access fails, choose Repair Messages access from this setup menu.')


def setup_codex():
    settings()  # Must be paired first.
    executable = shutil.which('codex')
    if not executable:
        raise ValueError('Install Codex CLI in this login first')
    if subprocess.run([executable, 'login', 'status'], capture_output=True).returncode:
        subprocess.run([executable, 'login'], check=True)
    workspace = Path(input('Codex project folder [~/EchoWorkspace]: ').strip() or '~/EchoWorkspace').expanduser().resolve()
    if workspace == ROOT or ROOT.is_relative_to(workspace) or workspace.is_relative_to(ROOT):
        raise ValueError('Use a separate project folder outside the running Echo checkout')
    workspace.mkdir(parents=True, exist_ok=True)
    secret_env('ECHO_CODEX_WORKSPACE', str(workspace))
    install('codex')
    print('Codex worker installed. On your main login, set echo_codex enabled to true in MCP settings.')


def restart():
    install(settings()['role'])
    target = f'gui/{os.getuid()}/local.echo.codex'
    if subprocess.run(['launchctl', 'print', target], capture_output=True).returncode == 0:
        install('codex')
    print('This login restarted successfully.')


def menu():
    if os.getuid() == 0:
        raise ValueError('Run as your normal Mac login, without sudo')
    print('1. Main login: set up agent\n2. Echo login: set up Messages bridge\n'
          '3. Echo login: set up optional Codex worker\n4. Repair Messages access\n'
          '5. Show status\n6. Main login: open MCP settings\n7. Restart this login')
    choice = input('Choose: ').strip()
    if choice == '1':
        setup_agent()
    elif choice == '2':
        setup_bridge()
    elif choice == '3':
        setup_codex()
    elif choice == '4':
        from echo.runtime.repair import main
        main()
    elif choice == '5':
        from echo.runtime.status import show
        show()
    elif choice == '6':
        subprocess.run(['open', '-e', str(state_dir() / 'mcp.json')], check=True)
    elif choice == '7':
        restart()
    else:
        raise ValueError('Choose an option from 1 to 7')
