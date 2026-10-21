"""Provider sign-in, bounded browser flows, and noninteractive agent authentication."""
import asyncio
import base64
from contextlib import AsyncExitStack
import hashlib
import logging
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from echo.connections.catalog import BY_ID
from echo.connections.google import GoogleClient, request
from echo.connections.store import Connections

log = logging.getLogger(__name__)


def server_url(value, *, https_only=False):
    schemes = ('https',) if https_only else ('http', 'https')
    label = 'HTTPS' if https_only else 'HTTP or HTTPS'
    if not isinstance(value, str) or not 1 <= len(value) <= 12000 or any(c.isspace() for c in value):
        raise ValueError(f'Enter a valid {label} URL')
    parsed = urlsplit(value)
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError(f'Use an {label} server URL without credentials or a fragment')
    parsed.port  # Validate a supplied port without rewriting path or query parameters.
    return value


def https_url(value):
    return server_url(value, https_only=True)


def custom_header(name, value):
    if not isinstance(name, str) or not isinstance(value, str):
        raise ValueError('Enter a header name and value')
    if not name and not value:
        return {}
    if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name)
            or not value or len(value) > 8000 or any(ord(c) < 32 or ord(c) > 126 for c in value)):
        raise ValueError('Enter a valid header name and a single-line value')
    if name.lower() in {'authorization', 'host', 'content-length', 'transfer-encoding', 'connection',
                         'content-type', 'accept', 'mcp-session-id', 'mcp-protocol-version', 'last-event-id'}:
        raise ValueError('This header is managed by Echo; use the sign-in options for authorization')
    return {name: value}


class TokenStorage:
    def __init__(self, store, id):
        self.store, self.id = store, id

    async def get_tokens(self):
        saved = self.store.secret(self.id+':tokens')
        if saved:
            data = dict(saved['token'])
            if saved.get('expires_at') is not None:
                data['expires_in'] = max(0, int(saved['expires_at']-time.time()))
            return OAuthToken.model_validate(data)

    async def set_tokens(self, tokens):
        self.store.secret(self.id+':tokens', {'token': tokens.model_dump(mode='json'),
                          'expires_at': time.time()+tokens.expires_in if tokens.expires_in is not None else None}, connection=self.id)

    async def get_client_info(self):
        data = self.store.secret(self.id+':client')
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, info):
        self.store.secret(self.id+':client', info.model_dump(mode='json'), connection=self.id)


async def remote_client(stack, id, *, store=None, redirect=None, callback=None):
    store = store or Connections()
    row = store.get(id)
    headers, auth = dict(store.secret(id+':headers') or {}), None
    if row['auth'] == 'token':
        secret = store.secret(id+':bearer')
        if not secret:
            raise ValueError('Add an access token from the dashboard')
        headers['Authorization'] = 'Bearer '+secret['token']
    elif row['auth'] == 'oauth':
        redirect_uri = store.secret(id+':redirect')['uri']
        async def needs_login(*args):
            raise ValueError('Reconnect this account in the Echo dashboard')
        auth = OAuthClientProvider(row['url'], OAuthClientMetadata(
            client_name='Echo', redirect_uris=[redirect_uri], grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'], token_endpoint_auth_method='none'), TokenStorage(store, id),
            redirect_handler=redirect or needs_login, callback_handler=callback or needs_login)
    http = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, auth=auth, timeout=30, trust_env=False))
    return await stack.enter_async_context(Client(streamable_http_client(row['url'], http_client=http), read_timeout_seconds=30))


