from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import run_unittest  # noqa: E402

from murmur.affect import AffectState, apply_message, decay, prompt_context  # noqa: E402


class AffectTests(unittest.TestCase):
    def test_positive_and_negative_events_move_state(self):
        now = datetime(2026, 8, 23, 10, tzinfo=UTC)
        base = AffectState(0.0, 0.0, now)
        happy = apply_message(base, "面试过了，太开心了", now)
        sad = apply_message(base, "面试失败了，特别难受", now)
        self.assertGreater(happy.valence, 0)
        self.assertGreater(happy.arousal, 0)
        self.assertLess(sad.valence, 0)
        self.assertGreater(sad.arousal, 0)

    def test_silence_only_decays_toward_neutral(self):
        start = datetime(2026, 8, 23, 10, tzinfo=UTC)
        state = AffectState(-0.8, 0.8, start)
        later = decay(state, start + timedelta(hours=12))
        self.assertGreater(later.valence, state.valence)
        self.assertLess(later.arousal, state.arousal)
        self.assertGreaterEqual(later.valence, -0.8)

    def test_plain_message_does_not_invent_an_emotion_event(self):
        now = datetime(2026, 8, 23, 10, tzinfo=UTC)
        base = AffectState(0.2, 0.3, now)
        updated = apply_message(base, "我到家了", now)
        self.assertEqual(updated.valence, base.valence)
        self.assertEqual(updated.arousal, base.arousal)

    def test_prompt_is_natural_language_and_hides_numbers(self):
        now = datetime(2026, 8, 23, 10, tzinfo=UTC)
        text = prompt_context(AffectState(-0.7, 0.8, now))
        self.assertIn("低落", text)
        self.assertNotIn("-0.7", text)
        self.assertNotIn("0.8", text)


if __name__ == "__main__":
    run_unittest()
