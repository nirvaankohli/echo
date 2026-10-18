"""Open a one-use browser sign-in link without printing the pairing secret."""
import webbrowser

from echo.connections.store import Connections
from echo.runtime.config import settings


def open_dashboard():
    config = settings()
    if config['role'] != 'agent':
        raise ValueError('Open the dashboard from your main Mac login, where the agent runs.')
    token = Connections().browser_token('ticket', 120)
    webbrowser.open(f"http://127.0.0.1:{config['port']}/app/#token={token}")
    print('Opened Echo dashboard. The sign-in link expires in two minutes.')
