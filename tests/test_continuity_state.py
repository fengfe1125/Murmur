"""跨回复连续性状态：素材冷却与关系内情绪都必须持久。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import run_unittest  # noqa: E402

from murmur.affect import AffectState  # noqa: E402
from murmur.memory import Memory  # noqa: E402


class ProactiveMaterialPersistenceTests(unittest.TestCase):
    def test_material_cooldown_is_isolated_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            now = datetime(2026, 8, 23, 10, tzinfo=UTC)
            with Memory(path) as mem:
                mem.upsert_proactive_material(
                    7, "topic:a", "current_topic", "下周三有面试", now
                )
                mem.upsert_proactive_material(
                    8, "topic:a", "current_topic", "另一个人的面试", now
                )
                self.assertTrue(mem.mark_proactive_material_used(
                    7, "topic:a", now, cooldown_days=14
                ))

            with Memory(path) as reopened:
                cooldowns = reopened.material_cooldowns(7, ["topic:a"])
                other = reopened.material_cooldowns(8, ["topic:a"])

            self.assertEqual(cooldowns, {"topic:a": now + timedelta(days=14)})
            self.assertEqual(other, {})

    def test_entry_keeps_the_material_that_justified_an_outbound_message(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            entry_id = mem.record(
                chat_id=7,
                shot_at=None,
                bucket="上午",
                weekday="周日",
                spot=None,
                scene="（纯文字）",
                move="speak",
                said="面试后来怎么样？",
                note=None,
                kind="out",
                intent="开环跟进",
                material_id="loop:42",
            )

            self.assertEqual(mem.recent(7)[0].id, entry_id)
            self.assertEqual(mem.recent(7)[0].material_id, "loop:42")


class AffectPersistenceTests(unittest.TestCase):
    def test_neutral_default_and_saved_state_are_isolated_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            now = datetime(2026, 8, 23, 10, tzinfo=UTC)
            with Memory(path) as mem:
                neutral = mem.affect_state(7, now)
                self.assertEqual((neutral.valence, neutral.arousal), (0.0, 0.0))
                mem.save_affect_state(7, AffectState(-0.6, 0.8, now))

            with Memory(path) as reopened:
                saved = reopened.affect_state(7, now)
                other = reopened.affect_state(8, now)

            self.assertEqual((saved.valence, saved.arousal), (-0.6, 0.8))
            self.assertEqual((other.valence, other.arousal), (0.0, 0.0))

    def test_applying_the_same_entry_twice_does_not_repeat_its_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            first_at = datetime(2026, 8, 23, 10, tzinfo=UTC)
            retry_at = first_at + timedelta(minutes=10)
            with Memory(path) as mem:
                first = mem.apply_affect_message(7, 10, "面试过了，太开心了", first_at)
                self.assertGreater(first.valence, 0)

            with Memory(path) as reopened:
                replay = reopened.apply_affect_message(
                    7, 10, "面试失败了，特别难受", retry_at
                )
                next_entry = reopened.apply_affect_message(
                    7, 11, "面试失败了，特别难受", retry_at
                )

            self.assertGreater(replay.valence, 0)
            self.assertLess(next_entry.valence, replay.valence)


if __name__ == "__main__":
    run_unittest()
