"""Validate webhooks and connect the processor to the sender."""

import logging

from flask import current_app, jsonify, request

from echo.agent import processor
from echo.messaging import sender

logger = logging.getLogger(__name__)


def receive_message():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('type'), str):
        return jsonify(error='Expected a JSON event object with a type'), 400
    if payload['type'] != 'new-message':
        return jsonify(status='ignored')

    data = payload.get('data')
    if not isinstance(data, dict):
        return jsonify(error='Expected a message object in data'), 400
    if data.get('isFromMe') is True:
        return jsonify(status='ignored')
    if data.get('isFromMe') is not False:
        return jsonify(error='Expected a boolean isFromMe'), 400

    text = data.get('text')
    # Ignore attachments without text and tapbacks (which can contain synthetic text).
    if data.get('associatedMessageType') or text is None:
        return jsonify(status='ignored')
    if not isinstance(text, str):
        return jsonify(error='Expected text to be a string'), 400
    if not text.strip() or not text.replace('\ufffc', '').strip():
        return jsonify(status='ignored')

    handle = data.get('handle')
    chats = data.get('chats')
    address = handle.get('address') if isinstance(handle, dict) else None
    chat = chats[0] if isinstance(chats, list) and chats else None
    conversation_id = chat.get('guid') if isinstance(chat, dict) else None
    if not isinstance(address, str) or not address.strip():
        return jsonify(error='Missing handle.address'), 400
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return jsonify(error='Missing chats[0].guid'), 400

    try:
        response = processor.process_message(text, address)
        if not isinstance(response, str):
            raise TypeError('Processor must return a string')
    except Exception as exc:
        logger.error('Processor failed (%s)', type(exc).__name__)
        return jsonify(error='Processor failed'), 500
    if not response.strip():
        return jsonify(status='ignored')

    config = current_app.config
    try:
        sender.send_message(response, conversation_id,
                            server_url=config['BLUEBUBBLES_URL'],
                            password=config['BLUEBUBBLES_PASSWORD'],
                            method=config['BLUEBUBBLES_SEND_METHOD'])
    except sender.SendError:
        return jsonify(error='Reply could not be confirmed'), 502
    return jsonify(status='replied')
