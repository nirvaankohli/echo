"""Read-only, content-free diagnosis of the installed bot's message filter."""

import json
import logging
from pathlib import Path
import plistlib
import shutil
import subprocess
from unittest.mock import patch

from echo.messaging.imsg_transport import handle_message
from echo.runtime.run_echo import ChatSelectionError, select_chat


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    path = Path.home() / 'Library/LaunchAgents/local.echo.bot.plist'
    config = plistlib.loads(path.read_bytes())
    args = config['ProgramArguments']
    imsg = shutil.which('imsg') or '/opt/homebrew/bin/imsg'
    self_chat = '--self-chat' in args
    if args[-3:] == ['-m', 'echo', 'bridge']:
        from echo.runtime.config import settings
        chat_id = select_chat(imsg, settings()['owner'], False)
    else:
        chat_id = (int(args[args.index('--chat-id') + 1]) if '--chat-id' in args else
                   select_chat(imsg, args[args.index('--to') + 1], self_chat))
    print(f'Checking the installed bot conversation: chat ID {chat_id}', flush=True)
    result = subprocess.run([imsg, 'history', '--chat-id', str(chat_id), '--limit', '5', '--json'],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        print('History access failed in this launcher. No message contents were displayed.')
        return
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    print(f'Testing {len(events)} recent rows. NO OpenAI calls or messages will be sent.', flush=True)
    for index, event in enumerate(events, 1):
        guid = event.get('chat_guid', '')
        kind = next((prefix for prefix in ['iMessage;', 'any;', 'SMS;'] if guid.startswith(prefix)), 'other')
        direction = 'outgoing' if event.get('is_from_me') is True else 'incoming/unknown'
        print(f'Row {index}: {direction}; chat format: {kind}', flush=True)
        # Exercise the real filter without calling OpenAI or sending a reply.
        with patch('echo.agent.processor.process_message', return_value='diagnostic only'):
            accepted = handle_message(event, chat_id, send=False, self_chat=self_chat)
        print('RESULT: would process' if accepted else 'RESULT: skipped (reason above)', flush=True)
    print('\nTake a photo of these results. No API key or message text is included.')


if __name__ == '__main__':
    try:
        main()
    except ChatSelectionError as exc:
        print(f'Diagnosis stopped: {exc}')
        print('If access failed: enable Terminal in System Settings > Privacy & Security > Full Disk Access.')
        print('Quit Terminal completely, reopen it, and double-click Diagnose Echo.command again.')
        print('If no unique chat was found: the bot recipient does not resolve to one direct iMessage conversation.')
    except Exception as exc:
        print(f'Diagnosis stopped ({type(exc).__name__}). Check installation and launcher permissions.')
    input('\nPress Enter to close.')
