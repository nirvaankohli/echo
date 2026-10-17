"""Local-transport tests never read or send real messages."""

import subprocess
import unittest
from unittest.mock import Mock, patch

from messaging.imsg_transport import handle_message, watch_messages

EVENT = {'chat_id': 42, 'chat_guid': 'iMessage;-;test@example.com',
         'sender': 'test@example.com', 'text': 'Hello', 'is_from_me': False}


class ImsgTests(unittest.TestCase):
    @patch('messaging.imsg_transport.subprocess.run')
    @patch('agent.processor.process_message', return_value='replacement response')
    def test_dry_run_calls_agent_without_sending(self, process, run):
        self.assertTrue(handle_message(EVENT, 42))
        process.assert_called_once_with('Hello', 'test@example.com')
        run.assert_not_called()

    @patch('messaging.imsg_transport.subprocess.run')
    @patch('agent.processor.process_message', return_value='replacement response')
    def test_sends_agent_response_to_original_chat(self, process, run):
        run.return_value = Mock(returncode=0, stdout='{"status":"sent"}')
        self.assertTrue(handle_message(EVENT, 42, send=True))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--text') + 1], 'replacement response')
        self.assertEqual(command[command.index('--chat-guid') + 1], EVENT['chat_guid'])
        self.assertIn('--no-sms-fallback', command)

    @patch('agent.processor.process_message')
    def test_ignores_own_other_chat_and_non_text_events(self, process):
        for changes in [{'is_from_me': True}, {'is_from_me': None}, {'chat_id': 43},
                        {'chat_guid': 'SMS;-;test'}, {'chat_guid': None},
                        {'text': ''}, {'text': '\ufffc'}, {'is_reaction': True}, {'sender': None}]:
            with self.subTest(changes=changes):
                self.assertFalse(handle_message({**EVENT, **changes}, 42, send=True))
        process.assert_not_called()

    @patch('agent.processor.process_message', return_value='test response')
    @patch('messaging.imsg_transport.subprocess.run')
    def test_failed_or_uncertain_send_is_not_retried(self, run, process):
        for result in [Mock(returncode=1), Mock(returncode=0, stdout='invalid'),
                       Mock(returncode=0, stdout='{}')]:
            run.reset_mock()
            run.return_value = result
            with self.assertRaises(RuntimeError):
                handle_message(EVENT, 42, send=True)
            run.assert_called_once()
        run.side_effect = subprocess.TimeoutExpired('imsg', 45)
        with self.assertRaises(subprocess.TimeoutExpired):
            handle_message(EVENT, 42, send=True)

    @patch('messaging.imsg_transport.subprocess.Popen')
    def test_watch_failure_terminates_child(self, popen):
        child = popen.return_value.__enter__.return_value
        child.stdout = iter(['invalid json\n'])
        child.poll.return_value = None
        with self.assertRaises(ValueError):
            watch_messages(42)
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)


class SelfChatTests(unittest.TestCase):
    @patch('messaging.imsg_transport.subprocess.run')
    @patch('agent.processor.process_message', return_value='hi')
    def test_own_command_passes_clean_text_to_agent(self, process, run):
        run.return_value = Mock(returncode=0, stdout='{"status":"sent"}')
        event = {**EVENT, 'is_from_me': True, 'sender': '', 'text': '/echo hello'}
        self.assertTrue(handle_message(event, 42, self_chat=True, send=True))
        process.assert_called_once_with('hello', 'self')
        run.assert_called_once()

    @patch('agent.processor.process_message')
    def test_replies_incoming_copies_and_groups_do_not_trigger(self, process):
        base = {**EVENT, 'is_from_me': True, 'text': '/echo hello'}
        for changes in [{'text': 'hi'}, {'text': 'ordinary message'}, {'text': '/echo '},
                        {'is_from_me': False}, {'chat_id': 43}, {'is_group': True},
                        {'chat_guid': 'iMessage;+;group'}]:
            with self.subTest(changes=changes):
                self.assertFalse(handle_message({**base, **changes}, 42, self_chat=True, send=True))
        process.assert_not_called()

    @patch('messaging.imsg_transport.subprocess.run')
    @patch('agent.processor.process_message', return_value='/echo recursive reply')
    def test_agent_cannot_generate_another_command(self, process, run):
        with self.assertRaises(ValueError):
            handle_message({**EVENT, 'is_from_me': True, 'text': '/echo hello'},
                           42, self_chat=True, send=True)
        run.assert_not_called()

    @patch('messaging.imsg_transport.subprocess.run')
    @patch('agent.processor.process_message', return_value='hi')
    def test_any_service_self_chat_command_and_received_copy(self, process, run):
        run.return_value = Mock(returncode=0, stdout='{"status":"sent"}')
        event = {**EVENT, 'chat_guid': 'any;-;+15551234567',
                 'chat_identifier': '+15551234567', 'is_group': False,
                 'text': '/echo hiii', 'is_from_me': True}
        self.assertTrue(handle_message(event, 42, self_chat=True, send=True))
        process.assert_called_once_with('hiii', 'self')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--to') + 1], '+15551234567')
        self.assertEqual(command[command.index('--service') + 1], 'imessage')
        self.assertIn('--no-sms-fallback', command)
        self.assertFalse(handle_message({**event, 'is_from_me': False}, 42,
                                        self_chat=True, send=True))
        run.assert_called_once()

    @patch('agent.processor.process_message')
    def test_any_chat_requires_matching_direct_identifier(self, process):
        base = {**EVENT, 'text': '/echo hello', 'is_from_me': True,
                'chat_guid': 'any;-;+15551234567'}
        for changes in [{}, {'chat_identifier': '+15557654321'},
                        {'chat_identifier': '+15551234567', 'is_group': True}]:
            self.assertFalse(handle_message({**base, **changes}, 42, self_chat=True))
        process.assert_not_called()
