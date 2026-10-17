import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import deployment_log as log


class DeploymentLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = self.root / 'local-logs'
        self.patch = patch.object(log, 'LOGS', self.logs)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_startup_requires_matching_fresh_live_process(self):
        started = time.time()
        log.startup('abc')
        self.assertTrue(log.wait_for_start('abc', started, timeout=0.1))
        with self.assertRaises(TimeoutError):
            log.wait_for_start('wrong-commit', started, timeout=0.01)
        with self.assertRaises(TimeoutError):
            log.wait_for_start('abc', time.time() + 10, timeout=0.01)
        with patch('deployment_log.os.kill', side_effect=ProcessLookupError):
            with self.assertRaises(TimeoutError):
                log.wait_for_start('abc', started, timeout=0.01)

    def test_publish_appends_on_separate_branch_without_changing_main(self):
        remote, checkout = self.root / 'remote.git', self.root / 'checkout'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        checkout.mkdir()
        log.git('init', '-b', 'main', cwd=checkout)
        log.git('remote', 'add', 'origin', str(remote), cwd=checkout)
        (checkout / 'code.py').write_text('print("hello")\n')
        log.git('add', 'code.py', cwd=checkout)
        log.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                'commit', '-m', 'Initial', cwd=checkout)
        log.git('push', 'origin', 'main', cwd=checkout)
        original = log.git('rev-parse', 'HEAD', cwd=checkout)
        with patch.object(log, 'ROOT', checkout):
            log.record('update_detected', commit=original)
            log.publish()
            first = log.git('ls-remote', 'origin', 'refs/heads/deployment-logs')
            log.publish()
            self.assertEqual(log.git('ls-remote', 'origin', 'refs/heads/deployment-logs'), first)
            log.startup(original)
            log.publish()
            log.git('fetch', 'origin', 'deployment-logs')
            filename = f"logs/{(self.logs / 'instance-id').read_text()}.jsonl"
            entries = [json.loads(line) for line in log.git('show', f'FETCH_HEAD:{filename}').splitlines()]
            self.assertEqual([e['event'] for e in entries], ['update_detected', 'watcher_started'])
            self.assertTrue(all(e['time'].endswith('+00:00') for e in entries))
            self.assertEqual(log.git('rev-parse', 'HEAD'), original)
            self.assertEqual(log.git('status', '--porcelain'), '')
            self.assertEqual(log.git('ls-tree', '-r', '--name-only', 'FETCH_HEAD'), filename)

    def test_failed_upload_keeps_local_journal(self):
        log.record('watcher_started', commit='abc')
        before = (self.logs / 'events.jsonl').read_bytes()
        with patch('deployment_log.git', side_effect=subprocess.CalledProcessError(1, 'git')):
            with self.assertRaises(subprocess.CalledProcessError):
                log.publish()
        self.assertEqual((self.logs / 'events.jsonl').read_bytes(), before)

    @patch('messaging.imsg_transport.deployment_log.startup')
    @patch('messaging.imsg_transport.deployment_log.record')
    @patch('messaging.imsg_transport.deployment_log.git', return_value='abc')
    @patch('messaging.imsg_transport.subprocess.Popen')
    def test_failed_watcher_never_acknowledges_start(self, popen, git, record, startup):
        from messaging.imsg_transport import watch_messages
        child = popen.return_value.__enter__.return_value
        child.wait.return_value = 1
        child.poll.return_value = 1
        with self.assertRaises(RuntimeError):
            watch_messages(42, send=True)
        startup.assert_not_called()

    @patch('messaging.imsg_transport.deployment_log.startup')
    @patch('messaging.imsg_transport.deployment_log.record')
    @patch('messaging.imsg_transport.deployment_log.git', return_value='abc')
    @patch('messaging.imsg_transport.subprocess.Popen')
    def test_watcher_acknowledges_after_startup_window(self, popen, git, record, startup):
        from messaging.imsg_transport import watch_messages
        child = popen.return_value.__enter__.return_value
        child.wait.side_effect = [subprocess.TimeoutExpired('imsg', 2), 0]
        child.stdout = iter([])
        child.poll.return_value = 0
        watch_messages(42, send=True)
        startup.assert_called_once_with('abc')
