"""Browser access boundaries and account configuration; no real provider calls."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from echo.agent.coordinator import Coordinator
from echo.agent.memory import Memory
from echo.agent.server import create_app
from echo.agent.tasks import Tasks
from echo.runtime.queue import Queue

BASE = 'http://127.0.0.1:8877'
HEADERS = {'Origin': BASE, 'X-Echo-Dashboard': '1'}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'ECHO_STATE_DIR': str(self.root), 'ECHO_MCP_CONFIG': str(self.root/'mcp.json')})
        env.start(); self.addCleanup(env.stop)
        self.tasks = Tasks(self.root/'tasks.sqlite3')
        worker = Coordinator(self.tasks, Memory(self.root/'memory.sqlite3'))
        self.app = create_app(dict(token='pairing-secret-'*4, owner='owner', port=8877),
                              Queue(self.root/'legacy.sqlite3'), threading.Event(), worker)
        self.client = self.app.test_client()
        self.store = self.app.extensions['echo_connections']
        self.manager = self.app.extensions['echo_connection_manager']

    def get(self, path='/app/api/state', **kwargs):
        return self.client.get(path, base_url=BASE, **kwargs)

    def post(self, path, data, headers=None):
        return self.client.post(path, base_url=BASE, headers=HEADERS if headers is None else headers, json=data)

    def login(self):
        token = self.store.browser_token('ticket', 120)
        result = self.post('/app/session', {'token': token})
        self.assertEqual(result.status_code, 200)
        return token, result

    def test_separate_session_one_use_ticket_and_cookie_attributes(self):
        self.assertEqual(self.get().status_code, 401)
        self.assertEqual(self.get(headers={'Authorization': 'Bearer '+'pairing-secret-'*4}).status_code, 401)
        token, result = self.login()
        self.assertIn('HttpOnly', result.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', result.headers['Set-Cookie'])
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.post('/app/session', {'token': token}).status_code, 401)
        self.assertEqual(self.get('/v1/health').status_code, 401)  # Browser session cannot access bridge API.

    def test_origin_host_and_content_type_reject_cross_site_mutations(self):
        self.login()
        for headers in ({}, {'Origin': 'https://evil.example', 'X-Echo-Dashboard': '1'}, {'Origin': BASE}):
            self.assertEqual(self.post('/app/logout', {}, headers).status_code, 403)
        self.assertEqual(self.client.get('/app/', base_url='http://evil.example:8877').status_code, 403)
        self.assertEqual(self.get(headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.post('/app/logout', []).status_code, 400)
        with self.get('/app/') as response:
            self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_expired_tickets_and_logout(self):
        expired = self.store.browser_token('ticket', -1)
        self.assertEqual(self.post('/app/session', {'token': expired}).status_code, 401)
        self.login()
        self.post('/app/logout', {})
        self.assertEqual(self.get().status_code, 401)

    def test_catalog_and_existing_connections_do_not_expose_credentials(self):
        self.login()
        (self.root/'mcp.json').write_text(json.dumps({'servers': {'echo_codex': {'url':'http://127.0.0.1:8878/mcp', 'headers': {'Authorization': 'hidden-legacy-secret'}, 'env': {'TOKEN':'hidden-env'}}}}))
        self.post('/app/api/google-client', {'client_id':'demo.apps.googleusercontent.com', 'client_secret':'hidden-google-secret'})
        id = self.store.add('github', 'Work GitHub', 'https://api.githubcopilot.com/mcp/', 'token')
        self.store.secret(id+':bearer', {'token':'hidden-access-token'}, connection=id)
        result = self.get()
        self.assertEqual(len(result.json['providers']), 15)
        self.assertEqual(result.json['existing'][0]['status'], 'configured')
        for value in ('hidden-google-secret', 'hidden-access-token', 'hidden-legacy-secret', 'hidden-env', 'pairing-secret'):
            self.assertNotIn(value, result.text)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_google_setup_required_and_signin_is_not_pretended_connected(self):
        self.login()
        self.assertEqual(self.post('/app/api/connections', {'provider':'gmail'}).status_code, 400)
        self.post('/app/api/google-client', {'client_id':'demo.apps.googleusercontent.com','client_secret':'private'})
        result = self.post('/app/api/connections', {'provider':'gmail'})
        self.assertEqual(result.status_code, 200)
        row = self.store.get(result.json['id'])
        self.assertFalse(row['enabled'])
        self.assertEqual(row['status'], 'setup')
        flow = self.get('/app/api/flows/'+result.json['flow']).json
        self.assertTrue(flow['url'].startswith('https://accounts.google.com/'))
        self.assertNotIn('private', flow['url'])

    def test_token_connection_pause_resume_disconnect_preserves_manual_configuration(self):
        self.login()
        manual = json.dumps({'servers':{'original':{'command':'original-cli'}}})
        (self.root/'mcp.json').write_text(manual)
        with patch.object(self.manager, 'begin', return_value='flow'):
            result = self.post('/app/api/connections', {'provider':'github','token':'abc-private'})
            id = result.json['id']
            self.assertEqual(self.store.secret(id+':bearer')['token'], 'abc-private')
            self.store.update(id, enabled=1,status='connected')
            self.assertEqual(self.post(f'/app/api/connections/{id}/pause', {}).status_code, 200)
            self.assertFalse(self.store.get(id)['enabled'])
            self.post(f'/app/api/connections/{id}/disconnect', {})
            self.assertIsNone(self.store.secret(id+':bearer'))
            with self.assertRaises(ValueError): self.store.get(id)
        self.assertEqual((self.root/'mcp.json').read_text(), manual)

    def test_invalid_endpoints_and_oauth_app_requirements(self):
        self.login()
        for url in ('http://example.com/mcp','https://user:pass@example.com/mcp','file:///tmp/private',None):
            self.assertEqual(self.post('/app/api/connections', {'provider':'custom','url':url}).status_code, 400)
        self.assertEqual(self.post('/app/api/connections', {'provider':'asana'}).status_code, 400)
        self.assertEqual(self.post('/app/api/connections', {'provider':'github','token':'a\nb'}).status_code, 400)
        self.assertEqual(self.store.all(), [])


if __name__=='__main__': unittest.main()
