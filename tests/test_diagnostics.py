import unittest
from unittest.mock import patch

from messaging.imsg_transport import handle_message


class DiagnosticsTests(unittest.TestCase):
    @patch('agent.processor.process_message')
    def test_skipped_chat_format_is_logged_without_private_data(self, process):
        event = {'chat_id': 42, 'chat_guid': 'any;-;private@example.com',
                 'chat_identifier': 'private@example.com', 'sender': 'private@example.com',
                 'text': 'private message text', 'is_from_me': False}
        with self.assertLogs('messaging.imsg_transport', level='INFO') as logs:
            self.assertFalse(handle_message(event, 42))
        output = '\n'.join(logs.output)
        self.assertIn('unsupported chat format', output)
        self.assertNotIn('private', output)
        process.assert_not_called()
