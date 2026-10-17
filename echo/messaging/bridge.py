"""Messages-only process. Its durable outbox talks to the other login's agent."""
import json
import logging
import os
import shutil
import subprocess
import threading
import time

import requests

from echo.messaging.imsg_transport import parse_message, send_reply, watch_messages
from echo.runtime.config import background, settings, single_instance, state_dir
from echo.runtime.queue import Queue
from echo.runtime.run_echo import select_chat

log = logging.getLogger(__name__)


def accept(queue, event, chat_id, owner, wake):
    row_id = event.get('id')
    if type(row_id) is not int or row_id <= 0:
        raise ValueError('imsg event has no durable row ID')
    parsed = parse_message(event, chat_id)
    if (parsed and parsed[1].casefold() == owner.casefold()
            and event.get('is_group') is not True and ';+;' not in event['chat_guid']):
        message_id = event.get('guid')
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError('imsg event has no message GUID')
        text, sender, destination = parsed
        if len(text) > 16000:
            log.warning('Message exceeds the 16000 character limit; skipped')
        else:
            queue.put(message_id, dict(text=text, sender=sender, destination=destination),
                      cursor=(str(chat_id), row_id))
            wake.set()
            return
    queue.cursor(str(chat_id), row_id)


def relay_one(queue, row, session, config, executable):
    payload = json.loads(row['payload'])
    try:
        response = session.post(f"http://127.0.0.1:{config['port']}/v1/messages",
                                json=dict(id=row['id'], sender=payload['sender'], text=payload['text']),
                                headers={'Authorization': 'Bearer ' + config['token']}, timeout=(2, 10))
        if response.status_code in (400, 401, 403, 409, 413):
            queue.update(row['id'], 'failed', error=f'Agent rejected request: HTTP {response.status_code}')
            return
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get('id') != row['id']:
            raise ValueError('Mismatched agent response')
        if result['status'] == 'completed':
            answer = result.get('result')
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError('Invalid agent response')
            # Commit before sending: a crash/timeout here has unknown delivery, never auto-retry.
            queue.update(row['id'], 'sending')
            try:
                send_reply(answer, payload['destination'], executable)
            except Exception as exc:
                queue.update(row['id'], 'uncertain', error=type(exc).__name__)
                log.error('Message %s has uncertain delivery; not retried', row['id'])
            else:
                queue.update(row['id'], 'sent')
        elif result['status'] in ('failed', 'uncertain'):
            queue.update(row['id'], result['status'], error=result.get('error'))
        elif result['status'] in ('queued', 'processing'):
            queue.update(row['id'], 'waiting', delay=2)
        else:
            raise ValueError('Unknown agent state')
        return True
    except (requests.RequestException, ValueError, KeyError) as exc:
        # Retrying this ID cannot re-run a stored agent request.
        queue.update(row['id'], 'waiting', error=type(exc).__name__, delay=15)
        return False


def dispatcher(queue, config, executable, wake, stop):
    deliveries = Queue(state_dir() / 'deliveries.sqlite3')
    deliveries.recover('sending')
    with requests.Session() as session:
        session.trust_env = False
        modern, checked = False, 0
        while not stop.is_set():
            wake.clear()
            offline = False
            try:
                if time.monotonic() - checked > 60 or not checked:
                    health = session.get(f"http://127.0.0.1:{config['port']}/v1/health",
                        headers={'Authorization': 'Bearer ' + config['token']}, timeout=(2, 10))
                    health.raise_for_status()
                    modern = 'tasks-v2' in health.json().get('capabilities', [])
                    checked = time.monotonic()
                for row in queue.pending(('queued', 'waiting', 'v2_pending'))[:1]:
                    if stop.is_set():
                        return
                    if row['status'] == 'v2_pending' or (modern and row['status'] == 'queued'):
                        relay_task(queue, row, session, config)
                    else:
                        offline = relay_one(queue, row, session, config, executable) is False
                if modern:
                    deliver_notifications(deliveries, session, config, executable)
            except (requests.RequestException, ValueError, KeyError) as exc:
                log.warning('Bridge connection deferred (%s)', type(exc).__name__)
                offline = True
            counts = queue.counts()
            wake.wait(15 if offline else (2 if counts.get('queued', 0) + counts.get('waiting', 0) else 5))


