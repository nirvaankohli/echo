"""Optional authenticated Codex MCP worker in the Echo macOS login."""
import secrets

import uvicorn
from echo.runtime.config import settings, single_instance
from echo.tools.mcp_server import server


class Authenticated:
    def __init__(self, app, token):
        if len(token) < 32:
            raise ValueError('Pair this login before starting its Codex worker')
        self.app, self.expected = app, ('Bearer ' + token).encode()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            supplied = dict(scope['headers']).get(b'authorization', b'')
            if not secrets.compare_digest(supplied, self.expected):
                await send({'type': 'http.response.start', 'status': 401, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'Unauthorized'})
                return
        await self.app(scope, receive, send)


def run():
    config = settings()
    with single_instance('codex'):
        app = server.streamable_http_app(stateless_http=True, json_response=True)
        uvicorn.run(Authenticated(app, config['token']), host='127.0.0.1',
                    port=config.get('codex_port', 8878), access_log=False)
