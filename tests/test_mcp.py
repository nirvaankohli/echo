"""Real local MCP + detached worker tests; a fake CLI replaces Codex and costs nothing."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mcp import Client, StdioServerParameters

from agent import codex_jobs as jobs
from agent.processor import process_message
from agent.tools import enabled_for, respond


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'workspace').mkdir()
        self.fake = self.root / 'codex'
        self.fake.write_text(f'#!{sys.executable}\n' + '''
import sys, time
from pathlib import Path
task = sys.stdin.read()
assert "--sandbox" in sys.argv and "workspace-write" in sys.argv
assert "--ignore-user-config" in sys.argv
time.sleep(0.4)
if task == "fail": sys.exit(7)
Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("Finished: " + task)
''')
        self.fake.chmod(0o700)
        self.env = patch.dict(os.environ, {
            'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'ECHO_JOBS_DIR': str(self.root / 'jobs'),
            'ECHO_CODEX_WORKSPACE': str(self.root / 'workspace'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def wait(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            data = jobs.get_job(job_id)
            if data['status'] not in ('queued', 'running'):
                return data
            time.sleep(.05)
        self.fail('Worker never finished')

    def test_detached_success_and_one_job_limit(self):
        job = jobs.start_job('make a file')
        with self.assertRaisesRegex(ValueError, 'already running'):
            jobs.start_job('second task')
        final = self.wait(job['job_id'])
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['result'], 'Finished: make a file')
        self.assertEqual(jobs.list_jobs()[0]['job_id'], job['job_id'])

    def test_failed_process_is_not_success(self):
        final = self.wait(jobs.start_job('fail')['job_id'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['exit_code'], 7)

    def test_rejects_bad_ids_and_live_repository(self):
        with self.assertRaises(ValueError):
            jobs.get_job('../../secret')
        for folder in (jobs.ROOT, jobs.ROOT.parent, jobs.ROOT / 'agent'):
            with patch.dict(os.environ, {'ECHO_CODEX_WORKSPACE': str(folder)}):
                with self.assertRaises(ValueError):
                    jobs.start_job('change it')
        self.assertEqual(jobs.list_jobs(), [])

    def test_dead_worker_is_interrupted(self):
        path = jobs.job_path('a' * 32)
        path.mkdir()
        jobs.save(path, {'job_id': path.name, 'status': 'running'})
        self.assertEqual(jobs.get_job(path.name)['status'], 'interrupted')

    def test_timeout_kills_process_group_and_records_failure(self):
        path = jobs.job_path('b' * 32)
        path.mkdir()
        jobs.save(path, dict(job_id=path.name, status='queued', workspace=str(self.root)))
        with patch.object(jobs.subprocess, 'Popen') as factory, patch.object(jobs.os, 'killpg') as kill:
            process = factory.return_value
            process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 1800), (None, None)]
            jobs.run_worker(path.name, 'codex', 'task')
            kill.assert_called_once()
        self.assertEqual(jobs.get_job(path.name)['status'], 'timed_out')

    def test_real_stdio_job_survives_client_exit(self):
        async def start():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'agent.mcp_server'],
                                           cwd=jobs.ROOT, env=dict(os.environ))
            async with Client(params, read_timeout_seconds=10) as client:
                listing = await client.list_tools()
                self.assertEqual({t.name for t in listing.tools},
                                 {'codex_start', 'codex_status', 'codex_jobs'})
                result = await client.call_tool('codex_start', {'task': 'through MCP'})
                self.assertFalse(result.is_error)
                # Dict-return tools use text content on the current SDK.
                return json.loads(result.content[0].text)['job_id']
        final = self.wait(asyncio.run(start()))
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['result'], 'Finished: through MCP')

    def test_model_cannot_start_twice_in_one_message(self):
        client = Mock()
        first = SimpleNamespace(type='function_call', name='codex_start',
                                arguments='{"task":"first"}', call_id='c1')
        second = SimpleNamespace(type='function_call', name='codex_start',
                                 arguments='{"task":"second"}', call_id='c2')
        client.responses.create.side_effect = [
            SimpleNamespace(status='completed', output=[first], output_text=''),
            SimpleNamespace(status='completed', output=[second], output_text=''),
            SimpleNamespace(status='completed', output=[], output_text='Started one job.'),
        ]
        self.assertEqual(asyncio.run(respond(client, 'do one task', '', 'test')), 'Started one job.')
        history = client.responses.create.call_args.kwargs['input']
        self.assertIn('Only one job', json.loads(history[-1]['output'])['error'])
        states = jobs.list_jobs()
        self.assertEqual(len(states), 1)
        self.assertEqual(self.wait(states[0]['job_id'])['result'], 'Finished: first')


class BridgeTests(unittest.TestCase):
    def test_tools_require_enabled_and_exact_owner(self):
        with patch.dict(os.environ, {'ECHO_MCP_ENABLED': '1', 'ECHO_TOOL_OWNER': '+15551234567'}):
            self.assertTrue(enabled_for('+15551234567'))
            self.assertFalse(enabled_for('+15557654321'))
            self.assertFalse(enabled_for('self'))
        with patch.dict(os.environ, {'ECHO_MCP_ENABLED': '1', 'ECHO_TOOL_OWNER': ''}):
            self.assertFalse(enabled_for(''))

    @patch('agent.processor.load_dotenv')
    @patch.dict(os.environ, {'OPENAI_API_KEY': 'fake', 'ECHO_MCP_ENABLED': '1',
                            'ECHO_TOOL_OWNER': 'owner'})
    @patch('agent.processor.OpenAI')
    @patch('agent.tools.respond')
    def test_default_and_nonowner_do_not_get_tools(self, respond_mock, factory, load):
        factory.return_value.__enter__.return_value.responses.create.return_value = Mock(
            status='completed', output_text='Hello')
        process_message('start work', 'owner')
        process_message('start work', 'stranger', allow_tools=True)
        respond_mock.assert_not_called()

    def test_model_tool_round_trip_over_real_mcp(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ECHO_JOBS_DIR': folder}):
            client = Mock()
            call = SimpleNamespace(type='function_call', name='codex_jobs', arguments='{}', call_id='c1')
            client.responses.create.side_effect = [
                SimpleNamespace(status='completed', output=[call], output_text=''),
                SimpleNamespace(status='completed', output=[], output_text='No jobs yet.'),
            ]
            self.assertEqual(asyncio.run(respond(client, 'show jobs', '', 'test')), 'No jobs yet.')
            history = client.responses.create.call_args.kwargs['input']
            self.assertEqual(history[-1]['type'], 'function_call_output')
            self.assertEqual(history[-1]['call_id'], 'c1')
            self.assertEqual(json.loads(history[-1]['output'])['structuredContent'], {'result': []})

    def test_model_incomplete_response_never_calls_a_tool(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ECHO_JOBS_DIR': folder}):
            client = Mock()
            client.responses.create.return_value = SimpleNamespace(status='incomplete')
            with self.assertRaises(ExceptionGroup) as caught:
                asyncio.run(respond(client, 'show jobs', '', 'test'))
            error = caught.exception
            while isinstance(error, ExceptionGroup):
                self.assertEqual(len(error.exceptions), 1)
                error = error.exceptions[0]
            self.assertIsInstance(error, RuntimeError)
            self.assertIn('did not complete', str(error))
