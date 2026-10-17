import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from run_echo import main


class LauncherTests(unittest.TestCase):
    @patch('run_echo.watch_messages')
    @patch('run_echo.subprocess.run')
    def test_exact_address_selects_only_direct_imessage_chat(self, run, watch):
        chats = [
            {'id': 1, 'service': 'SMS', 'identifier': '+15551234567'},
            {'id': 2, 'service': 'iMessage', 'identifier': '+15551234567', 'is_group': True},
            {'id': 3, 'service': 'iMessage', 'identifier': '+15551234567', 'is_group': False},
        ]
        run.return_value = Mock(returncode=0, stdout='\n'.join(map(json.dumps, chats)))
        with patch('sys.argv', ['run_echo.py', '--self-chat', '--send', '--to', '+15551234567']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        self.assertEqual(watch.call_args.args, (3,))
        self.assertTrue(watch.call_args.kwargs['self_chat'])
        self.assertTrue(watch.call_args.kwargs['send'])

    @patch('run_echo.watch_messages')
    @patch('run_echo.subprocess.run')
    def test_missing_or_ambiguous_address_does_not_start(self, run, watch):
        chat = {'id': 3, 'service': 'iMessage', 'identifier': '+15551234567'}
        for chats in [[{**chat, 'identifier': '+15557654321'}], [chat, {**chat, 'id': 4}]]:
            run.return_value = Mock(returncode=0, stdout='\n'.join(map(json.dumps, chats)))
            with patch('sys.argv', ['run_echo.py', '--self-chat', '--send', '--to', '+15551234567']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 1)
        watch.assert_not_called()
