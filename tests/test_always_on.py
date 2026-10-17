"""Always-on acceptance tests: no real model, tools, account or iMessage actions."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests

from echo.agent.coordinator import Coordinator, Approval, Waiting
from echo.agent.memory import Memory
from echo.agent.server import create_app, process_one
from echo.agent.tasks import Tasks
from echo.messaging.bridge import deliver_notifications, relay_task, relay_one
from echo.runtime.queue import Queue

OWNER = '+15551234567'
CONFIG = dict(role='agent', owner=OWNER, token='t' * 40, port=8877)
MESSAGE = dict(id='task1', sender=OWNER, text='Create a private study document')


def response(text='', calls=()):
    return SimpleNamespace(status='completed', output_text=text, output=list(calls),
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class Call:
    type = 'function_call'
    def __init__(self, name='start', call_id='c1', args=None):
        self.name, self.call_id, self.arguments = name, call_id, json.dumps(args or {})
    def model_dump(self, **kwargs):
        return dict(type=self.type, name=self.name, call_id=self.call_id, arguments=self.arguments)


def tool_result(data):
    return SimpleNamespace(model_dump=lambda **kwargs: {'structuredContent': data})


class AlwaysOnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '200000', 'ECHO_DAILY_TOKEN_LIMIT': '500000',
                                           'ECHO_DAILY_USD_LIMIT': '0', 'ECHO_MODEL_PRICES': '{}'}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tasks = Tasks(self.path / 'tasks.db')
        self.memory = Memory(self.path / 'memory.db')
        self.coordinator = Coordinator(self.tasks, self.memory)
        self.legacy = Queue(self.path / 'legacy.db')
        self.wake = threading.Event()
        self.client = create_app(CONFIG, self.legacy, self.wake, self.coordinator).test_client()
        self.headers = {'Authorization': 'Bearer ' + CONFIG['token']}

    def submit(self, **kwargs):
        message = {**MESSAGE, **kwargs}
        return self.client.post('/v2/messages', json=message, headers=self.headers)

    def test_task_ack_replay_owner_auth_and_restart(self):
        self.assertEqual(self.client.post('/v2/messages', json=MESSAGE).status_code, 401)
        self.assertEqual(self.submit(sender='other').status_code, 403)
        for _ in range(3):
            self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(len(self.tasks.outbox()), 1)
        self.assertEqual(self.submit(text='changed').status_code, 409)
        self.assertEqual(self.tasks.summary()['tasks'], {'queued': 1})
        task = self.tasks.claim()
        self.assertEqual(task['id'], 'task1')
        reopened = Tasks(self.path / 'tasks.db')
        reopened.recover_tasks()
        self.assertEqual(reopened.task('task1')['status'], 'uncertain')
        self.assertIsNone(reopened.claim())
        self.coordinator.events_once()
        self.assertEqual(len(reopened.outbox()), 2)

    def test_control_does_not_hide_task_and_never_enters_work_queue(self):
        self.submit()
        self.tasks.claim()
        with patch('echo.agent.coordinator.OpenAI') as model:
            self.submit(id='status1', text='status')
            self.submit(id='cancel1', text='cancel')
            self.assertEqual(self.tasks.latest()['id'], 'task1')
            self.assertEqual(self.tasks.task('task1')['cancel'], 1)
            self.assertIsNone(self.tasks.claim())
            model.assert_not_called()

    def test_simple_chat_has_no_ack_or_planner(self):
        self.submit(text='hi')
        self.assertEqual(self.tasks.outbox(), [])
        client = Mock()
        client.responses.create.return_value = response('Hello!')
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.tasks.claim())
        self.assertEqual(client.responses.create.call_count, 1)
        self.coordinator.events_once()
        self.assertEqual(self.tasks.outbox()[0]['text'], 'Hello!')

    def test_legacy_protocol_remains_single_reply_and_migration_conflict(self):
        self.client.post('/v1/messages', json=MESSAGE, headers=self.headers)
        with patch('echo.agent.server.process_message', return_value='old reply'):
            process_one(self.legacy, self.legacy.pending()[0])
        self.assertEqual(self.client.post('/v1/messages', json=MESSAGE, headers=self.headers).json['result'], 'old reply')
        self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(self.tasks.outbox(), [])
        self.assertEqual(self.client.get('/v1/health', headers=self.headers).json['protocol'], 1)

    def test_send_ack_loss_and_crash_never_duplicate(self):
        deliveries = Queue(self.path / 'deliveries.db')
        session = Mock()
        session.get.return_value.json.return_value = {'notifications': [{'id': 'n1', 'text': 'done', 'task_id': 'task1'}]}
        session.post.side_effect = requests.ConnectionError()
        with patch('echo.messaging.bridge.send_reply') as send:
            with self.assertRaises(requests.ConnectionError):
                deliver_notifications(deliveries, session, CONFIG, 'imsg')
            session.post.side_effect = None
            deliver_notifications(deliveries, session, CONFIG, 'imsg')
            send.assert_called_once_with('done', ['--to', OWNER], 'imsg')
        deliveries.put('n2', {'text': 'unknown'})
        deliveries.update('n2', 'sending')
        deliveries.recover('sending')
        session.get.return_value.json.return_value = {'notifications': [{'id': 'n2', 'text': 'unknown'}]}
        with patch('echo.messaging.bridge.send_reply') as send:
            deliver_notifications(deliveries, session, CONFIG, 'imsg')
            send.assert_not_called()
        self.assertEqual(session.post.call_args.kwargs['json']['status'], 'uncertain')

    def test_cancellation_and_budget_gate_before_tool(self):
        self.submit()
        row = self.tasks.claim()
        self.tasks.cancel(row['id'])
        with patch('echo.agent.coordinator.OpenAI') as model:
            self.coordinator.execute(row)
            model.assert_not_called()
        self.assertEqual(self.tasks.task(row['id'])['status'], 'cancelled')

    def test_unknown_tool_requires_persisted_exact_approval(self):
        self.submit()
        self.tasks.claim()
        remote = SimpleNamespace(call_tool=AsyncMock(return_value=tool_result({'ok': True})))
        route = (remote, 'create_document', 'docs', 'approval')
        with self.assertRaises(Approval):
            asyncio.run(self.coordinator.call_tool('task1', 'task1:c1', route, {'title': 'study'}, time.monotonic()+10, []))
        remote.call_tool.assert_not_called()
        self.tasks.change('task1', status='awaiting_input')
        self.submit(id='approval', text='approve')
        asyncio.run(self.coordinator.call_tool('task1', 'task1:c1', route, {'title': 'study'}, time.monotonic()+10, []))
        remote.call_tool.assert_called_once()
        with self.assertRaises(ValueError):
            asyncio.run(self.coordinator.call_tool('task1', 'task1:c1', route, {'title': 'different'}, time.monotonic()+10, []))

    def test_tool_failure_has_uncertain_outcome_never_replayed(self):
        self.submit()
        self.tasks.claim()
        remote = SimpleNamespace(call_tool=AsyncMock(side_effect=RuntimeError('network')))
        route = (remote, 'create_document', 'docs', 'private_write')
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.coordinator.call_tool('task1', 'task1:c1', route, {}, time.monotonic()+10, []))
        remote.call_tool.assert_called_once()

    def test_codex_completion_resumes_once_and_final_notification(self):
        self.submit()
        remote = SimpleNamespace(call_tool=AsyncMock(return_value=tool_result({'job_id': 'j1', 'status': 'queued'})))
        route = (remote, 'codex_start', 'echo_codex', 'private_write')
        client = Mock()
        client.responses.create.side_effect = [response('Use Codex and verify.'), response(calls=[Call(args={'task': 'write file'})])]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {'start': route}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.tasks.claim())
        self.assertEqual(self.tasks.task('task1')['status'], 'waiting')
        remote.call_tool.return_value = tool_result({'job_id': 'j1', 'status': 'completed', 'result': 'File created'})
        with patch('echo.agent.coordinator.configured_servers', return_value={'echo_codex': {}}), \
             patch('echo.agent.coordinator.connect', new=AsyncMock(return_value=remote)):
            asyncio.run(self.coordinator.jobs_once())
            asyncio.run(self.coordinator.jobs_once())
        self.assertEqual(self.tasks.task('task1')['status'], 'queued')
        client.responses.create.side_effect = [response('File created and verified.')]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {'start': route}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.tasks.claim())
        self.coordinator.events_once()
        self.coordinator.events_once()
        self.assertEqual(self.tasks.task('task1')['status'], 'completed')
        self.assertEqual(len(self.tasks.outbox()), 2)
        self.assertEqual(remote.call_tool.call_count, 2) # start and one authoritative status

    def test_cancel_waiting_job_does_not_resume_success(self):
        self.submit()
        self.tasks.change('task1', status='waiting')
        with self.tasks.db() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', ('j', 'task1', 'echo', 'j1', 'running', None, 0))
        self.submit(id='cancel', text='cancel')
        with patch('echo.agent.coordinator.connect') as connect:
            asyncio.run(self.coordinator.jobs_once())
            connect.assert_not_called()
        self.assertEqual(self.tasks.task('task1')['status'], 'cancelled')

    def test_idle_coordinator_and_monitor_no_model_calls(self):
        stop, wake = threading.Event(), threading.Event()
        with patch('echo.agent.coordinator.OpenAI') as model:
            thread = threading.Thread(target=self.coordinator.run, args=(wake, stop))
            thread.start()
            asyncio.run(self.coordinator.jobs_once())
            self.coordinator.events_once()
            stop.set()
            wake.set()
            thread.join(2)
            model.assert_not_called()

    def test_all_new_endpoints_are_authenticated(self):
        for method, path in [('get','/v2/outbox'),('post','/v2/outbox/ack'),('post','/v2/events/codex')]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401)

    def test_cancel_finds_active_task_after_completed_social_chat(self):
        self.submit()
        self.tasks.claim()
        self.submit(id='greeting', text='hi')
        self.tasks.change('greeting', status='completed', result='hello')
        self.submit(id='cancel', text='cancel')
        self.assertEqual(self.tasks.task('task1')['cancel'], 1)
        self.assertEqual(self.tasks.task('greeting')['cancel'], 0)

    def test_permanent_bridge_rejection_and_lost_receipt(self):
        queue = Queue(self.path / 'bridge.db')
        queue.put('bad', {'sender': OWNER, 'text': 'bad', 'destination': ['--to', OWNER]})
        queue.put('good', {'sender': OWNER, 'text': 'good', 'destination': ['--to', OWNER]})
        session = Mock()
        session.post.return_value.status_code = 409
        session.post.return_value.json.return_value = {'error': 'Conflicting message ID'}
        relay_task(queue, queue.get('bad'), session, CONFIG)
        self.assertEqual(queue.get('bad')['status'], 'failed')
        self.assertEqual(queue.pending()[0]['id'], 'good')
        session.post.side_effect = requests.ConnectionError()
        with self.assertRaises(requests.ConnectionError):
            relay_task(queue, queue.get('good'), session, CONFIG)
        self.assertEqual(queue.get('good')['status'], 'v2_pending')

    def test_approval_resume_runs_saved_arguments_once(self):
        self.submit()
        remote = SimpleNamespace(call_tool=AsyncMock(return_value=tool_result({'url': 'https://example.test/doc'})))
        route = (remote, 'create_doc', 'docs', 'approval')
        client = Mock()
        client.responses.create.side_effect = [response('Create the doc'), response(calls=[Call(args={'title': 'Study'})])]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {'start': route}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.tasks.claim())
        self.assertEqual(self.tasks.task('task1')['status'], 'awaiting_input')
        remote.call_tool.assert_not_called()
        self.submit(id='approval', text='approve')
        client.responses.create.side_effect = [response('Created your doc: https://example.test/doc')]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {'stable-alias': route}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.tasks.claim())
        remote.call_tool.assert_called_once_with('create_doc', {'title': 'Study'})
        self.assertEqual(self.tasks.task('task1')['status'], 'completed')

    def test_tool_error_and_oversized_approval_never_replayed(self):
        self.submit()
        self.tasks.claim()
        remote = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
            model_dump=lambda **kw: {'isError': True, 'content': []})))
        with self.assertRaises(RuntimeError):
            asyncio.run(self.coordinator.call_tool('task1', 'task1:c1', (remote, 'write', 'docs', 'private_write'), {}, time.monotonic()+10, []))
        with self.assertRaises(ValueError):
            asyncio.run(self.coordinator.call_tool('task1', 'task1:c2', (remote, 'write', 'docs', 'approval'), {'body': 'x'*3000}, time.monotonic()+10, []))
        self.assertEqual(remote.call_tool.call_count, 1)

    def test_quick_codex_completion_during_wait_transition_is_not_lost(self):
        self.submit()
        row = self.tasks.claim()
        async def workflow(*args):
            with self.tasks.db() as db:
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', ('j', 'task1', 'echo', 'j1', 'completed', '{}', 0))
            raise Waiting()
        with patch('echo.agent.coordinator.OpenAI'), patch.object(self.coordinator, 'workflow', side_effect=workflow):
            self.coordinator.execute(row)
        self.assertEqual(self.tasks.task('task1')['status'], 'queued')

    def test_long_prior_task_does_not_remove_memory_context(self):
        self.memory.remember('Biology exam preparation preference: diagrams', source='user')
        self.tasks.submit('prior', OWNER, 'x' * 16000, None, status='completed')
        self.tasks.change('prior', result='y' * 12000)
        self.submit(text='Create biology exam notes')
        client = Mock()
        client.responses.create.side_effect = [response('Make biology notes'), response('Done')]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.tasks.claim())
        context = client.responses.create.call_args_list[0].kwargs['input'][0]['content']
        self.assertIn('diagrams', context)
        self.assertLess(len(context), 21000)
        self.assertIn('y' * 12000, context)  # The recent full result is no longer reduced to 800 characters.

    def test_control_remains_responsive_while_worker_is_blocked(self):
        self.submit()
        started, release = threading.Event(), threading.Event()
        def slow_execute(row):
            started.set()
            release.wait(2)
        stop = threading.Event()
        with patch.object(self.coordinator, 'execute', side_effect=slow_execute):
            thread = threading.Thread(target=self.coordinator.run, args=(self.wake, stop))
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertEqual(self.submit(id='status', text='status').status_code, 200)
            self.assertEqual(self.submit(id='cancel', text='cancel').status_code, 200)
            self.assertEqual(self.tasks.task('task1')['cancel'], 1)
            stop.set()
            release.set()
            self.wake.set()
            thread.join(2)

    def test_multiple_approvals_require_exact_visible_code(self):
        from echo.agent.coordinator import approval_id
        self.submit()
        self.submit(id='task2', text='Create another document')
        remote = SimpleNamespace(call_tool=AsyncMock())
        for task in ('task1', 'task2'):
            with self.assertRaises(Approval):
                asyncio.run(self.coordinator.call_tool(task, task + ':c1',
                    (remote, 'create_doc', 'docs', 'approval'), {'title': task}, time.monotonic()+10, []))
        self.submit(id='ambiguous', text='approve')
        self.assertEqual(self.tasks.task('task1')['status'], 'awaiting_input')
        self.assertEqual(self.tasks.task('task2')['status'], 'awaiting_input')
        code = approval_id('task1:c1')
        self.submit(id='exact', text='approve ' + code)
        self.assertEqual(self.tasks.task('task1')['status'], 'queued')
        self.assertEqual(self.tasks.task('task2')['status'], 'awaiting_input')
        remote.call_tool.assert_not_called()

    def test_approval_committed_before_notice_and_concurrent_approval_not_overwritten(self):
        self.submit()
        row = self.tasks.claim()
        remote = SimpleNamespace(call_tool=AsyncMock())
        async def workflow(*args):
            try:
                await self.coordinator.call_tool('task1', 'task1:c1', (remote, 'write', 'docs', 'approval'), {}, time.monotonic()+10, [])
            except Approval:
                self.assertEqual(self.tasks.task('task1')['status'], 'awaiting_input')
                self.assertEqual(self.tasks.events()[0]['kind'], 'approval')
                self.submit(id='approve', text='approve')
                raise
        with patch('echo.agent.coordinator.OpenAI'), patch.object(self.coordinator, 'workflow', side_effect=workflow):
            self.coordinator.execute(row)
        self.assertEqual(self.tasks.task('task1')['status'], 'queued')
        self.assertEqual(self.tasks.events(), [])

    def test_memory_failure_does_not_rewrite_completed_task(self):
        self.submit()
        with patch('echo.agent.coordinator.OpenAI'), \
             patch.object(self.coordinator, 'workflow', new=AsyncMock(return_value='Document completed')), \
             patch.object(self.memory, 'remember', side_effect=OSError()):
            self.coordinator.execute(self.tasks.claim())
        self.assertEqual(self.tasks.task('task1')['status'], 'completed')
        self.assertEqual(self.tasks.events()[0]['text'], 'Document completed')

    def test_cancel_cannot_be_overwritten_by_late_completion(self):
        self.submit()
        self.tasks.change('task1', status='waiting')
        self.tasks.cancel('task1')
        self.tasks.finish('task1', 'completed', 'Done')
        self.assertEqual(self.tasks.task('task1')['status'], 'cancelled')
        self.assertEqual(self.tasks.events(), [])

    def test_stable_tool_alias_and_unknown_codex_name_requires_approval(self):
        from contextlib import AsyncExitStack
        from echo.tools.client import discover
        read = SimpleNamespace(name='read', description='Read', input_schema={}, annotations=None)
        start = SimpleNamespace(name='codex_start', description='Run', input_schema={}, annotations=None)
        one = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[read, start])))
        two = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[start, read])))
        async def check():
            with patch('echo.tools.client.configured_servers', return_value={'custom': {'command': 'fake'}}), \
                 patch('echo.tools.client.connect', new=AsyncMock(side_effect=[one, two])):
                async with AsyncExitStack() as stack:
                    tools1, routes1 = await discover(stack, stable=True)
                    tools2, routes2 = await discover(stack, stable=True)
                self.assertEqual({name: route[1:] for name, route in routes1.items()},
                                 {name: route[1:] for name, route in routes2.items()})
                self.assertTrue(all(route[3] == 'approval' for route in routes1.values()))
        asyncio.run(check())

    def test_cancel_between_precheck_and_action_transaction_prevents_tool(self):
        from echo.agent.coordinator import Cancelled
        self.submit()
        self.tasks.claim()
        remote = SimpleNamespace(call_tool=AsyncMock())
        original = self.coordinator.check
        def cancel_after_check(task_id, deadline):
            original(task_id, deadline)
            self.tasks.cancel(task_id)
        with patch.object(self.coordinator, 'check', side_effect=cancel_after_check):
            with self.assertRaises(Cancelled):
                asyncio.run(self.coordinator.call_tool('task1', 'task1:c1',
                    (remote, 'write', 'docs', 'private_write'), {}, time.monotonic()+10, []))
        remote.call_tool.assert_not_called()
        with self.tasks.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM actions').fetchone()[0], 0)
