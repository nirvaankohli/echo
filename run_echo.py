"""Run Echo for one iMessage conversation; dry-run unless --send is supplied."""

import argparse
import json
import logging
import shutil
import subprocess

from messaging.imsg_transport import watch_messages


class ChatSelectionError(Exception):
    """A safe, user-facing explanation for a failed conversation selection."""


def select_chat(executable: str, recipient: str | None, self_chat: bool) -> int | None:
    """Resolve an exact direct recipient, or let the user pick a conversation."""
    result = subprocess.run([executable, 'chats', '--limit', '1000' if recipient else '30', '--json'],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ChatSelectionError('Messages access failed. Check Full Disk Access for the launcher.')
    chats = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    chats = [chat for chat in chats if chat.get('service') == 'iMessage'
             and type(chat.get('id')) is int and chat['id'] > 0]
    if recipient or self_chat:
        chats = [chat for chat in chats if not chat.get('is_group') and ';+;' not in chat.get('guid', '')]
    if recipient:
        matches = [chat for chat in chats
                   if str(chat.get('identifier', '')).casefold() == recipient.casefold()]
        if len(matches) != 1:
            raise ChatSelectionError('No unique direct iMessage chat. Text that address first, or omit --to to pick one.')
        return matches[0]['id']
    if not chats:
        raise ChatSelectionError('No iMessage chats found. Create a conversation in Messages first.')
    print('Choose your OWN conversation for /echo commands.' if self_chat
          else 'Choose one conversation; only incoming messages trigger replies.')
    for index, chat in enumerate(chats, 1):
        label = str(chat.get('name') or chat.get('identifier') or 'Unnamed conversation')
        print(f"{index}. {''.join(c for c in label if c.isprintable())}")
    selection = input('Conversation number (Enter to cancel): ').strip()
    if not selection:
        return None
    if not selection.isdecimal() or not 1 <= int(selection) <= len(chats):
        raise ChatSelectionError('Invalid selection; nothing started.')
    return chats[int(selection) - 1]['id']


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--to', help='Exact direct-chat phone number or email')
    target.add_argument('--chat-id', type=int, help='Use a known positive chat ID')
    parser.add_argument('--send', action='store_true', help='Enable actual iMessage replies')
    parser.add_argument('--self-chat', action='store_true', help='Process only outgoing /echo commands')
    args = parser.parse_args()
    if args.chat_id is not None and args.chat_id <= 0:
        parser.error('--chat-id must be positive')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    executable = shutil.which('imsg') or '/opt/homebrew/bin/imsg'
    try:
        chat_id = args.chat_id or select_chat(executable, args.to, args.self_chat)
        if chat_id is not None:
            print('Echo: ' + ('replies enabled' if args.send else 'dry run (OpenAI is still called)'))
            watch_messages(chat_id, send=args.send, executable=executable, self_chat=args.self_chat)
    except (KeyboardInterrupt, EOFError):
        print('Stopped.')
    except ChatSelectionError as exc:
        logging.error('%s', exc)
        return 1
    except Exception as exc:
        # Raw subprocess/API errors can contain private data.
        logging.error('Stopped (%s). Check Messages, API configuration, and launcher permissions.', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
