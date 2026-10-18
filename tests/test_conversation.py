"""Continuous chat, routing and in-flight corrections, with no paid or external calls."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from echo.agent.conversation import Conversation, history
from echo.agent.coordinator import Coordinator, Revised, approval_id
from echo.agent.memory import Memory
from echo.agent.server import create_app
from echo.agent.tasks import Tasks
from echo.runtime.queue import Queue


def response(text='', calls=()):
    return SimpleNamespace(status='completed', output_text=text, output=list(calls),
                           usage=SimpleNamespace(input_tokens=100, output_tokens=30))


class Call:
    type = 'function_call'
    def __init__(self, call_id='call1'):
        self.name, self.call_id, self.arguments = 'write', call_id, '{"title":"Old title"}'
    def model_dump(self, **kwargs):
        return dict(type=self.type, name=self.name, call_id=self.call_id, arguments=self.arguments)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = Tasks(self.path / 'tasks.db')
        self.memory = Memory(self.path / 'memory.db')
        self.coordinator = Coordinator(self.store, self.memory)
        self.chat = Conversation(self.coordinator)
        env = patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '200000', 'ECHO_DAILY_TOKEN_LIMIT': '2000000',
                                      'ECHO_DAILY_USD_LIMIT': '0', 'ECHO_MODEL_PRICES': '{}'})
        env.start()
        self.addCleanup(env.stop)

    def turn(self, key, text, action='reply', reply='Okay.', target=None):
        self.chat.receive(key, 'owner', text)
        row = self.chat.claim()
        self.chat.apply(row, dict(action=action, target=target, response=reply))
        return row

    def deliver(self):
        for row in self.store.outbox():
            self.store.acknowledge(row['id'], 'sent')

    def worker(self, client, routes=None):
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], routes or {}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.store.claim())

    def test_real_roles_full_reply_survive_restart_and_no_task_for_chat(self):
        original = 'Study plan details. ' * 100 + 'The last step is review chapter 9.'
        self.turn('a', 'Write a study plan', reply=original)
        self.deliver()
        self.store = Tasks(self.path / 'tasks.db')
        self.chat = Conversation(Coordinator(self.store, self.memory))
        self.chat.recover()
        self.chat.receive('b', 'owner', 'Make the last step shorter')
        client = Mock()
        client.responses.create.return_value = response(json.dumps(dict(action='reply', target=None, response='Review chapter 9.')))
        with patch('echo.agent.conversation.OpenAI') as factory:
            factory.return_value.__enter__.return_value = client
            self.chat.process(self.chat.claim())
        inputs = client.responses.create.call_args.kwargs['input']
        self.assertIn({'role': 'user', 'content': 'Write a study plan'}, inputs)
        self.assertIn({'role': 'assistant', 'content': original}, inputs)
        self.assertEqual(inputs[-1], {'role': 'user', 'content': 'Make the last step shorter'})
        self.assertEqual(self.store.summary()['tasks'], {})
        self.assertEqual([row['text'] for row in self.store.outbox()], ['Review chapter 9.'])
        self.assertEqual(client.responses.create.call_count, 1)

    def test_pending_uncertain_and_other_owner_are_not_assistant_history(self):
        self.turn('a', 'hello', reply='pending secret')
        self.turn('b', 'again', reply='uncertain secret')
        self.store.acknowledge('b:reply', 'uncertain')
        self.store.submit('other', 'another-owner', 'private other request', 'private other answer', status='control')
        self.store.acknowledge('other:ack', 'sent')
        self.chat.receive('c', 'owner', 'What did I ask?')
        messages = history(self.store, 'owner', 'c')
        self.assertFalse(any(item['role'] == 'assistant' for item in messages))
        self.assertNotIn('private other', json.dumps(messages))
        self.store.acknowledge('a:reply', 'sent')  # Receipt arrives after the current user turn.
        messages = history(self.store, 'owner', 'c')
        self.assertIn({'role': 'assistant', 'content': 'pending secret'}, messages)
        self.assertEqual(messages[-1]['content'], 'What did I ask?')

    def test_migrate_old_delivered_exchanges_once(self):
        self.store.submit('old', 'owner', 'Earlier request', None)
        self.store.finish('old', 'completed', 'Earlier exact answer')
        self.coordinator.events_once()
        self.store.acknowledge('old:final:completed', 'sent')
        with self.store.db() as db:
            db.execute('DELETE FROM conversation_messages')  # Simulate a pre-conversation database.
        for _ in range(2):
            self.store = Tasks(self.path / 'tasks.db')
        messages = history(self.store, 'owner', 'new')
        self.assertEqual(messages, [{'role': 'user', 'content': 'Earlier request'},
                                    {'role': 'assistant', 'content': 'Earlier exact answer'}])

    def test_legacy_service_context_is_imported_without_resending_replies(self):
        queue = Queue(self.path / 'legacy.db')
        queue.put('old', {'sender': 'owner', 'text': 'Help me with biology'})
        queue.update('old', 'completed', result='Earlier biology answer')
        queue.put('pending', {'sender': 'owner', 'text': 'Still waiting'})
        for _ in range(2):
            self.chat.import_legacy(queue)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.outbox(), [])
        self.assertEqual(self.store.events(), [])
        self.assertIsNone(self.store.task('pending'))
        self.assertEqual(self.store.task('old')['result'], 'Earlier biology answer')
        self.assertEqual(history(self.store, 'owner', 'new'), [{'role': 'user', 'content': 'Help me with biology'}])

    def test_rapid_messages_are_ordered_and_not_lost_on_restart(self):
        self.chat.receive('a', 'owner', 'First')
        self.chat.receive('b', 'owner', 'Second')
        first = self.chat.claim()
        self.assertEqual(first['id'], 'a')
        self.assertNotIn('Second', json.dumps(history(self.store, 'owner', 'a')))
        self.chat.recover()
        first = self.chat.claim()
        self.chat.apply(first, dict(action='start', target=None, response='I will check.'))
        self.chat.apply(first, dict(action='start', target=None, response='I will check.'))
        self.assertEqual(self.chat.receive('a', 'owner', 'First')['status'], 'handled')
        self.assertEqual(self.chat.claim()['id'], 'b')
        self.assertEqual(self.store.summary()['tasks'], {'queued': 1})
        self.assertEqual(len(self.store.outbox()), 1)
        with self.assertRaises(ValueError):
            self.chat.receive('a', 'owner', 'Different')

    def test_v2_uses_conversation_and_enforces_owner(self):
        config = dict(owner='owner', token='t' * 40)
        wake = threading.Event()
        app = create_app(config, Queue(self.path / 'legacy.db'), threading.Event(), self.coordinator, self.chat, wake)
        client = app.test_client()
        auth = {'Authorization': 'Bearer ' + config['token']}
        self.assertEqual(client.post('/v2/messages', json=dict(id='x', sender='other', text='Hi'), headers=auth).status_code, 403)
        for _ in range(2):
            self.assertEqual(client.post('/v2/messages', json=dict(id='x', sender='owner', text='Hi'), headers=auth).status_code, 200)
        self.assertTrue(wake.is_set())
        self.assertEqual(self.store.outbox(), [])
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.chat.claim()['id'], 'x')
        self.assertIsNone(self.chat.claim())
        self.assertIn('conversation-v1', client.get('/v1/health', headers=auth).json['capabilities'])

    def test_worker_has_conversation_to_resolve_completed_task_followup(self):
        self.turn('a', 'Draft a study plan', reply='Biology draft with its exact last paragraph.')
        self.deliver()
        self.turn('b', 'Put that in a Google Doc', action='start', reply='I will create the document.')
        client = Mock()
        client.responses.create.side_effect = [response('Check document tools.'), response('Document tools are unavailable.')]
        self.worker(client)
        inputs = client.responses.create.call_args_list[0].kwargs['input']
        self.assertIn({'role': 'assistant', 'content': 'Biology draft with its exact last paragraph.'}, inputs)
        self.assertEqual(self.store.task('b')['status'], 'completed')

    def test_followup_keeps_task_and_changes_worker_instructions(self):
        self.turn('a', 'Create a document', action='start')
        self.turn('b', 'Call it Biology instead', action='continue', target='a')
        self.assertIsNone(self.store.task('b'))
        client = Mock()
        client.responses.create.side_effect = [response('Use the new title.'), response('I need a Docs connection.')]
        self.worker(client)
        inputs = client.responses.create.call_args_list[0].kwargs['input']
        self.assertIn({'role': 'user', 'content': 'Call it Biology instead'}, inputs)
        self.assertEqual(self.store.task('a')['seen_revision'], 1)
        self.assertEqual(self.store.summary()['tasks'], {'completed': 1})

    def test_followup_while_model_is_running_discards_stale_tool_call(self):
        self.turn('a', 'Create a document', action='start')
        remote = SimpleNamespace(call_tool=AsyncMock())
        calls = 0
        def model(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return response('Plan')
            if calls == 2:
                self.turn('b', 'Actually just explain how', action='continue', target='a')
                return response(calls=[Call()])
            self.assertIn({'role': 'user', 'content': 'Actually just explain how'}, kwargs['input'])
            return response('Here is how to create it.')
        client = Mock()
        client.responses.create.side_effect = model
        self.worker(client, {'write': (remote, 'create_doc', 'docs', 'private_write')})
        remote.call_tool.assert_not_called()
        self.assertEqual(self.store.task('a')['result'], 'Here is how to create it.')

    def test_tool_start_and_final_result_check_revision_atomically(self):
        self.turn('a', 'Create a document', action='start')
        self.store.claim()
        self.turn('b', 'Use another title', action='continue', target='a')
        remote = SimpleNamespace(call_tool=AsyncMock())
        with self.assertRaises(Revised):
            asyncio.run(self.coordinator.call_tool('a', 'a:call', (remote, 'create', 'docs', 'private_write'),
                                                   {}, time.monotonic() + 10, [], revision=0))
        remote.call_tool.assert_not_called()
        self.assertFalse(self.store.finish('a', 'completed', 'Old answer', revision=0))
        self.assertEqual(self.store.task('a')['status'], 'queued')
        self.assertEqual(self.store.events(), [])

    def test_followup_revokes_old_approval_and_preserves_valid_tool_history(self):
        self.turn('a', 'Create a document', action='start')
        self.store.change('a', status='awaiting_input', history=json.dumps([Call().model_dump()]))
        with self.store.db() as db:
            db.execute('INSERT INTO actions VALUES (?,?,?,?,?,?,?,?)',
                       ('a:call1', 'a', 'docs', 'create', '{}', 'approval_required', None, time.time()))
        self.store.event('a:call1:approval', 'a', 'approval', 'Old approval', promised=True)
        self.coordinator.events_once()
        self.turn('b', 'Use the title Biology instead', action='continue', target='a')
        self.coordinator.receive('approval', 'owner', 'approve ' + approval_id('a:call1'))
        self.assertIn('No matching', self.store.task('approval')['result'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM actions').fetchone()[0], 'superseded')
        self.assertEqual(self.store.task('a')['status'], 'queued')
        self.assertNotIn('Old approval', [row['text'] for row in self.store.outbox()])
        saved = json.loads(self.store.task('a')['history'])
        self.assertEqual(saved[-1]['type'], 'function_call_output')

    def test_followup_does_not_restart_inflight_codex(self):
        self.turn('a', 'Ask Codex to build it', action='start')
        self.store.change('a', status='waiting')
        with self.store.db() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', ('job', 'a', 'codex', 'j1', 'running', None, 0))
        self.turn('b', 'Make the title Biology', action='continue', target='a')
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.task('a')['status'], 'waiting')
        self.assertIn("haven't changed it", self.store.outbox()[-1]['text'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_waiting_job_followup_survives_restart_and_resumes_once(self):
        self.turn('a', 'Ask Codex to build it', action='start')
        self.store.change('a', status='waiting', plan='Use Codex',
                          history=json.dumps([{'role': 'user', 'content': 'Ask Codex to build it'}]))
        with self.store.db() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', ('job', 'a', 'codex', 'j1', 'completed', '{"result":"built"}', 0))
        self.turn('b', 'Explain the result in one sentence', action='continue', target='a')
        self.store = Tasks(self.path / 'tasks.db')
        self.coordinator = Coordinator(self.store, self.memory)
        self.store.change('a', status='queued')  # Authoritative completion monitor resumes it.
        client = Mock()
        client.responses.create.return_value = response('Built the requested feature.')
        self.worker(client)
        messages = client.responses.create.call_args.kwargs['input']
        self.assertEqual(sum(item.get('content') == 'Explain the result in one sentence' for item in messages), 1)
        self.assertEqual(self.store.task('a')['status'], 'completed')
        self.assertIsNone(self.store.claim())
        self.assertEqual(client.responses.create.call_count, 1)

    def test_approved_but_not_started_action_is_revoked_by_correction(self):
        self.turn('a', 'Create a document', action='start')
        self.store.change('a', history=json.dumps([Call().model_dump()]), plan='Create it')
        with self.store.db() as db:
            db.execute('INSERT INTO actions VALUES (?,?,?,?,?,?,?,?)',
                       ('a:call1', 'a', 'docs', 'create', '{}', 'approved', None, time.time()))
        self.turn('b', 'Wait, just explain it', action='continue', target='a')
        remote = SimpleNamespace(call_tool=AsyncMock())
        client = Mock()
        client.responses.create.return_value = response('Here is the explanation.')
        self.worker(client, {'write': (remote, 'create', 'docs', 'approval')})
        remote.call_tool.assert_not_called()
        self.assertEqual(self.store.task('a')['result'], 'Here is the explanation.')

    def test_history_window_is_bounded_without_clipping_messages(self):
        for index in range(30):
            self.turn(str(index), 'Question ' + str(index), reply=str(index) + ':' + 'x' * 1000)
            self.deliver()
        self.chat.receive('last', 'owner', 'Shorten that')
        messages = history(self.store, 'owner', 'last')
        self.assertLessEqual(sum(len(item['content']) for item in messages), 24000)
        self.assertIn({'role': 'assistant', 'content': '29:' + 'x' * 1000}, messages)
        self.assertEqual(messages[-1]['content'], 'Shorten that')
        self.assertNotIn({'role': 'user', 'content': 'Question 0'}, messages)

    def test_direct_controls_and_idle_do_not_call_model(self):
        self.chat.receive('a', 'owner', 'status')
        with patch('echo.agent.conversation.OpenAI') as model:
            self.chat.process(self.chat.claim())
            wake, work, stop = threading.Event(), threading.Event(), threading.Event()
            thread = threading.Thread(target=self.chat.run, args=(wake, work, stop))
            thread.start()
            stop.set()
            wake.set()
            thread.join(2)
            model.assert_not_called()
        self.assertEqual(self.store.task('a')['status'], 'control')

    def test_invalid_decision_does_not_create_work(self):
        self.chat.receive('a', 'owner', 'Change that')
        client = Mock()
        client.responses.create.return_value = response(json.dumps(dict(action='continue', target='missing', response='I will update it.')))
        with patch('echo.agent.conversation.OpenAI') as factory:
            factory.return_value.__enter__.return_value = client
            self.chat.process(self.chat.claim())
        self.assertIsNone(self.store.claim())
        self.assertIn("couldn't process", self.store.outbox()[0]['text'])


if __name__ == '__main__':
    unittest.main()
