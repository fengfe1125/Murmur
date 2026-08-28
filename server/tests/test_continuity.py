from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import make_config, run_unittest  # noqa: E402

from murmur.continuity import (  # noqa: E402
    parse_open_loop_candidates,
    refresh_open_loops,
)


class _Choice:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.message = type("Message", (), {"content": content})()
        self.finish_reason = finish_reason


class _Client:
    def __init__(self, content: str):
        self.content = content
        self.calls = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"choices": [_Choice(self.content)]})()


class _Memory:
    def __init__(self):
        self.resolved = []
        self.upserts = []

    def resolve_open_loops_from_text(self, chat_id, text, now):
        self.resolved.append((chat_id, text, now))
        return []

    def upsert_open_loop(self, **kwargs):
        self.upserts.append(kwargs)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 23, 10, tzinfo=UTC)

    def test_parser_accepts_offset_and_local_date(self):
        found = parse_open_loop_candidates(
            '{"loops":['
            '{"title":"明天面试","kind":"event","due_at":"2026-08-24T15:00:00+08:00"},'
            '{"title":"等快递","kind":"task","due_at":"2026-08-25"}'
            "]}",
            self.now,
        )
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].due_at.isoformat(), "2026-08-24T07:00:00+00:00")
        self.assertEqual(found[1].due_at.hour, 12)

    def test_invalid_date_drops_candidate_instead_of_guessing(self):
        found = parse_open_loop_candidates(
            '{"loops":[{"title":"面试","kind":"event","due_at":"下周差不多"}]}',
            self.now,
        )
        self.assertEqual(found, [])

    def test_refresh_resolves_first_and_upserts_model_candidates(self):
        memory = _Memory()
        client = _Client(
            '{"loops":[{"title":"明天面试","kind":"event",'
            '"due_at":"2026-08-24T15:00:00+08:00"}]}'
        )
        count = refresh_open_loops(
            make_config(open_loops=True),
            memory,
            7,
            42,
            "我明天下午三点面试",
            now=self.now,
            client=client,
        )
        self.assertEqual(count, 1)
        self.assertEqual(memory.resolved[0][:2], (7, "我明天下午三点面试"))
        self.assertEqual(memory.upserts[0]["source_entry_id"], 42)

    def test_non_temporal_message_does_not_spend_a_model_call(self):
        memory = _Memory()
        client = _Client('{"loops":[]}')
        count = refresh_open_loops(
            make_config(open_loops=True), memory, 7, 42, "我到家了",
            now=self.now, client=client,
        )
        self.assertEqual(count, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(len(memory.resolved), 1)


if __name__ == "__main__":
    run_unittest()
