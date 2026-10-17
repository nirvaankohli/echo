"""Flask entry point for the BlueBubbles bot."""

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import Flask

from echo.messaging.receiver import receive_message


def create_app() -> Flask:
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    app = Flask(__name__)
    url = os.getenv('BLUEBUBBLES_URL', 'http://127.0.0.1:1234').rstrip('/')
    password = os.getenv('BLUEBUBBLES_PASSWORD', '')
    method = os.getenv('BLUEBUBBLES_SEND_METHOD', 'apple-script')
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('BLUEBUBBLES_URL must be an http(s) server URL')
    if not password.strip():
        raise ValueError('Set BLUEBUBBLES_PASSWORD in .env or the environment')
    if method not in {'apple-script', 'private-api'}:
        raise ValueError('BLUEBUBBLES_SEND_METHOD must be apple-script or private-api')
    app.config.update(
        BLUEBUBBLES_URL=url,
        BLUEBUBBLES_PASSWORD=password,
        BLUEBUBBLES_SEND_METHOD=method,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    app.add_url_rule('/webhook', view_func=receive_message, methods=['POST'])
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    create_app().run(host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')), debug=False)
