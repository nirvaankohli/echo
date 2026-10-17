"""One-time setup inside Echo's macOS login; never changes another user's settings."""

import os
from pathlib import Path
import shutil
import subprocess

from dotenv import set_key

from agent.codex_jobs import ROOT


def main():
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('Install Codex CLI in this login, then run this setup again.')
    if subprocess.run([executable, 'login', 'status'], capture_output=True).returncode:
        print('Sign into Codex in this Echo login. Setup continues after sign-in.')
        subprocess.run([executable, 'login'], check=True)
    folder = input('Folder Codex may work in [~/EchoWorkspace]: ').strip() or '~/EchoWorkspace'
    workspace = Path(folder).expanduser().resolve()
    if workspace == ROOT or ROOT.is_relative_to(workspace) or workspace.is_relative_to(ROOT):
        raise ValueError('Choose a separate project folder outside Echo.')
    owner = input('Your iMessage sender number/email [+19089691989]: ').strip() or '+19089691989'
    workspace.mkdir(parents=True, exist_ok=True)
    env = ROOT / '.env'
    env.touch(mode=0o600, exist_ok=True)
    env.chmod(0o600)
    for key, value in [('ECHO_CODEX_WORKSPACE', str(workspace)), ('ECHO_TOOL_OWNER', owner),
                       ('ECHO_MCP_ENABLED', '1')]:
        set_key(str(env), key, value)
    result = subprocess.run(['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/local.echo.bot'],
                            capture_output=True)
    print('Codex tools enabled. ' + ('Echo restarted.' if result.returncode == 0
                                    else 'Start Echo with your usual server command.'))
    print('Text Echo: Ask Codex to create a hello.txt file saying hello.')
    print('Then text: What is the status of my latest Codex job?')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Setup failed: {exc}')
    finally:
        input('Press Enter to close.')
