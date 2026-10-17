"""Offline coverage of provenance, replacement, forgetting, and optional retrieval."""
import os
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from echo.agent.memory import Memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'memory.sqlite3'
        self.memory = Memory(self.path)

    def test_remember_provenance_persists_and_is_private(self):
        ident = self.memory.remember('Biology study guide', 'task:123')
        result = Memory(self.path).retrieve('biology')[0]
        self.assertEqual((result['id'], result['source']), (ident, 'task:123'))
        self.assertIn('+00:00', result['created'])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)
        self.assertEqual(self.memory.retrieve('unrelated'), [])

    def test_repeated_fact_deduplicates_but_provenance_is_distinct(self):
        ident = self.memory.remember('Prefer short answers', 'user:1')
        self.assertEqual(ident, self.memory.remember('Prefer short answers', 'user:1'))
        other = self.memory.remember('Prefer short answers', 'source:2')
        self.assertNotEqual(ident, other)
        self.assertEqual(len(self.memory.recent()), 2)

    def test_key_replaces_stale_fact_without_overwriting_other_sources(self):
        ident = self.memory.remember('Biology deadline Monday', 'canvas:123', key='deadline')
        self.memory.remember('Math deadline Monday', 'canvas:456', key='deadline')
        self.assertEqual(ident, self.memory.remember('Biology deadline Friday', 'canvas:123', key='deadline'))
        self.assertEqual([item['text'] for item in self.memory.retrieve('Monday')], ['Math deadline Monday'])
        self.assertEqual(self.memory.recent()[0]['text'], 'Biology deadline Friday')
        self.assertEqual(len(self.memory.retrieve('biology')), 1)

    def test_expiration_and_limits(self):
        self.memory.remember('Old deadline', 'canvas', expires_at='2001-01-01T00:00:00Z')
        self.memory.remember('New deadline', 'canvas2', expires_at='2999-01-01T00:00:00-05:00')
        self.assertEqual([row['text'] for row in self.memory.retrieve('deadline')], ['New deadline'])
        self.assertEqual(len(self.memory.recent()), 1)
        self.assertEqual(self.memory.retrieve('deadline', limit=-1), [])
        self.assertEqual(self.memory.recent(limit=0), [])
        with self.assertRaises(ValueError):
            self.memory.remember('Date', 'source', expires_at='2026-01-01')

    def test_forgetting_is_literal_and_removes_search_and_vectors(self):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(embedding=[1, 0])])
        memory = Memory(self.path, embedding_client=client)
        memory.remember('My biology preference', 'user:1')
        memory.remember('My math preference', 'user:2')
        self.assertEqual(memory.forget(''), 0)
        self.assertEqual(memory.forget('%'), 0)
        self.assertEqual(memory.forget('BIOLOGY'), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_search WHERE memory_search MATCH 'biology'").fetchone()[0], 0)
        self.assertEqual([row['text'] for row in memory.recent()], ['My math preference'])
        self.assertEqual(memory.forget('user:2'), 1)
        self.assertEqual(memory.retrieve('biology'), [])

    def test_credential_guard_before_storage_or_embedding(self):
        client = Mock()
        memory = Memory(self.path, embedding_client=client)
        for secret in ('sk-proj-' + 'a' * 25, 'password=abc', 'Bearer ' + 'x' * 24,
                       '-----BEGIN RSA PRIVATE KEY-----'):
            with self.assertRaises(ValueError):
                memory.remember(secret, 'user')
            with self.assertRaises(ValueError):
                memory.remember('ordinary note', secret)
            self.assertEqual(memory.retrieve(secret), [])
        self.assertEqual(memory.recent(), [])
        client.embeddings.create.assert_not_called()

    def test_fts_query_is_not_executable_and_unicode_is_supported(self):
        self.memory.remember('Biología is interesting', 'user')
        self.assertEqual(len(self.memory.retrieve('Biología')), 1)
        self.assertEqual(self.memory.retrieve('"* OR : SELECT'), [])
        self.assertEqual(self.memory.retrieve('!!!'), [])

    def test_optional_semantic_search_and_no_duplicate_embedding(self):
        client = Mock()
        def embed(**kwargs):
            vector = [0, 1] if 'math' in kwargs['input'] else [1, 0]
            return SimpleNamespace(data=[SimpleNamespace(embedding=vector)])
        client.embeddings.create.side_effect = embed
        memory = Memory(self.path, embedding_client=client)
        memory.remember('Prefer concise replies', 'user')
        memory.remember('Prefer concise replies', 'user')
        self.assertEqual(client.embeddings.create.call_count, 1)
        memory.remember('math assignment', 'canvas')
        self.assertEqual([row['text'] for row in memory.retrieve('brief responses')], ['Prefer concise replies'])
        # No vector from a different model is mixed with current query embeddings.
        other = Memory(self.path, embedding_client=client, embedding_model='different-model')
        calls = client.embeddings.create.call_count
        self.assertEqual(other.retrieve('brief responses'), [])
        self.assertEqual(client.embeddings.create.call_count, calls)

    def test_network_calls_have_no_open_db_context_and_empty_search_is_free(self):
        client = Mock()
        memory = Memory(self.path, embedding_client=client)
        active = 0
        real_db = memory._db
        @contextmanager
        def checked_db():
            nonlocal active
            active += 1
            try:
                with real_db() as db:
                    yield db
            finally:
                active -= 1
        memory._db = checked_db
        def embed(**kwargs):
            self.assertEqual(active, 0)
            # A concurrent writer can acquire the database immediately.
            with closing(sqlite3.connect(self.path, timeout=0)) as db:
                db.execute('BEGIN IMMEDIATE')
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1, 0])])
        client.embeddings.create.side_effect = embed
        self.assertEqual(memory.retrieve('anything'), [])
        client.embeddings.create.assert_not_called()
        memory.remember('Biology preferences', 'user')
        self.assertEqual(len(memory.retrieve('study preferences')), 1)
        self.assertEqual(client.embeddings.create.call_count, 2)

    def test_forget_during_query_embedding_does_not_return_stale_snapshot(self):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(embedding=[1, 0])])
        memory = Memory(self.path, embedding_client=client)
        memory.remember('Biology preference', 'user')
        def embed(**kwargs):
            memory.forget('biology')
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1, 0])])
        client.embeddings.create.side_effect = embed
        self.assertEqual(memory.retrieve('biology'), [])

    def test_embedding_failures_fall_back_to_keyword(self):
        client = Mock()
        client.embeddings.create.side_effect = RuntimeError('not connected')
        memory = Memory(self.path, embedding_client=client)
        with self.assertLogs(level='WARNING'):
            memory.remember('Biology preference', 'user')
            result = memory.retrieve('biology')
        self.assertEqual(result[0]['text'], 'Biology preference')


if __name__ == '__main__':
    unittest.main()