class ConnectionManager:
    def __init__(self, store, base_url):
        self.store, self.base_url = store, base_url
        self.flows, self.lock = {}, threading.RLock()

    def stop(self, id):
        with self.lock:
            for flow in self.flows.values():
                if flow['connection']==id:
                    flow.update(cancelled=True, status='error', error='Connection attempt cancelled.')
                    flow['event'].set()

    def begin(self, id, session, *, interactive=True, activate=True):
        row = self.store.get(id)
        with self.lock:
            self.flows = {key: value for key, value in self.flows.items() if value['expires']>time.time()}
            if any(f['connection']==id and f['status'] in ('starting', 'authorize', 'checking') for f in self.flows.values()):
                raise ValueError('A connection attempt is already in progress')
            if len(self.flows) >= 30:
                raise ValueError('Too many connection attempts. Try again in a few minutes.')
            key = secrets.token_urlsafe(24)
            flow = dict(id=key, connection=id, session=session, status='starting', url=None,
                        expires=time.time()+300, event=threading.Event(), result=None, interactive=interactive,
                        enable=1 if activate else row['enabled'])
            self.flows[key] = flow
        if row['auth']=='google' and interactive:
            client = self.store.secret('google:client')
            if not client:
                self.flows.pop(key, None)
                raise ValueError('Set up your Google OAuth app first')
            scope = 'https://www.googleapis.com/auth/'+BY_ID[row['provider']]['scope']
            verifier = secrets.token_urlsafe(48)
            flow.update(state=secrets.token_urlsafe(32), verifier=verifier, scope=scope, status='authorize')
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            flow['url'] = 'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(dict(
                client_id=client['client_id'], redirect_uri=self.base_url+'/app/oauth/google/callback',
                response_type='code', scope=scope, state=flow['state'], access_type='offline', prompt='select_account consent',
                code_challenge=challenge, code_challenge_method='S256'))
        else:
            threading.Thread(target=self._check, args=(flow,), daemon=True).start()
        return key

    def view(self, key, session):
        with self.lock:
            flow = self.flows.get(key)
            if not flow or flow['session'] != session:
                raise ValueError('Connection attempt not found')
            if flow['expires']<=time.time():
                flow.update(status='error', error='Sign-in timed out. Try connecting again.')
                flow['event'].set()
            return {k: flow.get(k) for k in ('id', 'connection', 'status', 'url', 'error')}

    def callback(self, values, session, kind):
        with self.lock:
            flow = next((f for f in self.flows.values() if f.get('state')==values.get('state')
                         and f['session']==session and f['status']=='authorize' and f['expires']>time.time()), None)
            if not flow:
                raise ValueError('Sign-in expired or does not match this browser. Try again.')
            row = self.store.get(flow['connection'])
            if (row['auth']=='google') != (kind=='google'):
                raise ValueError('Incorrect sign-in callback')
            flow['status'] = 'checking'
            if values.get('error') or not values.get('code'):
                flow.update(status='error', error='Sign-in was declined. You can connect again when ready.')
                flow['event'].set()
                return
            flow['result'] = dict(code=values['code'], state=values['state'], iss=values.get('iss'))
            if kind=='google':
                threading.Thread(target=self._google_callback, args=(flow,), daemon=True).start()
            else:
                flow['event'].set()

    def _google_callback(self, flow):
        try:
            client = self.store.secret('google:client')
            data = request('POST', 'https://oauth2.googleapis.com/token', data={**client,
                'grant_type': 'authorization_code', 'code': flow['result']['code'], 'code_verifier': flow['verifier'],
                'redirect_uri': self.base_url+'/app/oauth/google/callback'}).json()
            if not data.get('access_token') or flow['scope'] not in data.get('scope', '').split():
                raise ValueError('Google did not grant the requested access')
            data['expires_at'] = time.time()+data.get('expires_in', 3600)
            self.store.secret(flow['connection']+':tokens', data, connection=flow['connection'])
            self._check(flow)
        except Exception as exc:
            self._failed(flow, exc)

    def _failed(self, flow, exc):
        if flow.get('cancelled'):
            return
        log.warning('Account connection failed (%s)', type(exc).__name__)
        flow.update(status='error', error='Could not verify this connection. Check the provider setup and account permissions, then try again.')
        self.store.update(flow['connection'], status='error', error=flow['error'], checked=time.time())

    def _check(self, flow):
        async def check():
            row = self.store.get(flow['connection'])
            if row['auth']=='google':
                client = GoogleClient(row['id'], self.store)
                await asyncio.to_thread(client.verify)
                return len((await client.list_tools()).tools)
            async def redirect(url):
                https_url(url)
                state = parse_qs(urlsplit(url).query).get('state', [None])[0]
                if not state:
                    raise ValueError('Provider omitted OAuth state')
                with self.lock:
                    flow.update(url=url, state=state, status='authorize')
            async def callback():
                await asyncio.to_thread(flow['event'].wait, max(0, flow['expires']-time.time()))
                if not flow['result']:
                    raise ValueError('Sign-in timed out or was declined')
                return AuthorizationCodeResult(**flow['result'])
            async with AsyncExitStack() as stack:
                client = await remote_client(stack, row['id'], store=self.store,
                    redirect=redirect if flow['interactive'] else None, callback=callback if flow['interactive'] else None)
                return len((await client.list_tools()).tools)
        try:
            count = asyncio.run(check())
            with self.lock:
                if flow.get('cancelled'):
                    return
                self.store.get(flow['connection'])  # A disconnect must not resurrect the account.
                self.store.update(flow['connection'], enabled=flow['enable'], status='connected', tool_count=count, checked=time.time(), error=None)
                flow.update(status='connected', url=None)
        except Exception as exc:
            self._failed(flow, exc)
