import os
from pathlib import Path
import plistlib
import unittest
from unittest.mock import Mock, patch

from agent.processor import process_message
from service import build_plist


class AgentTests(unittest.TestCase):
    @patch('agent.processor.load_dotenv')
    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key', 'OPENAI_MODEL': 'test-model'})
    @patch('agent.processor.OpenAI')
    def test_reply_and_private_sender(self, factory, load):
        create = factory.return_value.__enter__.return_value.responses.create
        create.return_value = Mock(status='completed', output_text='  Hello!  ')
        self.assertEqual(process_message('Hello', '+15551234567'), 'Hello!')
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['input'], 'Hello')
        self.assertEqual(kwargs['model'], 'test-model')
        self.assertFalse(kwargs['store'])
        self.assertNotIn('+15551234567', str(kwargs))

    @patch('agent.processor.load_dotenv')
    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('agent.processor.OpenAI')
    def test_empty_incomplete_and_failed_responses(self, factory, load):
        create = factory.return_value.__enter__.return_value.responses.create
        for status, text in [('completed', ' '), ('incomplete', 'partial')]:
            create.return_value = Mock(status=status, output_text=text)
            with self.assertRaises(RuntimeError):
                process_message('Hello', 'local')
        create.side_effect = RuntimeError('API unavailable')
        with self.assertRaises(RuntimeError):
            process_message('Hello', 'local')

    def test_service_uses_echo_paths_and_survives_exit(self):
        project = Path('/Users/echo/My Bot')
        config = plistlib.loads(plistlib.dumps(build_plist(
            project, Path('/Users/echo'), '+15551234567', '/opt/homebrew/bin/imsg')))
        self.assertTrue(config['KeepAlive'])
        self.assertTrue(config['RunAtLoad'])
        self.assertEqual(config['LimitLoadToSessionType'], 'Aqua')
        self.assertEqual(config['ProgramArguments'][0], str(project / '.venv/bin/python'))
        self.assertEqual(config['ProgramArguments'][-2:], ['--to', '+15551234567'])
        self.assertNotIn('--self-chat', config['ProgramArguments'])
        self.assertNotIn('OPENAI_API_KEY', config['EnvironmentVariables'])

class ServiceInstallTests(unittest.TestCase):
    @patch('service.subprocess.run')
    def test_replaces_loaded_service_before_starting(self, run):
        import tempfile
        from service import install_service
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = build_plist(home / 'echo', home, 'test@example.com', '/opt/homebrew/bin/imsg')
            install_service(config, home)
            saved = plistlib.loads((home / 'Library/LaunchAgents/local.echo.bot.plist').read_bytes())
            self.assertEqual(saved, config)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([cmd[1] for cmd in commands], ['print', 'bootout', 'bootstrap'])

    @patch('service.subprocess.run')
    def test_new_service_bootstraps_without_bootout(self, run):
        import tempfile
        from service import install_service
        run.return_value.returncode = 1
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            install_service(build_plist(home / 'echo', home, 'test@example.com', '/opt/homebrew/bin/imsg'), home)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ['print', 'bootstrap'])

    def test_updater_is_periodic_and_keep_awake_wraps_only_bot(self):
        project, home = Path('/Users/echo/My Bot'), Path('/Users/echo')
        bot = build_plist(project, home, 'test@example.com', '/opt/homebrew/bin/imsg', keep_awake=True)
        updater = build_plist(project, home, 'test@example.com', '/opt/homebrew/bin/imsg',
                              update=True, keep_awake=True)
        self.assertEqual(bot['ProgramArguments'][0], str(project / '.venv/bin/python'))
        self.assertEqual(bot['EnvironmentVariables']['ECHO_KEEP_AWAKE'], '1')
        self.assertEqual(updater['EnvironmentVariables']['ECHO_KEEP_AWAKE'], '0')
        self.assertEqual(updater['StartInterval'], 300)
        self.assertNotIn('KeepAlive', updater)
        self.assertEqual(updater['Label'], 'local.echo.update')
        self.assertEqual(updater['EnvironmentVariables']['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(updater['ProgramArguments'], [str(project / '.venv/bin/python'), '-u',
                                                     str(project / 'update_bot.py')])
