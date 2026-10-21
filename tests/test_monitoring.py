"""Monitoring uses fake tools and models; never accesses an account or sends messages."""
import json
from unittest.mock import AsyncMock, Mock, patch

import unittest
import test_schedules
from test_always_on import Call, response, tool_result
from echo.agent import monitoring
from echo.agent.schedules import Schedules
from echo.agent.tasks import Tasks


class MonitoringTests(unittest.TestCase):
    setUp = test_schedules.ScheduleTests.setUp

    def start_monitor(self):
        self.saved = self.schedules.call('monitor_create', dict(title='Calendar', timezone='America/New_York',
            cron='*/5 * * * *', prompt='Read School calendar; ask about changed deadlines. Never edit events.'),
            'request', 'request:monitor', self.now)
        self.store.finish('request', 'completed', 'Saved')
        self.schedules.tick(self.now + 300)
        return self.store.claim()

    def complete(self, row, alerts=()):
        monitoring.finish(self.schedules, row['id'], json.dumps({'state': {'cursor': 'next'}, 'alerts': list(alerts)}), 0)

    def test_quiet_state_survives_restart_and_duplicate_alert_is_suppressed(self):
        row = self.start_monitor()
        self.complete(row)
        reopened = Schedules(Tasks(self.store.path))
        self.assertEqual(monitoring.context(reopened, row['id'])['state'], {'cursor': 'next'})
        self.assertFalse(any(n['task_id'] == row['id'] for n in self.store.outbox()))
        alert = {'key': 'school:event1:v2:conflict', 'text': 'Your meeting moved. Want help rescheduling?'}
        for offset in (600, 900):
            reopened.tick(self.now + offset)
            run = self.store.claim()
            self.complete(run, [alert])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notifications WHERE id LIKE '%:finding:%'").fetchone()[0], 1)

    def test_cancelled_monitor_cannot_publish_or_advance_state(self):
        row = self.start_monitor()
        self.store.submit('cancel', 'owner', 'Stop monitoring', None)
        self.schedules.call('schedule_cancel', {'id': self.saved['id']}, 'cancel', 'cancel:1')
        self.complete(row, [{'key': 'a', 'text': 'Do something?'}])
        self.assertEqual(monitoring.context(self.schedules, row['id'])['state'], {})
        self.assertEqual(self.store.task(row['id'])['status'], 'cancelled')
        self.assertFalse(any(n['task_id'] == row['id'] for n in self.store.outbox()))

    def test_invalid_result_never_advances_state(self):
        row = self.start_monitor()
        with self.assertRaises(ValueError):
            monitoring.finish(self.schedules, row['id'], '{"state":{},"alerts":[{"text":"oops"}]}', 0)
        self.assertEqual(monitoring.context(self.schedules, row['id'])['state'], {})

    def test_followup_invalidates_result(self):
        row = self.start_monitor()
        with self.store.db() as db:
            db.execute('UPDATE tasks SET revision=1 WHERE id=?', (row['id'],))
        self.complete(row, [{'key': 'a', 'text': 'Do something?'}])
        self.assertEqual(self.store.task(row['id'])['status'], 'queued')
        self.assertEqual(monitoring.context(self.schedules, row['id'])['state'], {})

    def test_actual_worker_reads_unfamiliar_mcp_and_delivers_only_finding(self):
        row = self.start_monitor()
        remote = Mock()
        remote.call_tool = AsyncMock(return_value=tool_result({'events': [{'id': '1', 'version': 2}]}))
        tools = [dict(type='function', name=n, description=n, parameters={'type': 'object', 'properties': {}})
                 for n in ('read_calendar', 'edit_calendar')]
        routes = {'read_calendar': (remote, 'list_events', 'unfamiliar', 'read'),
                  'edit_calendar': (remote, 'update_event', 'unfamiliar', 'private_write')}
        client = Mock()
        client.responses.create.side_effect = [response('Read current events.'),
            response(calls=[Call('read_calendar')]),
            response(json.dumps({'state': {'cursor': '2'}, 'alerts': [{'key': 'event1:v2', 'text': 'Meeting moved. Want help?'}]}))]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=(tools, routes))), \
             patch('echo.agent.coordinator.review', return_value={'allow': True}) as review:
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(row)
        remote.call_tool.assert_called_once_with('list_events', {})
        review.assert_called_once()
        self.assertEqual(self.store.task(row['id'])['status'], 'completed')
        self.assertEqual([n['text'] for n in self.store.outbox() if n['task_id'] == row['id']], ['Meeting moved. Want help?'])
        offered = client.responses.create.call_args.kwargs['tools']
        self.assertEqual([t['name'] for t in offered], ['read_calendar'])

    def test_failed_monitor_pauses(self):
        row = self.start_monitor()
        self.store.finish(row['id'], 'failed', 'Source unavailable')
        monitoring.pause_failed(self.schedules, row['id'])
        self.assertFalse(self.schedules.listing('owner')[0]['enabled'])
        self.assertEqual(self.schedules.tick(self.now + 600), 0)

    def test_worker_without_source_read_fails_and_pauses_instead_of_silent_success(self):
        row = self.start_monitor()
        client = Mock()
        client.responses.create.side_effect = [response('No tools available.'),
            response('{"state":{},"alerts":[]}')]
        with patch('echo.agent.coordinator.OpenAI') as factory, \
             patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {}))):
            factory.return_value.__enter__.return_value = client
            self.coordinator.execute(row)
        self.assertEqual(self.store.task(row['id'])['status'], 'failed')
        self.assertIn('Monitoring is paused', self.store.task(row['id'])['result'])
        self.assertFalse(self.schedules.listing('owner')[0]['enabled'])
