"""Local Messages transport using the imsg CLI; no BlueBubbles required."""

import json
import logging
import subprocess

from agent import processor

logger = logging.getLogger(__name__)


def handle_message(event: dict, chat_id: int, *, send: bool = False,
                   executable: str = 'imsg', self_chat: bool = False) -> bool:
    """Handle only incoming text in the explicitly selected iMessage chat."""
    if not isinstance(event, dict):
        return False
    if (type(event.get('chat_id')) is not int or event['chat_id'] != chat_id
            or event.get('is_from_me') is not self_chat or event.get('is_reaction')):
        return False
    guid = event.get('chat_guid')
    text, address = event.get('text'), event.get('sender')
    if not isinstance(guid, str):
        return False
    destination = ['--chat-guid', guid]
    if self_chat and guid.startswith('any;-;'):
        # macOS can use a service-neutral GUID for a direct self-conversation.
        # Resolve its exact recipient and explicitly require iMessage when sending.
        identifier = event.get('chat_identifier')
        if not isinstance(identifier, str) or not identifier or guid != f'any;-;{identifier}':
            return False
        destination = ['--to', identifier]
    elif not guid.startswith('iMessage;'):
        return False
    if self_chat:
        # Accept only outgoing commands; incoming self-chat copies are ignored.
        if ';+;' in guid or event.get('is_group') is True:
            return False
        if not isinstance(text, str) or not text.startswith('/echo '):
            return False
        text = text[len('/echo '):].strip()
        address = 'self'
    if (not isinstance(text, str) or not text.replace('\ufffc', '').strip()
            or not isinstance(address, str) or not address.strip()):
        return False

    response = processor.process_message(text, address)
    if not isinstance(response, str):
        raise TypeError('Processor must return a string')
    if not response.strip():
        return False
    if self_chat and response.startswith('/echo '):
        raise ValueError('Processor reply cannot start with /echo in self-chat mode')
    if not send:
        logger.info('Dry run: processed incoming text; reply ready (%d characters)', len(response))
        return True

    result = subprocess.run(
        [executable, 'send', *destination, '--text', response,
         '--service', 'imessage', '--no-sms-fallback', '--json'],
        capture_output=True, text=True, timeout=45, check=False,
    )
    if result.returncode:
        # CLI output may contain message content or private identifiers.
        raise RuntimeError('imsg send failed; check Messages and Automation permission. Do not retry blindly.')
    try:
        confirmation = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('imsg returned an invalid send confirmation; outcome unknown') from None
    if not isinstance(confirmation, dict) or confirmation.get('status') != 'sent':
        raise RuntimeError('imsg did not confirm sending; outcome unknown')
    logger.info('Reply submitted to Messages')
    return True


def watch_messages(chat_id: int, *, send: bool = False, executable: str = 'imsg',
                   self_chat: bool = False) -> None:
    """Watch new messages only; stop on errors without retrying uncertain sends."""
    command = [executable, 'watch', '--chat-id', str(chat_id), '--debounce', '500ms', '--json']
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as watcher:
        logger.info('Watcher started in %s mode; Ctrl+C stops it', 'send' if send else 'dry-run')
        try:
            for line in watcher.stdout:
                if not line.strip():
                    continue
                event = json.loads(line)
                handle_message(event, chat_id, send=send, executable=executable, self_chat=self_chat)
            if watcher.wait() != 0:
                raise RuntimeError('imsg watcher failed; see its diagnostic above')
        finally:
            if watcher.poll() is None:
                watcher.terminate()
                try:
                    watcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    watcher.kill()
                    watcher.wait()
