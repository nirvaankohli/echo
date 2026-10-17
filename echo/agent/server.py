"""Authenticated loopback inbox and a single event-driven agent worker."""
import json
import logging
import secrets
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from flask import Flask, jsonify, request

from echo.agent.processor import process_message
from echo.runtime import deployment_log
from echo.runtime.config import background, settings, single_instance, state_dir
from echo.runtime.queue import Queue

log = logging.getLogger(__name__)


def create_app(config, queue, wake, coordinator=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024
    if len(config.get('token', '')) < 32:
        raise ValueError('Run setup to generate a pairing token')

    @app.before_request
    def authenticate():
        if not secrets.compare_digest(request.headers.get('Authorization', '').encode(),
                                      ('Bearer ' + config['token']).encode()):
            return jsonify(error='Unauthorized'), 401

    @app.get('/v1/health')
    def health():
        return jsonify(protocol=1, role='agent', queue=queue.counts(),
                       capabilities=['tasks-v2'] if coordinator else [],
                       tasks=coordinator.store.summary() if coordinator else {})

    @app.post('/v1/messages')
    def receive():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'id', 'sender', 'text'}:
            return jsonify(error='Expected id, sender, text'), 400
        if not all(isinstance(data[k], str) and data[k].strip() for k in data):
            return jsonify(error='Invalid message'), 400
        if len(data['id']) > 128 or len(data['text']) > 16000:
            return jsonify(error='Message too large'), 400
        if data['sender'].casefold() != config['owner'].casefold():
            return jsonify(error='Sender is not the configured owner'), 403
        try:
            row = queue.put(data['id'], {'sender': data['sender'], 'text': data['text']})
        except ValueError:
            return jsonify(error='Conflicting message ID'), 409
        wake.set()
        return jsonify(id=row['id'], status=row['status'], result=row['result'], error=row['error'])

    if coordinator:
        @app.post('/v2/messages')
        def receive_task():
            data = request.get_json(silent=True)
            if (not isinstance(data, dict) or set(data) != {'id', 'sender', 'text'}
                    or not all(isinstance(value, str) and value.strip() for value in data.values())
                    or len(data['id']) > 128 or len(data['text']) > 16000):
                return jsonify(error='Invalid message'), 400
            if data['sender'].casefold() != config['owner'].casefold():
                return jsonify(error='Sender is not the configured owner'), 403
            # A legacy request keeps its original delivery contract during rolling upgrades.
            if queue.get(data['id']):
                return jsonify(error='Use legacy message endpoint'), 409
            try:
                row = coordinator.receive(data['id'], data['sender'], data['text'])
            except ValueError:
                return jsonify(error='Conflicting message ID'), 409
            wake.set()
            return jsonify(id=row['id'], status=row['status'], accepted=True)

        @app.get('/v2/outbox')
        def outbox():
            return jsonify(notifications=coordinator.store.outbox())

        @app.post('/v2/outbox/ack')
        def acknowledge():
            data = request.get_json(silent=True)
            if (not isinstance(data, dict) or set(data) != {'id', 'status'}
                    or not isinstance(data['id'], str) or len(data['id']) > 300
                    or data['status'] not in ('sent', 'uncertain')):
                return jsonify(error='Invalid acknowledgement'), 400
            coordinator.store.acknowledge(data['id'], data['status'])
            return jsonify(ok=True)

        @app.post('/v2/events/codex')
        def codex_event():
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not isinstance(data.get('job_id'), str) or len(data['job_id']) > 64:
                return jsonify(error='Invalid event'), 400
            # Callback only wakes a tracked job; query the MCP for the authoritative result.
            with coordinator.store.db() as db:
                db.execute("UPDATE jobs SET due=0 WHERE job_id=? AND status='running'", (data['job_id'],))
            return jsonify(ok=True)

    return app


def process_one(queue, row, budget_store=None):
    queue.update(row['id'], 'processing')
    try:
        data = json.loads(row['payload'])
        options = {'allow_tools': True}
        if budget_store is not None:
            from echo.agent.budget import Budget
            options['budget'] = Budget(budget_store, 'legacy:' + row['id'])
        answer = process_message(data['text'], data['sender'], **options)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Empty agent response')
        queue.update(row['id'], 'completed', result=answer)
        log.info('Agent completed message %s', row['id'])
    except Exception as exc:
        # A tool may already have acted; a failure never causes automatic re-execution.
        queue.update(row['id'], 'failed', error=type(exc).__name__)
        log.error('Agent failed message %s (%s)', row['id'], type(exc).__name__)


def worker(queue, wake, stop, budget_store=None):
    while not stop.is_set():
        wake.clear()
        pending = queue.pending()
        if not pending:
            wake.wait(30)
        for row in pending:
            if stop.is_set():
                return
            process_one(queue, row, budget_store)


class LocalServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass  # No request contents or authentication headers in access logs.


def run():
    config = settings()
    if config['role'] != 'agent':
        raise ValueError('This login is not configured as the agent')
    import os
    os.environ['ECHO_TOOL_OWNER'] = config['owner']
    os.environ['ECHO_MCP_ENABLED'] = '1'
    with single_instance('agent'):
        queue = Queue(state_dir() / 'agent.sqlite3')
        queue.recover('processing')
        from echo.agent.tasks import Tasks
        from echo.agent.memory import Memory
        from echo.agent.coordinator import Coordinator
        tasks = Tasks(state_dir() / 'tasks.sqlite3')
        tasks.recover_tasks()
        coordinator = Coordinator(tasks, Memory(state_dir() / 'memory.sqlite3'))
        wake, stop = threading.Event(), threading.Event()
        app = create_app(config, queue, wake, coordinator)
        with make_server('127.0.0.1', config['port'], app, server_class=LocalServer,
                         handler_class=QuietHandler) as server:
            background(worker, queue, wake, stop, tasks)
            background(coordinator.run, wake, stop)
            background(coordinator.monitor, wake, stop)
            deployment_log.startup(deployment_log.git('rev-parse', 'HEAD'), role='agent')
            log.info('Agent ready on loopback port %s; no model calls while idle', config['port'])
            try:
                server.serve_forever()
            finally:
                stop.set()
                wake.set()
