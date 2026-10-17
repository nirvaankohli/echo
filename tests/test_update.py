import unittest
import subprocess
from unittest.mock import Mock, patch

from update_bot import update


class UpdateTests(unittest.TestCase):
    @patch('update_bot.run')
    def test_local_changes_never_fetch_or_restart(self, run):
        run.return_value.stdout = ' M agent/processor.py'
        update()
        run.assert_called_once_with('git', 'status', '--porcelain', capture=True)

    @patch('update_bot.run')
    def test_failed_candidate_tests_leave_live_checkout_untouched(self, run):
        def result(*args, **kwargs):
            if args[:3] == ('git', 'status', '--porcelain'):
                return Mock(stdout='')
            if args[:3] == ('git', 'branch', '--show-current'):
                return Mock(stdout='main')
            if args[:2] == ('git', 'rev-parse'):
                return Mock(stdout='new' if args[2] == 'origin/main' else 'old')
            if 'unittest' in args:
                raise subprocess.CalledProcessError(1, args)
            return Mock(stdout='')
        run.side_effect = result
        with self.assertRaises(subprocess.CalledProcessError):
            update()
        commands = [call.args for call in run.call_args_list]
        self.assertFalse(any(cmd[:2] == ('git', 'merge') for cmd in commands))
        self.assertFalse(any(cmd[0] == 'launchctl' for cmd in commands))
        self.assertTrue(any(cmd[:3] == ('git', 'worktree', 'remove') for cmd in commands))
