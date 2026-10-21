"""Offline budget reservations and durable notification retry behavior."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from echo.agent.budget import Budget, BudgetExceeded
from echo.agent.coordinator import Coordinator
from echo.agent.tasks import Tasks


class BudgetEventTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Tasks(Path(self.folder.name) / 'state.sqlite3')
        self.env = patch.dict(os.environ, {
            'ECHO_MODEL_PRICES': '{}', 'ECHO_DAILY_USD_LIMIT': '0',
            'ECHO_TASK_TOKEN_LIMIT': '64000', 'ECHO_DAILY_TOKEN_LIMIT': '250000',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_limit_blocks_before_network_and_reservations_survive_failure(self):
        client = Mock()
        client.responses.create.side_effect = TimeoutError()
        budget = Budget(self.store, 'one')
        with self.assertRaises(TimeoutError):
            budget.create(client, model='test', input='hello', max_output_tokens=100)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM usage').fetchone()[0], 'reserved')
        with patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '100'}):
            with self.assertRaises(BudgetExceeded):
                budget.create(client, model='test', input='hello', max_output_tokens=100)
        self.assertEqual(client.responses.create.call_count, 1)

    def test_default_allows_large_tool_context_but_preserves_daily_cap(self):
        # Tool schemas are conservatively reserved as bytes, not actual billed tokens.
        os.environ.pop('ECHO_TASK_TOKEN_LIMIT', None)
        budget = Budget(self.store, 'reminder')
        previous = budget.reserve('test', {}, 100)
        budget.record(previous, SimpleNamespace(usage=SimpleNamespace(input_tokens=10000, output_tokens=100)))
        budget.reserve('test', {'tools': 'x' * 80000}, 1200)
        with patch.dict(os.environ, {'ECHO_DAILY_TOKEN_LIMIT': '92000'}):
            with self.assertRaisesRegex(BudgetExceeded, 'Daily token budget'):
                Budget(self.store, 'another').reserve('test', {}, 1200)

    def test_task_limit_message_identifies_the_limit(self):
        with patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '1000'}):
            with self.assertRaisesRegex(BudgetExceeded, 'Task token budget'):
                Budget(self.store, 'one').reserve('test', {}, 100)

    def test_prices_are_required_and_invalid_rates_are_rejected(self):
        budget = Budget(self.store, 'one')
        with patch.dict(os.environ, {'ECHO_DAILY_USD_LIMIT': '1'}):
            with self.assertRaises(BudgetExceeded):
                budget.reserve('unknown', {}, 10)
        for value in (-1, float('nan'), float('inf'), 'not a price'):
            with patch.dict(os.environ, {'ECHO_MODEL_PRICES': json.dumps({'test': {'input': value, 'output': 1}})}):
                with self.assertRaises(BudgetExceeded):
                    budget.reserve('test', {}, 10)

    def test_dollar_cap_counts_other_tasks_and_unknown_previous_cost(self):
        Budget(self.store, 'old').reserve('unknown', {}, 10)
        with patch.dict(os.environ, {'ECHO_DAILY_USD_LIMIT': '1',
                                    'ECHO_MODEL_PRICES': '{"test":{"input":1,"output":2}}'}):
            with self.assertRaisesRegex(BudgetExceeded, 'Earlier usage'):
                Budget(self.store, 'new').reserve('test', {}, 10)
        with self.store.db() as db:
            db.execute('DELETE FROM usage')
        with patch.dict(os.environ, {'ECHO_DAILY_USD_LIMIT': '0.002',
                                    'ECHO_MODEL_PRICES': '{"test":{"input":1,"output":2}}'}):
            Budget(self.store, 'one').reserve('test', {}, 10)
            with self.assertRaisesRegex(BudgetExceeded, 'Daily spending'):
                Budget(self.store, 'two').reserve('test', {}, 10)

    def test_record_uses_reservation_price_not_changed_configuration(self):
        budget = Budget(self.store, 'one')
        with patch.dict(os.environ, {'ECHO_MODEL_PRICES': '{"test":{"input":1,"output":2}}'}):
            reservation = budget.reserve('test', {}, 100)
        budget.record(reservation, SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=10)))
        with self.store.db() as db:
            row = db.execute('SELECT * FROM usage').fetchone()
            self.assertAlmostEqual(row['dollars'], .00012)
            self.assertEqual(row['status'], 'reported')

    def test_failed_decisions_back_off_and_do_not_block_promised_results(self):
        self.store.event('optional', 'one', 'changed', 'Some update')
        self.store.event('promised', 'two', 'completed', 'Finished', promised=True)
        coordinator = Coordinator(self.store, Mock())
        with patch('echo.agent.coordinator.OpenAI', side_effect=RuntimeError('offline')) as api:
            coordinator.events_once()
            coordinator.events_once()
            api.assert_called_once()
        self.assertEqual([row['id'] for row in self.store.outbox()], ['promised'])
        reopened = Tasks(self.store.path)
        self.assertEqual(reopened.events(), [])
        with self.store.db() as db:
            row = db.execute('SELECT attempts,due FROM events WHERE id="optional"').fetchone()
            self.assertEqual(row['attempts'], 1)
            self.assertGreater(row['due'], 0)
        for _ in range(4):
            reopened.defer_event('optional')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM events WHERE id="optional"').fetchone()[0], 'deferred')

    def test_duplicate_event_has_one_notification_and_heartbeat_is_free(self):
        for _ in range(2):
            self.store.event('done', 'one', 'completed', 'Done', promised=True)
        self.store.event('beat', 'one', 'heartbeat', 'Still running')
        with patch('echo.agent.coordinator.OpenAI') as api:
            Coordinator(self.store, Mock()).events_once()
            api.assert_not_called()
        self.assertEqual([row['id'] for row in self.store.outbox()], ['done'])

    def test_legacy_server_messages_share_budget_and_disable_hidden_retries(self):
        from echo.agent.server import process_one
        from echo.runtime.queue import Queue
        legacy = Queue(Path(self.folder.name) / 'legacy.sqlite3')
        legacy.put('message', {'text': 'hello', 'sender': 'owner'})
        with patch('echo.agent.processor.load_env'), patch('echo.agent.processor.load_dotenv'), \
             patch.dict(os.environ, {'OPENAI_API_KEY': 'test', 'ECHO_MCP_ENABLED': '0',
                                     'ECHO_DAILY_TOKEN_LIMIT': '1'}), \
             patch('echo.agent.processor.OpenAI') as factory:
            process_one(legacy, legacy.get('message'), self.store)
            factory.assert_called_once_with(timeout=30.0, max_retries=0)
            factory.return_value.__enter__.return_value.responses.create.assert_not_called()
        self.assertEqual(legacy.get('message')['status'], 'failed')

    def test_legacy_tool_rounds_all_use_reservations(self):
        import asyncio
        from unittest.mock import AsyncMock
        from openai.types.responses import ResponseFunctionToolCall
        from echo.tools.client import respond
        call = ResponseFunctionToolCall(type='function_call', name='read', arguments='{}', call_id='one')
        client = Mock()
        client.responses.create.side_effect = [
            SimpleNamespace(status='completed', output=[call], output_text=''),
            SimpleNamespace(status='completed', output=[], output_text='Finished'),
        ]
        remote = Mock()
        remote.call_tool = AsyncMock(return_value=Mock(model_dump=Mock(return_value={'content': []})))
        with patch('echo.tools.client.discover', new=AsyncMock(return_value=([], {'read': (remote, 'read')}))):
            result = asyncio.run(respond(client, 'read', '', 'test', budget=Budget(self.store, 'legacy:one')))
        self.assertEqual(result, 'Finished')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 2)
