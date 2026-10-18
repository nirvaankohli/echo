"""Credential lifetime, OAuth callback binding, and agent discovery integration."""
import asyncio
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from mcp.shared.auth import OAuthToken
from echo.connections.google import GoogleClient, token
from echo.connections.oauth import ConnectionManager, TokenStorage
from echo.connections.store import Connections
from echo.tools.client import configured_servers, discover


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'ECHO_STATE_DIR':str(self.root), 'ECHO_MCP_CONFIG':str(self.root/'mcp.json')})
        env.start(); self.addCleanup(env.stop)
        self.store = Connections()
        self.manager = ConnectionManager(self.store, 'http://127.0.0.1:8877')

    def google(self):
        self.store.secret('google:client', {'client_id':'client.apps.googleusercontent.com','client_secret':'private'})
        return self.store.add('gmail','Personal Gmail','','google')

    def test_google_pkce_callback_state_session_and_scope_validation(self):
        id = self.google()
        key = self.manager.begin(id,'browser-session')
        flow = self.manager.flows[key]
        args = parse_qs(urlsplit(flow['url']).query)
        self.assertEqual(args['code_challenge_method'], ['S256'])
        self.assertEqual(args['access_type'], ['offline'])
        self.assertIn('gmail.readonly', args['scope'][0])
        for session, state in [('other-browser',flow['state']), ('browser-session','wrong-state')]:
            with self.assertRaises(ValueError):
                self.manager.callback({'code':'code','state':state},session,'google')
        with patch('echo.connections.oauth.threading.Thread') as thread:
            self.manager.callback({'code':'code','state':flow['state']},'browser-session','google')
            thread.assert_called_once()
        with self.assertRaises(ValueError):
            self.manager.callback({'code':'code','state':flow['state']},'browser-session','google')
        with patch('echo.connections.oauth.request',return_value=Mock(json=lambda:{'access_token':'private','scope':'wrong.scope'})):
            self.manager._google_callback(flow)
        self.assertEqual(self.store.get(id)['status'],'error')
        self.assertIsNone(self.store.secret(id+':tokens'))

    def test_successful_google_callback_verifies_api_before_enabling(self):
        id = self.google(); key=self.manager.begin(id,'session'); flow=self.manager.flows[key]
        flow['result']={'code':'the-code'}
        response={'access_token':'secret-token','refresh_token':'refresh-secret','scope':flow['scope'],'expires_in':3600}
        with patch('echo.connections.oauth.request',return_value=Mock(json=lambda:response)) as exchange, \
             patch.object(GoogleClient,'verify') as verify:
            self.manager._google_callback(flow)
        verify.assert_called_once()
        self.assertEqual(exchange.call_args.kwargs['data']['code_verifier'],flow['verifier'])
        self.assertEqual(self.store.get(id)['status'],'connected')
        self.assertEqual(self.store.get(id)['tool_count'],2)
        self.assertTrue(self.store.get(id)['enabled'])

    def test_google_refresh_and_rejection_are_not_silent_success(self):
        id=self.google()
        self.store.secret(id+':tokens', {'access_token':'old','refresh_token':'refresh','expires_at':0}, connection=id)
        with patch('echo.connections.google.request',return_value=Mock(json=lambda:{'access_token':'new','expires_in':3600})) as refresh:
            self.assertEqual(token(self.store,id),'new')
            self.assertEqual(token(self.store,id),'new')
            refresh.assert_called_once()
        self.assertEqual(self.store.secret(id+':tokens')['refresh_token'],'refresh')
        self.store.remove(id)
        with self.assertRaises(ValueError): token(self.store,id)

    def test_html_only_email_is_readable_without_scripts_or_network_requests(self):
        client = GoogleClient(self.google(), self.store)
        payload = {'mimeType':'text/html', 'body':{'data':base64.urlsafe_b64encode(
            b'<style>hidden</style><p>Meeting &amp; lunch</p><script>bad()</script><img src="https://tracking.example">').decode()}}
        with patch.object(client, 'get', return_value=Mock(json=lambda:{'id':'email','payload':payload})) as get:
            result = client.execute('read_email', {'message_id':'email'})
        self.assertEqual(result['text'], 'Meeting & lunch')
        get.assert_called_once()

    def test_sdk_storage_preserves_absolute_expiry_and_disconnect_cannot_resurrect_tokens(self):
        id=self.store.add('linear','Linear','https://mcp.linear.app/mcp','oauth')
        storage=TokenStorage(self.store,id)
        asyncio.run(storage.set_tokens(OAuthToken(access_token='token',expires_in=20)))
        with patch('echo.connections.oauth.time.time',return_value=time.time()+30):
            self.assertEqual(asyncio.run(storage.get_tokens()).expires_in,0)
        self.store.remove(id)
        with self.assertRaises(ValueError): asyncio.run(storage.set_tokens(OAuthToken(access_token='late-token')))

    def test_remote_flow_uses_oauth_callback_then_checks_tools(self):
        id=self.store.add('linear','Linear','https://mcp.linear.app/mcp','oauth')
        async def connect(stack,id,**kwargs):
            await kwargs['redirect']('https://linear.app/oauth/authorize?state=remote-state')
            callback=await kwargs['callback']()
            self.assertEqual(callback.code,'approved-code')
            return SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[1,2,3])))
        with patch('echo.connections.oauth.remote_client', side_effect=connect):
            key=self.manager.begin(id,'browser')
            deadline=time.time()+3
            while self.manager.view(key,'browser')['status']!='authorize' and time.time()<deadline: time.sleep(.01)
            self.manager.callback({'state':'remote-state','code':'approved-code'},'browser','mcp')
            while self.manager.view(key,'browser')['status']=='checking' and time.time()<deadline: time.sleep(.01)
        self.assertEqual(self.store.get(id)['status'],'connected')
        self.assertEqual(self.store.get(id)['tool_count'],3)

    def test_pause_during_check_cannot_reenable_connection(self):
        id=self.google(); self.store.update(id,enabled=1,status='connected')
        started,finish=threading.Event(),threading.Event()
        def verify(client): started.set();finish.wait(2)
        with patch.object(GoogleClient,'verify',verify):
            key=self.manager.begin(id,'session',interactive=False)
            self.assertTrue(started.wait(2))
            self.manager.stop(id);self.store.update(id,enabled=0)
            finish.set()
            time.sleep(.05)
        self.assertFalse(self.store.get(id)['enabled'])

    def test_discovery_merges_only_verified_enabled_connections_and_carries_account_label(self):
        (self.root/'mcp.json').write_text(json.dumps({'servers':{'old':{'command':'original'}}}))
        id=self.google()
        self.assertEqual(set(configured_servers()), {'old'})
        self.store.update(id,enabled=1,status='connected')
        self.assertIn('account_'+id, configured_servers())
        async def listing():
            from contextlib import AsyncExitStack
            with patch('echo.tools.client.configured_servers', return_value=self.store.configs()):
                async with AsyncExitStack() as stack: return await discover(stack,stable=True)
        tools,routes=asyncio.run(listing())
        self.assertEqual(len(tools),2)
        self.assertIn('Personal Gmail',tools[0]['description'])
        self.assertEqual(next(iter(routes.values()))[3],'read')
        self.store.update(id,enabled=0)
        self.assertEqual(set(configured_servers()),{'old'})

    def test_native_google_adapter_restricts_provider_tools_and_escapes_path(self):
        id=self.google(); client=GoogleClient(id,self.store)
        with self.assertRaises(ValueError): asyncio.run(client.call_tool('list_events',{}))
        with patch.object(client,'get',return_value=Mock(json=lambda:{'id':'email','payload':{},'snippet':'Hello'})) as get:
            asyncio.run(client.call_tool('read_email',{'message_id':'a/b?c'}))
        self.assertTrue(get.call_args.args[0].endswith('a%2Fb%3Fc'))


if __name__=='__main__': unittest.main()
