"""Cross-login protocol tests use real local HTTP/SQLite and fake OpenAI/iMessage calls."""
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from wsgiref.simple_server import make_server

import requests
import uvicorn

from echo.agent.server import create_app, LocalServer, QuietHandler, process_one, worker
from echo.messaging.bridge import accept, relay_one
from echo.messaging.imsg_transport import watch_messages
from echo.runtime.queue import Queue
from echo.runtime.setup import export_pairing, install, service_config
from echo.tools.client import connect, configured_servers, respond
from echo.tools.mcp_server import server as mcp_server
from echo.tools.worker import Authenticated

OWNER = '+15551234567'
CONFIG = dict(role='agent', owner=OWNER, token='t' * 40, port=8877)
MESSAGE = dict(id='message-guid', sender=OWNER, text='hello')
EVENT = dict(id=20, guid='message-guid', sender=OWNER, text='hello', is_from_me=False,
             chat_id=42, chat_guid='any;-;' + OWNER, chat_identifier=OWNER)


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.agent = Queue(self.path / 'agent.sqlite3')
        self.bridge = Queue(self.path / 'bridge.sqlite3')
        self.wake = threading.Event()
        self.app = create_app(CONFIG, self.agent, self.wake)
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + CONFIG['token']}

    def test_authorization_owner_and_conflicting_id(self):
        self.assertEqual(self.client.post('/v1/messages', json=MESSAGE).status_code, 401)
        self.assertEqual(self.client.post('/v1/messages', headers=self.headers,
                                         json={**MESSAGE, 'sender': 'stranger'}).status_code, 403)
        self.assertEqual(self.agent.counts(), {})
        for _ in range(2):
            response = self.client.post('/v1/messages', json=MESSAGE, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.agent.counts(), {'queued': 1})
        self.assertEqual(self.client.post('/v1/messages', headers=self.headers,
                                         json={**MESSAGE, 'text': 'different'}).status_code, 409)
        self.assertTrue(self.wake.is_set())

    def test_completed_retries_reuse_result_without_new_work(self):
        self.client.post('/v1/messages', json=MESSAGE, headers=self.headers)
        with patch('echo.agent.server.process_message', return_value='answer') as process:
            process_one(self.agent, self.agent.pending()[0])
            for _ in range(3):
                result = self.client.post('/v1/messages', json=MESSAGE, headers=self.headers).json
                self.assertEqual(result['result'], 'answer')
                self.assertEqual(result['status'], 'completed')
            process.assert_called_once_with('hello', OWNER, allow_tools=True)
        self.assertEqual(self.agent.pending(), [])

    def test_idle_worker_never_calls_openai(self):
        stop = threading.Event()
        with patch('echo.agent.server.process_message') as process:
            thread = threading.Thread(target=worker, args=(self.agent, self.wake, stop))
            thread.start()
            time.sleep(.1)
            stop.set()
            self.wake.set()
            thread.join(2)
            process.assert_not_called()
            self.assertFalse(thread.is_alive())

    def test_crash_recovery_does_not_reexecute_or_resend(self):
        self.agent.put('job', {'text': 'task', 'sender': OWNER})
        self.agent.update('job', 'processing')
        reopened = Queue(self.path / 'agent.sqlite3')
        reopened.recover('processing')
        self.assertEqual(reopened.get('job')['status'], 'uncertain')
        self.assertEqual(reopened.pending(), [])
        accept(self.bridge, EVENT, 42, OWNER, self.wake)
        self.bridge.update(EVENT['guid'], 'sending')
        self.bridge.recover('sending')
        self.assertEqual(self.bridge.get(EVENT['guid'])['status'], 'uncertain')
        self.assertEqual(self.bridge.pending(('queued', 'waiting')), [])

    def test_owner_filter_and_cursor_are_durable(self):
        for event in (EVENT, EVENT, {**EVENT, 'id': 21, 'is_from_me': True},
                      {**EVENT, 'id': 22, 'sender': 'stranger'},
                      {**EVENT, 'id': 23, 'is_group': True}):
            accept(self.bridge, event, 42, OWNER, self.wake)
        self.assertEqual(self.bridge.counts(), {'queued': 1})
        self.assertEqual(Queue(self.path / 'bridge.sqlite3').cursor('42'), 23)

    def test_offline_agent_retains_message(self):
        accept(self.bridge, EVENT, 42, OWNER, self.wake)
        session = Mock()
        session.post.side_effect = requests.ConnectionError()
        with patch('echo.messaging.bridge.send_reply') as send:
            relay_one(self.bridge, self.bridge.get(EVENT['guid']), session, CONFIG, 'imsg')
            send.assert_not_called()
        self.assertEqual(self.bridge.get(EVENT['guid'])['status'], 'waiting')

    def test_uncertain_send_is_never_retried(self):
        accept(self.bridge, EVENT, 42, OWNER, self.wake)
        session = Mock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = dict(id=EVENT['guid'], status='completed', result='answer')
        with patch('echo.messaging.bridge.send_reply', side_effect=RuntimeError('unknown')) as send:
            relay_one(self.bridge, self.bridge.get(EVENT['guid']), session, CONFIG, 'imsg')
            self.assertEqual(self.bridge.get(EVENT['guid'])['status'], 'uncertain')
            self.assertEqual(self.bridge.pending(('queued', 'waiting')), [])
            send.assert_called_once()

    def test_full_loopback_round_trip_once(self):
        with make_server('127.0.0.1', 0, self.app, server_class=LocalServer, handler_class=QuietHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            config = {**CONFIG, 'port': server.server_port}
            try:
                accept(self.bridge, EVENT, 42, OWNER, self.wake)
                with requests.Session() as session, patch('echo.messaging.bridge.send_reply') as send:
                    session.trust_env = False
                    relay_one(self.bridge, self.bridge.get(EVENT['guid']), session, config, 'imsg')
                    with patch('echo.agent.server.process_message', return_value='agent answer'):
                        process_one(self.agent, self.agent.pending()[0])
                    relay_one(self.bridge, self.bridge.get(EVENT['guid']), session, config, 'imsg')
                    send.assert_called_once_with('agent answer', ['--to', OWNER], 'imsg')
                self.assertEqual(self.bridge.get(EVENT['guid'])['status'], 'sent')
            finally:
                server.shutdown()
                thread.join(2)

    def test_watcher_resumes_after_durable_row(self):
        with patch('echo.messaging.imsg_transport.subprocess.Popen') as popen:
            child = popen.return_value.__enter__.return_value
            child.stdout = []
            child.wait.return_value = 0
            child.poll.return_value = 0
            watch_messages(42, since_rowid=19, on_message=Mock())
            self.assertEqual(popen.call_args.args[0][-2:], ['--since-rowid', '19'])

    def test_failed_model_is_terminal(self):
        self.agent.put('job', {'text': 'task', 'sender': OWNER})
        with patch('echo.agent.server.process_message', side_effect=RuntimeError('private details')):
            process_one(self.agent, self.agent.pending()[0])
        self.assertEqual(self.agent.get('job')['status'], 'failed')
        self.assertEqual(self.agent.get('job')['error'], 'RuntimeError')
        self.assertEqual(self.agent.pending(), [])

    def test_new_services_keep_secrets_out_of_plist(self):
        with patch('echo.runtime.setup.Path.home', return_value=self.path):
            for role in ('agent', 'bridge', 'codex'):
                config = service_config(role)
                self.assertEqual(config['ProgramArguments'][-3:], ['-m', 'echo', role])
                self.assertTrue(config['KeepAlive'])
                self.assertNotIn('token', str(config).lower())
                self.assertNotIn('OPENAI_API_KEY', config['EnvironmentVariables'])


class RemoteMCPTests(unittest.TestCase):
    def test_missing_optional_server_does_not_break_chat(self):
        from types import SimpleNamespace
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(status='completed', output=[], output_text='Hello')
        with patch('echo.tools.client.configured_servers', return_value={
                'missing': {'command': '/nonexistent/echo-test-server'}}):
            self.assertEqual(asyncio.run(respond(client, 'hi', '', 'test')), 'Hello')
        self.assertEqual(client.responses.create.call_args.kwargs['tools'], [])

    def test_authenticated_http_mcp_can_be_used_across_logins(self):
        app = Authenticated(mcp_server.streamable_http_app(stateless_http=True, json_response=True), CONFIG['token'])
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(server.started)
            with requests.Session() as session:
                session.trust_env = False
                self.assertEqual(session.get(f'http://127.0.0.1:{port}/mcp', timeout=3).status_code, 401)
            async def check():
                async with AsyncExitStack() as stack:
                    with patch.dict('os.environ', {'TEST_MCP_AUTH': 'Bearer ' + CONFIG['token']}):
                        client = await connect(stack, {'url': f'http://127.0.0.1:{port}/mcp',
                                                       'header_env': {'Authorization': 'TEST_MCP_AUTH'}})
                        result = await client.list_tools()
                        self.assertIn('codex_start', {tool.name for tool in result.tools})
            asyncio.run(check())
        finally:
            server.should_exit = True
            thread.join(5)
            listener.close()

    def test_disabled_servers_and_unsupported_remote_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'mcp.json'
            config.write_text(json.dumps({'servers': {'off': {'enabled': False}, 'on': {'command': 'example'}}}))
            with patch.dict('os.environ', {'ECHO_MCP_CONFIG': str(config), 'ECHO_STATE_DIR': folder}):
                self.assertEqual(list(configured_servers()), ['on'])
        async def check():
            async with AsyncExitStack() as stack:
                with self.assertRaises(ValueError):
                    await connect(stack, {'url': 'ftp://external.example/mcp'})
        asyncio.run(check())


class PairingTests(unittest.TestCase):
    def test_installer_requires_a_fresh_startup_acknowledgement(self):
        with patch('echo.runtime.setup.install_service'), \
             patch('echo.runtime.setup.service_config', return_value={}), \
             patch('echo.runtime.setup.deployment_log.git', return_value='new'), \
             patch('echo.runtime.setup.deployment_log.wait_for_start', side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, 'did not confirm startup'):
                install('agent')

    def test_pairing_uses_private_file_and_only_target_user_acl(self):
        original_mkstemp = tempfile.mkstemp
        with tempfile.TemporaryDirectory() as directory:
            def temporary(**kwargs):
                return original_mkstemp(dir=directory)
            with patch('echo.runtime.setup.tempfile.mkstemp', side_effect=temporary), \
                 patch('echo.runtime.setup.getpass.getuser', return_value='main'), \
                 patch('echo.runtime.setup.subprocess.run') as run:
                path = export_pairing(CONFIG, 'echo')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(json.loads(path.read_text())['bridge_user'], 'echo')
                self.assertEqual(run.call_args.args[0], ['chmod', '+a', 'user:echo allow read', str(path)])

    def test_no_public_fallback_when_acl_fails(self):
        original_mkstemp = tempfile.mkstemp
        with tempfile.TemporaryDirectory() as directory:
            def temporary(**kwargs):
                return original_mkstemp(dir=directory)
            with patch('echo.runtime.setup.tempfile.mkstemp', side_effect=temporary), \
                 patch('echo.runtime.setup.getpass.getuser', return_value='main'), \
                 patch('echo.runtime.setup.subprocess.run', side_effect=[Mock(), OSError('ACL unavailable')]):
                with self.assertRaises(OSError):
                    export_pairing(CONFIG, 'echo')
            self.assertEqual(list(Path(directory).iterdir()), [])
