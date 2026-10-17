"""Interactive repair for Echo's background Messages permissions."""

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

from echo.runtime.deployment_log import git, wait_for_start
from echo.runtime.service import install_service

ROOT = Path(__file__).resolve().parents[2]


def direct_launcher(config):
    """Make Python the permission-bearing launcher; keep sleep prevention inside it."""
    config = {**config, 'EnvironmentVariables': dict(config.get('EnvironmentVariables', {}))}
    command = config['ProgramArguments']
    if command[:2] == ['/usr/bin/caffeinate', '-i']:
        config['ProgramArguments'] = command[2:]
        config['EnvironmentVariables']['ECHO_KEEP_AWAKE'] = '1'
    return config


def main():
    if sys.platform != 'darwin' or os.getuid() == 0:
        raise RuntimeError('Open this tool in the Echo Mac login, without sudo.')
    path = Path.home() / 'Library/LaunchAgents/local.echo.bot.plist'
    if not path.exists():
        raise RuntimeError('Echo is not installed in this Mac login yet. Follow the README setup first.')
    config = direct_launcher(plistlib.loads(path.read_bytes()))
    if (str(ROOT / 'run_echo.py') not in config['ProgramArguments']
            and not (config.get('WorkingDirectory') == str(ROOT)
                     and config['ProgramArguments'][-3:] == ['-m', 'echo', 'bridge'])):
        raise RuntimeError('Open Repair Echo.command from the same project folder as the installed bot.')
    python = Path(config['ProgramArguments'][0]).resolve()
    print('\nEcho needs permission to read Messages when running in the background.\n')
    print('1. In Full Disk Access settings, click +.')
    print('2. Drag the selected Python executable from Finder into the file chooser.')
    print('3. Click Open, then turn its switch ON. Authenticate if macOS asks.')
    print(f'\nPython: {python}\n')
    subprocess.run(['open', '-R', str(python)], check=True)
    subprocess.run(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'], check=True)
    input('After enabling Python, return here and press Enter to restart and check Echo: ')
    started = time.time()
    install_service(config, Path.home())
    try:
        wait_for_start(git('rev-parse', 'HEAD'), started)
    except TimeoutError:
        print('\nEcho did not confirm startup. Background permission is not verified.')
        print('Its latest error is in the log now opening. If it still says Messages access failed,')
        print('add the imsg executable selected in Finder to Full Disk Access too, then rerun this tool.')
        imsg = shutil.which('imsg') or '/opt/homebrew/bin/imsg'
        subprocess.run(['open', '-R', str(Path(imsg).resolve())], check=False)
        subprocess.run(['open', '-e', config.get('StandardErrorPath',
                       str(Path.home() / 'Library/Logs/Echo/stderr.log'))], check=False)
        return
    print('\nEcho confirmed startup. Now text its email from your iPhone to test an actual reply.')
    print('If macOS asks for permission to control Messages, allow it.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'\nRepair stopped: {exc}')
    except (KeyboardInterrupt, EOFError):
        print('\nRepair cancelled.')
    input('\nPress Enter to close this window.')
