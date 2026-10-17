"""Run Echo directly on macOS with imsg, independently of Flask/BlueBubbles."""

import argparse
import logging
import shutil
import subprocess

from messaging.imsg_transport import watch_messages


def positive_id(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError('chat ID must be positive')
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chat-id', required=True, type=positive_id,
                        help='Watch only this conversation (find it using imsg chats --json)')
    parser.add_argument('--send', action='store_true', help='Send replies; default is a dry run')
    parser.add_argument('--self-chat', action='store_true',
                        help='In your self-conversation, respond only to outgoing /echo commands')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    executable = shutil.which('imsg')
    if not executable:
        parser.error('Install imsg first: brew install steipete/tap/imsg')
    try:
        watch_messages(args.chat_id, send=args.send, executable=executable, self_chat=args.self_chat)
    except KeyboardInterrupt:
        logging.info('Stopped')
    except subprocess.TimeoutExpired:
        logging.error('Send timed out; delivery is uncertain. Check Messages before retrying.')
        return 1
    except Exception as exc:
        logging.error('Stopped (%s). Check permissions and Messages before restarting.', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
