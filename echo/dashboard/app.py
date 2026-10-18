"""Loopback dashboard with separate browser sessions and OAuth callbacks."""
import json
import os
from pathlib import Path
import time

from flask import Blueprint, jsonify, request, make_response, send_from_directory

from echo.connections.catalog import CATALOG, BY_ID
from echo.connections.oauth import ConnectionManager, https_url
from echo.connections.store import Connections

STATIC = Path(__file__).parent / 'static'


def register(app, config, coordinator):
    store = Connections(coordinator.store.path.with_name('connections.sqlite3'))
    base = f"http://127.0.0.1:{config.get('port', 8877)}"
    manager = ConnectionManager(store, base)
    bp = Blueprint('dashboard', __name__, url_prefix='/app')
    app.extensions['echo_connections'] = store
    app.extensions['echo_connection_manager'] = manager

    def browser_session():
        return request.cookies.get('echo_dashboard', '')

    @bp.before_request
    def protect():
        if request.host != url_host(base) or request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify(error='Open the dashboard on this Mac using its launcher.'), 403
        origin = request.headers.get('Origin')
        if origin and origin != base:
            return jsonify(error='Cross-site request rejected'), 403
        if request.method not in ('GET', 'HEAD'):
            if origin != base or request.headers.get('X-Echo-Dashboard') != '1' or not request.is_json:
                return jsonify(error='Invalid dashboard request'), 403
            if not isinstance(request.get_json(silent=True), dict):
                return jsonify(error='Expected a JSON object'), 400
        if request.endpoint not in ('dashboard.index', 'dashboard.static_file', 'dashboard.login'):
            if not store.valid_token(browser_session(), 'session'):
                return jsonify(error='Open Echo Dashboard.command on your main login to sign in.'), 401

    @bp.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @bp.get('/')
    def index():
        return send_from_directory(STATIC, 'index.html')

    @bp.get('/<any(app.css,app.js):name>')
    @bp.get('/static/<path:name>')
    def static_file(name):
        return send_from_directory(STATIC, name)

    @bp.post('/session')
    def login():
        token = request.get_json().get('token')
        if not store.valid_token(token, 'ticket', consume=True):
            return jsonify(error='This sign-in link expired. Open Echo Dashboard.command again.'), 401
        response = jsonify(ok=True)
        response.set_cookie('echo_dashboard', store.browser_token('session', 8*3600), httponly=True,
                            samesite='Lax', path='/app', max_age=8*3600)
        return response

    @bp.post('/logout')
    def logout():
        store.valid_token(browser_session(), 'session', consume=True)
        response = jsonify(ok=True)
        response.delete_cookie('echo_dashboard', path='/app')
        return response

    @bp.get('/api/state')
    def state():
        path = Path(os.environ.get('ECHO_MCP_CONFIG', str(coordinator.store.path.parent/'mcp.json'))).expanduser()
        existing = json.loads(path.read_text()).get('servers', {}) if path.exists() else {}
        imported = [dict(id=name, label=name.replace('_', ' '), enabled=value.get('enabled', True),
                         status='configured', imported=True) for name, value in existing.items()]
        return jsonify(providers=CATALOG, connections=store.all(), existing=imported,
                       google_configured=bool(store.secret('google:client')), google_redirect=base+'/app/oauth/google/callback',
                       mcp_redirect=base+'/app/oauth/mcp/callback',
                       schedules=coordinator.schedules.listing(config['owner']),
                       tasks=coordinator.store.recent(limit=12), counts=coordinator.store.summary(),
                       now=time.time())

    @bp.post('/api/google-client')
    def google_client():
        data = request.get_json()
        client_id, secret = data.get('client_id'), data.get('client_secret')
        if (not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com')
                or len(client_id)>300 or not isinstance(secret, str) or not 1<=len(secret)<=4096):
            raise ValueError('Enter the client ID and secret from your Google Web application credentials.')
        store.secret('google:client', dict(client_id=client_id, client_secret=secret))
        return jsonify(configured=True)

    @bp.post('/api/connections')
    def add():
        data = request.get_json()
        provider = BY_ID.get(data.get('provider'))
        if not provider and data.get('provider')!='custom':
            raise ValueError('Unknown provider')
        provider = provider or dict(id='custom', name='Custom connection', auth='oauth', url='')
        supplied_label = data.get('label', '')
        if not isinstance(supplied_label, str):
            raise ValueError('Use an account label of 1–80 characters')
        label = supplied_label.strip() or provider['name']
        if not isinstance(label, str) or not 1<=len(label.strip())<=80:
            raise ValueError('Use a connection name of 1–80 characters')
        auth = provider['auth'] if provider['id']!='custom' else data.get('auth', 'oauth')
        if auth not in ('google', 'oauth', 'token', 'none'):
            raise ValueError('Unknown sign-in method')
        url = '' if auth=='google' else https_url(provider['url'] or data.get('url', ''))
        bearer = data.get('token', '')
        if auth=='token' and (not isinstance(bearer, str) or not 1<=len(bearer)<=8000 or '\n' in bearer or '\r' in bearer):
            raise ValueError('Enter a valid access token')
        client_id, secret = data.get('client_id', ''), data.get('client_secret', '')
        if not all(isinstance(v, str) and len(v)<=4096 for v in (client_id, secret)):
            raise ValueError('Invalid OAuth app credentials')
        if provider.get('client_required') and not (client_id and secret):
            raise ValueError('This provider requires your OAuth app client ID and secret.')
        if auth=='google' and not store.secret('google:client'):
            raise ValueError('Complete the Google app setup first')
        id = store.add(provider['id'], label.strip(), url, auth, automatic_label=not supplied_label.strip())
        if auth=='token':
            store.secret(id+':bearer', {'token': bearer}, connection=id)
        if auth=='oauth':
            redirect = base+'/app/oauth/mcp/callback'
            store.secret(id+':redirect', {'uri': redirect}, connection=id)
            if client_id:
                store.secret(id+':client', dict(client_id=client_id, client_secret=secret or None,
                    redirect_uris=[redirect], grant_types=['authorization_code', 'refresh_token'], response_types=['code'],
                    token_endpoint_auth_method='client_secret_post' if secret else 'none'), connection=id)
        flow = manager.begin(id, browser_session())
        return jsonify(id=id, flow=flow)

    @bp.post('/api/connections/<id>/<action>')
    def manage(id, action):
        store.get(id)
        if action=='rename':
            store.rename(id, request.get_json().get('label'))
            return jsonify(ok=True)
        if action=='disconnect':
            manager.stop(id)
            store.remove(id)
            return jsonify(ok=True)
        if action=='pause':
            manager.stop(id)
            store.update(id, enabled=0)
            return jsonify(ok=True)
        if action not in ('reconnect', 'check', 'resume'):
            raise ValueError('Unknown connection action')
        return jsonify(flow=manager.begin(id, browser_session(), interactive=action=='reconnect', activate=action!='check'))

    @bp.get('/api/flows/<id>')
    def flow(id):
        return jsonify(manager.view(id, browser_session()))

    @bp.get('/oauth/<kind>/callback')
    def callback(kind):
        if kind not in ('google', 'mcp'):
            raise ValueError('Unknown sign-in callback')
        manager.callback(request.args, browser_session(), kind)
        return make_response('<!doctype html><html lang="en"><meta charset="utf-8"><title>Echo connection</title>'
            '<link rel="stylesheet" href="/app/static/app.css"><main class="callback"><h1>Back to Echo.</h1>'
            '<p>Your sign-in response was received. Echo is checking the connection.</p>'
            '<a class="button primary" href="/app/">Return to your connections</a></main></html>')

    app.register_blueprint(bp)


def url_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc
