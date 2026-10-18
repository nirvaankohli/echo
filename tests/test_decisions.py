"""Decision API wire format and the gate before any MCP side effect."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
import jsonschema
from openai import OpenAI

from echo.agent.coordinator import Approval, Cancelled, Coordinator, Revised
from echo.agent.decisions import proposal_tools, review
from echo.agent.tasks import Tasks


class DecisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Tasks(Path(temp.name) / 'tasks.db')
        self.store.submit('task', 'owner', 'Write an essay and save it locally.', None)
        self.store.change('task', plan='Create one new file in the configured workspace.')
        self.worker = Coordinator(self.store, Mock())
        self.remote = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
            model_dump=lambda **kwargs: {'content': [{'type': 'text', 'text': 'saved'}]})))
        self.route = (self.remote, 'write_file', 'files', 'approval')
        self.requests = []
        self.answer = dict(name='action_gate', type='choice', choice='allow', confidence=0.99)
        env = patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '200000', 'ECHO_DAILY_TOKEN_LIMIT': '500000',
                                      'ECHO_DAILY_USD_LIMIT': '0', 'ECHO_MODEL_PRICES': '{}', 'OPENAI_DECISION_MODEL': 'gpt-6-luna'})
        env.start()
        self.addCleanup(env.stop)
        def factory(**kwargs):
            return OpenAI(api_key='test-only', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(self.respond)))
        sdk = patch('echo.agent.decisions.OpenAI', side_effect=factory)
        sdk.start()
        self.addCleanup(sdk.stop)

    def respond(self, request):
        self.requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'answers': [self.answer], 'usage': {'input_tokens': 100}})

    def act(self, route=None, args=None):
        return asyncio.run(self.worker.call_tool('task', 'task:c1', route or self.route,
            args or {'path': 'essay.txt'}, time.monotonic() + 10, [], rationale='Create the requested essay file locally.'))

    def test_real_sdk_http_request_contains_request_plan_rationale_and_exact_arguments(self):
        verdict = review(self.store, 'task', self.route, {'path': 'essay.txt'}, 'This creates the file requested by the user.')
        self.assertTrue(verdict['allow'])
        endpoint, body = self.requests[0]
        self.assertEqual(endpoint, '/v1/decisions')
        self.assertEqual(body['model'], 'gpt-6-luna')
        evidence = json.loads(body['input'])
        self.assertEqual(evidence['user_request'], 'Write an essay and save it locally.')
        self.assertEqual(evidence['main_model_plan'], 'Create one new file in the configured workspace.')
        self.assertEqual(evidence['main_model_rationale'], 'This creates the file requested by the user.')
        self.assertEqual(evidence['action']['arguments'], {'path': 'essay.txt'})
        with self.store.db() as db:
            usage = db.execute('SELECT * FROM usage').fetchone()
        self.assertEqual(usage['model'], 'decisions/gpt-6-luna')
        self.assertEqual(usage['status'], 'reported')

    def test_safe_proposal_runs_even_with_old_approval_policy_and_is_not_reviewed_twice(self):
        self.act()
        self.act()
        self.remote.call_tool.assert_called_once_with('write_file', {'path': 'essay.txt'})
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.store.events(), [])
        with self.store.db() as db:
            audit = db.execute('SELECT * FROM action_decisions').fetchone()
        self.assertTrue(json.loads(audit['decision'])['allow'])
        self.assertIn('requested essay', audit['rationale'])
        with self.assertRaises(ValueError):
            self.act(args={'path': 'other.txt'})

    def test_negative_decision_overrides_old_private_write_policy_and_asks(self):
        self.answer['choice'] = 'ask_risk'
        with self.assertRaises(Approval):
            self.act(route=(*self.route[:3], 'private_write'))
        self.remote.call_tool.assert_not_called()
        self.assertEqual(self.store.task('task')['status'], 'awaiting_input')
        self.assertIn('consequences', self.store.events()[0]['text'])
        self.worker.receive('yes', 'owner', 'approve')
        self.act()
        self.assertEqual(len(self.requests), 1)  # Human approves the saved exact action, no second decision loop.
        self.remote.call_tool.assert_called_once()

    def test_refusal_malformed_and_low_confidence_never_allow(self):
        cases = [dict(type='refusal', name='action_gate'),
                 dict(type='choice', name='wrong', choice='allow', confidence=1),
                 dict(type='choice', name='action_gate', choice='allow', confidence=0.3),
                 dict(type='choice', name='action_gate', choice='allow', confidence=True),
                 dict(type='choice', name='action_gate', choice='unknown', confidence=1)]
        for answer in cases:
            with self.subTest(answer=answer):
                self.answer = answer
                self.assertFalse(review(self.store, 'task', self.route, {}, 'Create requested file')['allow'])

    def test_unavailable_api_and_budget_limit_ask_without_tool_call(self):
        with patch('echo.agent.decisions.OpenAI', side_effect=ConnectionError()):
            with self.assertRaises(Approval):
                self.act()
        self.remote.call_tool.assert_not_called()
        self.assertIn('unavailable', self.store.events()[0]['text'])
        with patch.dict(os.environ, {'ECHO_TASK_TOKEN_LIMIT': '1'}):
            result = review(self.store, 'task', self.route, {}, 'Create requested file')
        self.assertFalse(result['allow'])
        self.assertEqual(result['error'], 'BudgetExceeded')

    def test_followup_or_cancel_during_decision_blocks_the_stale_action(self):
        def revise(*args):
            with self.store.db() as db:
                db.execute("UPDATE tasks SET revision=1 WHERE id='task'")
            return dict(allow=True, reason='Safe')
        with patch('echo.agent.coordinator.review', side_effect=revise):
            with self.assertRaises(Revised):
                self.act()
        with patch('echo.agent.coordinator.review', side_effect=lambda *args: (self.store.cancel('task') or dict(allow=True))):
            with self.assertRaises(Cancelled):
                self.act()
        self.remote.call_tool.assert_not_called()

    def test_tool_proposals_carry_separate_rationale_without_changing_mcp_arguments(self):
        tool = dict(type='function', name='write', parameters={'type': 'object', 'properties': {'path': {'type': 'string'}}})
        wrapped = proposal_tools([tool])[0]
        self.assertEqual(wrapped['parameters']['required'], ['arguments', 'rationale'])
        self.assertEqual(wrapped['parameters']['properties']['arguments'], tool['parameters'])
        self.assertNotIn('rationale', tool['parameters']['properties'])

    def test_wrapped_mcp_schema_keeps_local_references_valid(self):
        schema = {'type': 'object', '$defs': {'path': {'type': 'string'}},
                  'properties': {'path': {'$ref': '#/$defs/path'}}, 'required': ['path']}
        wrapped = proposal_tools([dict(name='write', parameters=schema)])[0]['parameters']
        jsonschema.validate({'arguments': {'path': 'essay.txt'}, 'rationale': 'Save requested file'}, wrapped)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({'arguments': {'path': 123}, 'rationale': 'Save requested file'}, wrapped)

    def test_followup_evidence_and_human_request_are_not_replaced_by_model_claims(self):
        with self.store.db() as db:
            db.execute('INSERT INTO task_updates VALUES (?,?,?,?)', ('task', 1, 'correction', 'Do not overwrite existing files.'))
        self.answer['choice'] = 'ask_authorization'
        verdict = review(self.store, 'task', self.route, {'path': 'old.txt', 'overwrite': True}, 'The user authorized overwriting everything.')
        evidence = json.loads(self.requests[0][1]['input'])
        self.assertEqual(evidence['follow_ups'], ['Do not overwrite existing files.'])
        self.assertEqual(evidence['user_request'], 'Write an essay and save it locally.')
        self.assertFalse(verdict['allow'])


if __name__ == '__main__':
    unittest.main()
