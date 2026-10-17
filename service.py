"""Generate or install Echo background services for the current macOS login."""

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

LABEL = 'local.echo.bot'


def build_plist(project: Path, home: Path, recipient: str, imsg: str) -> dict:
    return {
        'Label': LABEL,
        'ProgramArguments': [str(project / '.venv/bin/python'), '-u',
                             str(project / 'run_echo.py'), '--send', '--to', recipient],
        'WorkingDirectory': str(project),
        'EnvironmentVariables': {
            'PATH': f'{Path(imsg).parent}:/usr/bin:/bin:/usr/sbin:/sbin',
        },
        'LimitLoadToSessionType': 'Aqua',
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 30,
        'StandardOutPath': str(home / 'Library/Logs/Echo/stdout.log'),
        'StandardErrorPath': str(home / 'Library/Logs/Echo/stderr.log'),
        'Umask': 0o077,
    }


def install_service(config: dict, home: Path) -> None:
    """Replace only our named service, never create a second loaded instance."""
    directory = home / 'Library/LaunchAgents'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{config['Label']}.plist"
    path.write_bytes(plistlib.dumps(config))
    path.chmod(0o600)
    domain = f'gui/{os.getuid()}'
    target = f"{domain}/{config['Label']}"
    loaded = subprocess.run(['launchctl', 'print', target], capture_output=True, timeout=10)
    if loaded.returncode == 0:
        subprocess.run(['launchctl', 'bootout', target], check=True, timeout=20)
    subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True, timeout=20)
    print(f"Loaded {config['Label']}; check logs and test a new phone message.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install and start services in this Mac login')
    parser.add_argument('--keep-awake', action='store_true', help='Prevent idle system sleep while Echo runs')
    parser.add_argument('--auto-update', action='store_true', help='Also generate a 5-minute origin/main updater')
    parser.add_argument('--to', required=True, help='Your iPhone number or email, as shown in imsg chats')
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.getuid() == 0:
        parser.error('Run as the Echo macOS user, not root, on a Mac')
    project = Path(__file__).resolve().parent
    home = Path.home()
    if not project.is_relative_to(home):
        parser.error('First copy this project into the Echo user home folder')
    imsg = shutil.which('imsg')
    if not imsg or not (project / '.venv/bin/python').exists():
        parser.error('Install imsg and create the project .venv first')
    if args.install:
        from dotenv import dotenv_values
        key = os.environ.get('OPENAI_API_KEY') or dotenv_values(project / '.env').get('OPENAI_API_KEY')
        if not key or not key.strip():
            parser.error('Set OPENAI_API_KEY in this project .env before installing')
    logs = home / 'Library/Logs/Echo'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = project / f'{LABEL}.plist'
    bot = build_plist(project, home, args.to, imsg)
    if args.keep_awake:
        bot['ProgramArguments'] = ['/usr/bin/caffeinate', '-i', *bot['ProgramArguments']]
    output.write_bytes(plistlib.dumps(bot))
    output.chmod(0o600)
    print(f'Created {output}. Follow README activation steps in the Echo login.')
    if args.install:
        install_service(bot, home)
    if args.auto_update:
        updater = build_plist(project, home, args.to, imsg)
        updater.update({
            'Label': 'local.echo.update',
            'ProgramArguments': [str(project / '.venv/bin/python'), '-u', str(project / 'update_bot.py')],
            'StartInterval': 300,
            'StandardOutPath': str(logs / 'update.log'),
            'StandardErrorPath': str(logs / 'update.log'),
        })
        updater['EnvironmentVariables']['GIT_TERMINAL_PROMPT'] = '0'
        updater.pop('KeepAlive')
        output = project / 'local.echo.update.plist'
        output.write_bytes(plistlib.dumps(updater))
        output.chmod(0o600)
        print(f'Created {output}. Updates require a clean checkout on main.')
        if args.install:
            install_service(updater, home)


if __name__ == '__main__':
    main()
