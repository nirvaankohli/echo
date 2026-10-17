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


def create_app(config, queue, wake):
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
        return jsonify(protocol=1, role='agent', queue=queue.counts())

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

    return app


def process_one(queue, row):
    queue.update(row['id'], 'processing')
    try:
        data = json.loads(row['payload'])
        answer = process_message(data['text'], data['sender'], allow_tools=True)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Empty agent response')
        queue.update(row['id'], 'completed', result=answer)
        log.info('Agent completed message %s', row['id'])
    except Exception as exc:
        # A tool may already have acted; a failure never causes automatic re-execution.
        queue.update(row['id'], 'failed', error=type(exc).__name__)
        log.error('Agent failed message %s (%s)', row['id'], type(exc).__name__)


def worker(queue, wake, stop):
    while not stop.is_set():
        wake.clear()
        pending = queue.pending()
        if not pending:
            wake.wait(30)
        for row in pending:
            if stop.is_set():
                return
            process_one(queue, row)


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
        wake, stop = threading.Event(), threading.Event()
        app = create_app(config, queue, wake)
        with make_server('127.0.0.1', config['port'], app, server_class=LocalServer,
                         handler_class=QuietHandler) as server:
            background(worker, queue, wake, stop)
            deployment_log.startup(deployment_log.git('rev-parse', 'HEAD'), role='agent')
            log.info('Agent ready on loopback port %s; no model calls while idle', config['port'])
            try:
                server.serve_forever()
            finally:
                stop.set()
                wake.set()
