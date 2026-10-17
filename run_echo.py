"""Select one iMessage conversation and start Echo from an authorized terminal."""

import argparse
import json
import logging
import shutil
import subprocess

from messaging.imsg_transport import watch_messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true', help='Enable actual replies (otherwise dry run)')
    parser.add_argument('--self-chat', action='store_true',
                        help='In your self-conversation, respond only to outgoing /echo commands')
    parser.add_argument('--to', help='Select the exact direct-chat phone number or email instead of prompting')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    executable = shutil.which('imsg') or '/opt/homebrew/bin/imsg'
    try:
        result = subprocess.run([executable, 'chats', '--limit', '1000' if args.to else '30', '--json'],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            print('Messages access failed. Quit and reopen Ghostty after enabling Full Disk Access, then retry.')
            return 1
        chats = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        chats = [chat for chat in chats if chat.get('service') == 'iMessage'
                 and type(chat.get('id')) is int and chat['id'] > 0]
        if not chats:
            print('No recent iMessage chats found. Open Messages and create a test conversation first.')
            return 1
        if args.to:
            matches = [chat for chat in chats
                       if not chat.get('is_group') and ';+;' not in chat.get('guid', '')
                       and str(chat.get('identifier', '')).casefold() == args.to.casefold()]
            if len(matches) != 1:
                print('No unique direct iMessage chat found for that address.')
                print('Text that exact address first, wait for it to sync to the Mac, then retry.')
                print('If multiple chats exist, omit --to and select the intended conversation.')
                return 1
            chosen = matches[0]
            print('Selected the matching direct iMessage conversation.')
        else:
            if args.self_chat:
                chats = [chat for chat in chats if not chat.get('is_group')
                         and ';+;' not in chat.get('guid', '')]
                print('\nChoose your OWN phone number or Apple Account email conversation.')
                print('Only your outgoing /echo commands will trigger replies.\n')
            else:
                print('\nChoose ONE conversation. Only incoming messages trigger replies.')
                print('Use --self-chat to text Echo from your own phone on the same account.\n')
            for index, chat in enumerate(chats, 1):
                label = chat.get('name') or chat.get('identifier') or 'Unnamed conversation'
                # Chat labels are untrusted text; remove terminal control characters.
                label = ''.join(c for c in str(label) if c.isprintable())
                print(f'{index}. {label}')
            selection = input('\nConversation number (Enter to cancel): ').strip()
            if not selection:
                return 0
            if not selection.isdecimal() or not 1 <= int(selection) <= len(chats):
                print('Invalid selection; nothing started.')
                return 1
            chosen = chats[int(selection) - 1]
        print('\nEcho is starting. Keep this terminal open and your Mac awake.')
        print('Mode: ' + ('REPLY — eligible messages get the processor response.' if args.send
                          else 'DRY RUN — no replies will be sent.'))
        print('If macOS asks, approve Ghostty controlling Messages to enable sending. Ctrl+C stops Echo.\n')
        watch_messages(chosen['id'], send=args.send, executable=executable, self_chat=args.self_chat)
    except (KeyboardInterrupt, EOFError):
        print('\nStopped.')
    except subprocess.TimeoutExpired:
        print('A command timed out. If a send was in progress, check Messages before retrying.')
        return 1
    except Exception as exc:
        print(f'Stopped ({type(exc).__name__}). Check Messages and permissions before restarting.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