def run():
    config = settings()
    if config['role'] != 'bridge':
        raise ValueError('This login is not configured as the Messages bridge')
    executable = shutil.which('imsg') or '/opt/homebrew/bin/imsg'
    with single_instance('bridge'):
        queue = Queue(state_dir() / 'bridge.sqlite3')
        queue.recover('sending')
        chat_id = select_chat(executable, config['owner'], False)
        cursor = queue.cursor(str(chat_id))
        if cursor is None:
            # First installation begins now; subsequent starts resume from the durable cursor.
            history = subprocess.run([executable, 'history', '--chat-id', str(chat_id),
                                      '--limit', '1', '--json'], capture_output=True, text=True,
                                     check=True, timeout=20)
            rows = [json.loads(line) for line in history.stdout.splitlines() if line.strip()]
            cursor = max((row['id'] for row in rows), default=0)
            queue.cursor(str(chat_id), cursor)
        wake, stop = threading.Event(), threading.Event()
        awake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())])
        background(dispatcher, queue, config, executable, wake, stop)
        try:
            watch_messages(chat_id, send=True, executable=executable, since_rowid=cursor,
                           on_message=lambda event: accept(queue, event, chat_id, config['owner'], wake))
        finally:
            stop.set()
            wake.set()
            awake.terminate()
            awake.wait(timeout=5)


def relay_task(queue, row, session, config):
    payload = json.loads(row['payload'])
    # Pin protocol before the request. A lost response must never fall back to duplicate legacy work.
    queue.update(row['id'], 'v2_pending', delay=15)
    response = session.post(f"http://127.0.0.1:{config['port']}/v2/messages",
                            json=dict(id=row['id'], sender=payload['sender'], text=payload['text']),
                            headers={'Authorization': 'Bearer ' + config['token']}, timeout=(2, 10))
    if response.status_code == 409 and response.json().get('error') == 'Use legacy message endpoint':
        queue.update(row['id'], 'waiting')
        return
    if response.status_code in (400, 401, 403, 409, 413):
        queue.update(row['id'], 'failed', error=f'Agent rejected task: HTTP {response.status_code}')
        return
    response.raise_for_status()
    data = response.json()
    if data.get('id') != row['id'] or data.get('accepted') is not True:
        raise ValueError('Invalid task receipt')
    queue.update(row['id'], 'submitted')


def deliver_notifications(deliveries, session, config, executable):
    base = f"http://127.0.0.1:{config['port']}"
    headers = {'Authorization': 'Bearer ' + config['token']}
    response = session.get(base + '/v2/outbox', headers=headers, timeout=(2, 10))
    response.raise_for_status()
    notifications = response.json()['notifications']
    if not isinstance(notifications, list) or len(notifications) > 20:
        raise ValueError('Invalid notification list')
    for item in notifications:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or len(item['id']) > 300 or not isinstance(item.get('text'), str)
                or not item['text'].strip() or len(item['text']) > 12000):
            raise ValueError('Invalid notification')
        row = deliveries.put(item['id'], {'text': item['text']})
        if row['status'] == 'queued':
            deliveries.update(item['id'], 'sending')
            try:
                # Destination comes only from local pairing configuration, never remote event text.
                send_reply(item['text'], ['--to', config['owner']], executable)
            except Exception as exc:
                deliveries.update(item['id'], 'uncertain', error=type(exc).__name__)
            else:
                deliveries.update(item['id'], 'sent')
            row = deliveries.get(item['id'])
        if row['status'] in ('sent', 'uncertain'):
            ack = session.post(base + '/v2/outbox/ack', json=dict(id=item['id'], status=row['status']),
                               headers=headers, timeout=(2, 10))
            ack.raise_for_status()
