"""Timers use temporary state and mocked models/transports; never send real messages."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from echo.agent.coordinator import Approval, Coordinator
from echo.agent.conversation import Conversation, context
from echo.agent.memory import Memory
from echo.agent.schedules import Schedules, next_cron
from echo.agent.tasks import Tasks
from test_always_on import Call, response, tool_result


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Tasks(Path(temp.name) / 'tasks.db')
        self.coordinator = Coordinator(self.store, Memory(Path(temp.name) / 'memory.db'))
        self.schedules = self.coordinator.schedules
        self.store.submit('request', 'owner', 'At 7 PM remind me to study and create a study guide.', None)
        self.store.claim()
        self.now = datetime.fromisoformat('2026-10-18T12:00:00-04:00').timestamp()
        self.due = datetime.fromisoformat('2026-10-18T19:00:00-04:00').timestamp()
        self.args = dict(title='Chemistry', timezone='America/New_York', at='2026-10-18T19:00:00-04:00',
                         cron=None, reminder='Time to study AP Chemistry.', prompt='Create an AP Chemistry study guide using Codex.')
        env = patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '200000', 'ECHO_DAILY_TOKEN_LIMIT': '500000',
                                      'ECHO_DAILY_USD_LIMIT': '0', 'ECHO_MODEL_PRICES': '{}',
                                      'ECHO_TOOL_CONFIRMATIONS': '1'})
        env.start(); self.addCleanup(env.stop)

    def create(self, **changes):
        return self.schedules.call('schedule_create', {**self.args, **changes}, 'request', 'request:call1', self.now)

    def runs(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM schedule_runs ORDER BY due')]

    def test_one_time_reminder_fires_once_after_restart_without_model(self):
        saved = self.create(prompt='')
        self.assertEqual(saved['next_run'], self.args['at'])
        self.assertEqual(self.schedules.tick(self.due - 1), 0)
        reopened = Schedules(Tasks(self.store.path))
        with patch('echo.agent.coordinator.OpenAI') as api:
            self.assertEqual(reopened.tick(self.due), 1)
            self.assertEqual(reopened.tick(self.due + 1), 0)
            api.assert_not_called()
        self.assertEqual(len(self.store.outbox()), 1)
        self.assertEqual(self.store.outbox()[0]['text'], self.args['reminder'])
        self.assertEqual(self.store.task(self.runs()[0]['task_id'])['status'], 'completed')
        self.assertFalse(reopened.listing('owner')[0]['enabled'])

    def test_concurrent_ticks_and_create_replay_do_not_duplicate(self):
        self.assertEqual(self.create()['id'], self.create()['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(self.schedules.tick, [self.due, self.due]))
        self.assertEqual(sum(counts), 1)
        self.assertEqual(len(self.runs()), 1)

    def test_interruption_rolls_back_cursor_and_occurrence_together(self):
        self.create()
        with self.store.db() as db:
            db.execute("CREATE TRIGGER interrupt_run BEFORE INSERT ON tasks WHEN NEW.id LIKE 'schedule:%' "
                       "BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.schedules.tick(self.due)
        self.assertEqual(self.runs(), [])
        self.assertTrue(self.schedules.listing('owner')[0]['enabled'])
        with self.store.db() as db:
            db.execute('DROP TRIGGER interrupt_run')
        self.assertEqual(self.schedules.tick(self.due), 1)

    def test_schedule_edits_keep_authorization_and_restart_does_not_invent_user_turn(self):
        saved = self.create()
        self.store.submit('edit', 'owner', 'Move the chemistry reminder to tomorrow at 8 PM.', None)
        args = {**self.args, 'id': saved['id'], 'at': '2026-10-19T20:00:00-04:00'}
        self.schedules.call('schedule_update', args, 'edit', 'edit:c1', self.now)
        due = datetime.fromisoformat(args['at']).timestamp()
        self.schedules.tick(due)
        run = self.runs()[0]['task_id']
        original = self.schedules.origin(run)['authorization']
        self.assertIn('At 7 PM', original['request'])
        self.assertIn('Move the chemistry', original['amendments'][0]['request'])
        reopened = Tasks(self.store.path)
        with reopened.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM conversation_messages WHERE id=?', ('user:'+run,)).fetchone())

    def test_recurring_downtime_coalesces_and_active_work_does_not_overlap(self):
        self.create(at=None, cron='0 19 * * *')
        self.schedules.tick(self.due + 86400 * 3)
        self.assertEqual(len(self.runs()), 1)
        first = self.runs()[0]['task_id']
        self.schedules.tick(self.due + 86400 * 4)
        self.assertEqual(len(self.runs()), 1)
        self.store.change(first, status='completed')
        self.schedules.tick(self.due + 86400 * 5)
        self.assertEqual(len(self.runs()), 2)

    def test_cancel_stops_future_and_queued_work_and_unsent_reminder(self):
        saved = self.create(at=None, cron='0 19 * * *')
        self.schedules.tick(self.due)
        self.schedules.call('schedule_cancel', {'id': saved['id']}, 'request', 'request:c2', self.due + 1)
        self.assertEqual(self.store.task(self.runs()[0]['task_id'])['status'], 'cancelled')
        self.assertEqual(self.store.outbox(), [])
        self.assertEqual(self.schedules.tick(self.due + 86400), 0)

    def test_cancel_preserves_running_work_and_enforces_owner(self):
        saved = self.create()
        self.store.submit('other', 'stranger', 'Cancel it', None, status='control')
        self.assertEqual(self.schedules.listing('stranger'), [])
        with self.assertRaises(ValueError):
            self.schedules.call('schedule_cancel', {'id': saved['id']}, 'other', 'other:c1', self.now)
        self.schedules.tick(self.due)
        self.store.claim()
        self.schedules.call('schedule_cancel', {'id': saved['id']}, 'request', 'request:c2', self.due + 1)
        self.assertEqual(self.store.task(self.runs()[0]['task_id'])['status'], 'running')

    def test_cancel_also_invalidates_occurrence_waiting_for_approval(self):
        saved = self.create()
        self.schedules.tick(self.due)
        task_id = self.runs()[0]['task_id']
        self.store.change(task_id, status='awaiting_input')
        with self.store.db() as db:
            db.execute('INSERT INTO actions VALUES (?,?,?,?,?,?,?,?)',
                       (task_id+':a', task_id, 'codex', 'codex_start', '{}', 'approval_required', None, self.due))
        self.store.event(task_id+':approval', task_id, 'approval', 'Confirm this action.', promised=True)
        self.schedules.call('schedule_cancel', {'id': saved['id']}, 'request', 'request:c2', self.due + 1)
        self.assertEqual(self.store.task(task_id)['status'], 'cancelled')
        self.assertEqual(self.store.events(), [])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM actions').fetchone()[0], 'cancelled')

    def test_update_replaces_future_time_preserves_original_run_snapshot(self):
        saved = self.create()
        self.schedules.tick(self.due)
        old = self.schedules.origin(self.runs()[0]['task_id'])
        changed = {**self.args, 'id': saved['id'], 'at': '2026-10-19T20:00:00-04:00', 'prompt': 'Revise the guide.'}
        result = self.schedules.call('schedule_update', changed, 'request', 'request:c2', self.due + 1)
        self.assertEqual(result['next_run'], changed['at'])
        self.assertEqual(self.schedules.origin(self.runs()[0]['task_id']), old)
        self.assertEqual(self.schedules.listing('owner')[0]['prompt'], changed['prompt'])

    def test_invalid_or_past_times_do_not_save(self):
        for change in [dict(at='2026-10-18T19:00:00'), dict(at='2026-10-17T19:00:00-04:00'),
                       dict(at='2026-10-18T19:00:00-05:00'), dict(cron='* * * * *'),
                       dict(at=None, cron=None), dict(at=None, cron='0 0 31 2 *'),
                       dict(at=None, cron='* * * * * *'), dict(timezone='Not/AZone'),
                       dict(reminder='', prompt='')]:
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                self.create(**change)
        self.assertEqual(self.schedules.listing('owner'), [])

    def test_timezone_wall_clock_survives_spring_and_fall_dst(self):
        zone = ZoneInfo('America/New_York')
        for before, after in [('2026-03-07T09:00:00-05:00', '2026-03-08T09:00:00-04:00'),
                              ('2026-10-31T09:00:00-04:00', '2026-11-01T09:00:00-05:00')]:
            result = next_cron('0 9 * * *', zone.key, datetime.fromisoformat(before).timestamp())
            self.assertEqual(datetime.fromtimestamp(result, zone).isoformat(), after)

    def test_scheduler_is_gated_and_validation_errors_are_recoverable(self):
        route = self.schedules.tools()[1]['schedule_create']
        args = {**self.args, 'at': datetime.fromtimestamp(time.time() + 3600, ZoneInfo(self.args['timezone'])).isoformat()}
        async def act(key, values):
            return await self.coordinator.call_tool('request', key, route, values, time.monotonic()+10, [], rationale='Requested reminder.')
        with patch('echo.agent.coordinator.review', return_value={'allow': False, 'reason': 'Confirm time'}):
            with self.assertRaises(Approval):
                asyncio.run(act('request:denied', args))
        self.assertEqual(self.schedules.listing('owner'), [])
        self.store.change('request', status='running')
        with patch('echo.agent.coordinator.review', return_value={'allow': True}):
            result, _ = asyncio.run(act('request:invalid', {**args, 'at': 'yesterday'}))
            self.assertFalse(result['structuredContent']['saved'])
            result, _ = asyncio.run(act('request:valid', args))
            self.assertTrue(result['structuredContent']['saved'])

    def test_due_work_enters_existing_codex_tracking_with_original_authorization(self):
        self.create()
        self.schedules.tick(self.due)
        row = self.store.claim()
        client = Mock()
        client.responses.create.side_effect = [response('Create the guide with Codex.'),
            response(calls=[Call('codex', args={'task': 'Create an AP Chemistry study guide.'})])]
        remote = Mock()
        remote.call_tool = AsyncMock(return_value=tool_result({'job_id': 'job123', 'status': 'queued'}))
        routes = {'codex': (remote, 'codex_start', 'echo_codex', 'private_write')}
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], routes))), \
             patch('echo.agent.coordinator.review', return_value={'allow': True}) as gate:
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(row)
        self.assertEqual(self.store.task(row['id'])['status'], 'waiting')
        remote.call_tool.assert_called_once()
        gate.assert_called_once()
        data = context(self.store, self.coordinator.memory, row)['content']
        self.assertIn('At 7 PM remind me', data)
        self.assertIn('scheduled_occurrence', data)
        supplied = client.responses.create.call_args.kwargs['input']
        self.assertTrue(any('Automatic scheduled occurrence' in m.get('content', '') for m in supplied if isinstance(m, dict)))

    def test_conversation_can_route_and_worker_can_create_schedule_without_external_mcp(self):
        conv = Conversation(self.coordinator)
        conv.receive('text', 'owner', 'Remind me tomorrow to study.')
        turn = conv.claim()
        conv.apply(turn, {'action': 'start', 'target': None, 'response': 'I will set that reminder.'})
        row = self.store.claim()
        args = {**self.args, 'at': datetime.fromtimestamp(time.time()+86400, ZoneInfo('America/New_York')).isoformat(), 'prompt': ''}
        client = Mock()
        client.responses.create.side_effect = [response('Save the reminder.'),
            response(calls=[Call('schedule_create', args=args)]), response('Reminder saved.')]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {}))), \
             patch('echo.agent.coordinator.review', return_value={'allow': True}):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(row)
        self.assertEqual(self.store.task('text')['status'], 'completed')
        self.assertEqual(len(self.schedules.listing('owner')), 1)
        tools = client.responses.create.call_args.kwargs['tools']
        self.assertIn('schedule_create', [tool['name'] for tool in tools])

    def test_two_daily_assignment_schedules_complete_in_one_reply(self):
        self.store.submit('daily', 'owner',
            'Every day at 7 AM list today assignments and tests, and at 2:30 PM list tomorrow assignments and tests.', None)
        morning = {**self.args, 'title': 'Today assignments and tests', 'at': None,
                   'cron': '0 7 * * *', 'reminder': '',
                   'prompt': 'List my Canvas assignments and tests due today.'}
        afternoon = {**morning, 'title': 'Tomorrow assignments and tests', 'cron': '30 14 * * *',
                     'prompt': 'List my Canvas assignments and tests due tomorrow.'}
        client = Mock()
        client.responses.create.side_effect = [response('Save both daily schedules.'),
            response(calls=[Call('schedule_create', 'morning', morning)]),
            response(calls=[Call('schedule_create', 'afternoon', afternoon)]),
            response('Saved both daily assignment and test schedules.')]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {}))), \
             patch('echo.agent.coordinator.review', return_value={'allow': True}):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(self.store.claim())
        self.coordinator.events_once()
        self.assertEqual({row['cron'] for row in self.schedules.listing('owner')},
                         {'0 7 * * *', '30 14 * * *'})
        self.assertEqual([row['text'] for row in self.store.outbox()],
                         ['Saved both daily assignment and test schedules.'])


if __name__ == '__main__':
    unittest.main()
