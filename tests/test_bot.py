"""Offline tests: no BlueBubbles server or real messages required."""

import os
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from uuid import UUID

import requests

from echo.agent.processor import process_message
from echo.messaging.bluebubbles import create_app
from echo.messaging.sender import SendError, send_message

EVENT = {
    'type': 'new-message',
    'data': {'guid': 'incoming-guid', 'text': 'Hello', 'isFromMe': False,
             'handle': {'address': '+15551234567'},
             'chats': [{'guid': 'iMessage;-;+15551234567'}]},
}


class BotTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {'BLUEBUBBLES_PASSWORD': 'test-password',
                                     'BLUEBUBBLES_URL': 'http://127.0.0.1:1234',
                                     'BLUEBUBBLES_SEND_METHOD': 'apple-script'}):
            self.client = create_app().test_client()

    @patch('echo.agent.processor.load_dotenv')
    @patch.dict(os.environ, {'OPENAI_API_KEY': ''})
    def test_processor_requires_key(self, load):
        with self.assertRaises(ValueError):
            process_message('anything', 'someone')

    @patch('echo.messaging.sender.requests.post')
    @patch('echo.agent.processor.process_message', return_value='custom agent response')
    def test_full_flow_uses_processor_response_and_original_chat(self, process, post):
        post.return_value.json.return_value = {'status': 200, 'data': {}}
        response = self.client.post('/webhook', json=EVENT)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'replied')
        process.assert_called_once_with('Hello', '+15551234567')
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'http://127.0.0.1:1234/api/v1/message/text')
        self.assertEqual(kwargs['params'], {'password': 'test-password'})
        self.assertEqual(kwargs['json']['message'], 'custom agent response')
        self.assertEqual(kwargs['json']['chatGuid'], EVENT['data']['chats'][0]['guid'])
        self.assertEqual(kwargs['json']['method'], 'apple-script')
        UUID(kwargs['json']['tempGuid'])
        self.assertEqual(kwargs['timeout'], (5, 70))

    @patch('echo.messaging.sender.send_message')
    @patch('echo.agent.processor.process_message')
    def test_ignored_events_do_not_process_or_send(self, process, send):
        variants = [{'isFromMe': True}, {'text': None}, {'text': ' '},
                    {'text': '\ufffc'}, {'associatedMessageType': 2000}]
        events = []
        for changes in variants:
            event = deepcopy(EVENT)
            event['data'].update(changes)
            events.append(event)
        events.append({'type': 'updated-message', 'data': EVENT['data']})
        events.append({'type': 'hello-world'})
        for event in events:
            with self.subTest(event=event):
                result = self.client.post('/webhook', json=event)
                self.assertEqual(result.json['status'], 'ignored')
        process.assert_not_called()
        send.assert_not_called()

    @patch('echo.messaging.sender.send_message')
    def test_malformed_messages_do_not_send(self, send):
        events = [[], {}, {'type': 'new-message', 'data': None}]
        for changes in [{'isFromMe': 'false'}, {'handle': None}, {'chats': []},
                        {'chats': [None]}, {'text': 7}]:
            event = deepcopy(EVENT)
            event['data'].update(changes)
            events.append(event)
        for event in events:
            with self.subTest(event=event):
                self.assertEqual(self.client.post('/webhook', json=event).status_code, 400)
        self.assertEqual(self.client.post('/webhook', data='{', content_type='application/json').status_code, 400)
        send.assert_not_called()

    @patch('echo.messaging.sender.send_message')
    @patch('echo.agent.processor.process_message')
    def test_processor_failures_and_empty_response(self, process, send):
        process.side_effect = RuntimeError('agent unavailable')
        self.assertEqual(self.client.post('/webhook', json=EVENT).status_code, 500)
        process.side_effect = None
        process.return_value = None
        self.assertEqual(self.client.post('/webhook', json=EVENT).status_code, 500)
        process.return_value = ''
        self.assertEqual(self.client.post('/webhook', json=EVENT).json['status'], 'ignored')
        send.assert_not_called()

    @patch('echo.agent.processor.process_message', return_value='test response')
    @patch('echo.messaging.sender.requests.post', side_effect=requests.Timeout('secret URL'))
    def test_send_failure_is_reported_without_credentials(self, post, process):
        with self.assertLogs('echo.messaging.sender', level='ERROR') as logs:
            result = self.client.post('/webhook', json=EVENT)
        self.assertEqual(result.status_code, 502)
        self.assertNotIn('secret URL', ''.join(logs.output))

    @patch('echo.messaging.sender.requests.post')
    def test_http_api_and_invalid_json_failures(self, post):
        http_error = requests.HTTPError(response=Mock(status_code=401))
        for status_error, body, json_error in [
            (http_error, {}, None), (None, {'status': 500}, None),
            (None, {'status': 200, 'error': {'type': 'failure'}}, None),
            (None, [], None), (None, {}, ValueError('invalid JSON')),
        ]:
            with self.subTest(body=body, error=status_error):
                post.return_value.raise_for_status.side_effect = status_error
                post.return_value.json.return_value = body
                post.return_value.json.side_effect = json_error
                with self.assertRaises(SendError):
                    send_message('response', 'chat', server_url='http://localhost:1234', password='secret')

    def test_missing_password_fails_at_startup(self):
        with patch.dict(os.environ, {'BLUEBUBBLES_PASSWORD': ''}), self.assertRaises(ValueError):
            create_app()


if __name__ == '__main__':
    unittest.main()
