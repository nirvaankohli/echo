"""Send text to an existing BlueBubbles conversation."""

import logging
from uuid import uuid4

import requests

logger = logging.getLogger(__name__)


class SendError(RuntimeError):
    """BlueBubbles did not confirm the send succeeded."""


def send_message(response: str, conversation_id: str, *, server_url: str,
                 password: str, method: str = 'apple-script') -> None:
    try:
        result = requests.post(
            f'{server_url.rstrip("/")}/api/v1/message/text',
            params={'password': password},
            json={'chatGuid': conversation_id, 'message': response,
                  'tempGuid': str(uuid4()), 'method': method},
            timeout=(5, 70),
        )
        result.raise_for_status()
    except requests.RequestException as exc:
        # Request exception strings can include the password-bearing URL.
        status = exc.response.status_code if exc.response is not None else 'unavailable'
        logger.error('BlueBubbles send failed (%s, HTTP %s)', type(exc).__name__, status)
        raise SendError('BlueBubbles request failed; see server logs') from None

    try:
        body = result.json()
    except ValueError:
        logger.error('BlueBubbles returned invalid JSON')
        raise SendError('Invalid BlueBubbles response') from None
    if (not isinstance(body, dict) or type(body.get('status')) is not int
            or not 200 <= body['status'] < 300 or body.get('error')):
        logger.error('BlueBubbles did not confirm success')
        raise SendError('BlueBubbles did not confirm success')
    logger.info('Reply sent')
