import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from repair import direct_launcher
from run_echo import main


class RepairTests(unittest.TestCase):
    def test_migrates_old_caffeinate_wrapper_without_changing_recipient(self):
        args = ['/Users/echo/echo/.venv/bin/python', '-u', '/Users/echo/echo/run_echo.py',
                '--send', '--to', '+15551234567']
        old = {'ProgramArguments': ['/usr/bin/caffeinate', '-i', *args],
               'EnvironmentVariables': {'PATH': '/usr/bin'}}
        new = direct_launcher(old)
        self.assertEqual(new['ProgramArguments'], args)
        self.assertEqual(new['EnvironmentVariables']['ECHO_KEEP_AWAKE'], '1')
        self.assertNotIn('ECHO_KEEP_AWAKE', old['EnvironmentVariables'])
        self.assertEqual(direct_launcher(new), new)

    @patch.dict('os.environ', {'ECHO_KEEP_AWAKE': '1'})
    @patch('run_echo.subprocess.Popen')
    @patch('run_echo.watch_messages')
    def test_sleep_helper_runs_as_child_and_stops_on_failure(self, watch, popen):
        watch.side_effect = RuntimeError('watcher failed')
        helper = popen.return_value
        helper.poll.return_value = None
        with patch('sys.argv', ['run_echo.py', '--chat-id', '42']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 1)
        self.assertEqual(popen.call_args.args[0][:3], ['/usr/bin/caffeinate', '-i', '-w'])
        helper.terminate.assert_called_once()
        helper.wait.assert_called_once_with(timeout=5)
