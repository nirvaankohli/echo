import unittest
from unittest.mock import patch

from messaging.imsg_transport import handle_message
from run_echo import ChatSelectionError


class DiagnosticsTests(unittest.TestCase):
    @patch('agent.processor.process_message')
    def test_skipped_chat_format_is_logged_without_private_data(self, process):
        event = {'chat_id': 42, 'chat_guid': 'SMS;-;private@example.com',
                 'chat_identifier': 'private@example.com', 'sender': 'private@example.com',
                 'text': 'private message text', 'is_from_me': False}
        with self.assertLogs('messaging.imsg_transport', level='INFO') as logs:
            self.assertFalse(handle_message(event, 42))
        output = '\n'.join(logs.output)
        self.assertIn('unsupported chat format', output)
        self.assertNotIn('private', output)
        process.assert_not_called()

    @patch('builtins.input', return_value='')
    @patch('run_echo.select_chat', side_effect=ChatSelectionError('No unique direct iMessage chat.'))
    @patch('pathlib.Path.read_bytes')
    def test_diagnostic_preserves_safe_selection_reason(self, read, select, prompt):
        import contextlib
        import io
        import plistlib
        import runpy
        read.return_value = plistlib.dumps({'ProgramArguments': ['python', 'run_echo.py', '--to', 'test@example.com']})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            runpy.run_module('diagnose', run_name='__main__')
        self.assertIn('No unique direct iMessage chat.', output.getvalue())
