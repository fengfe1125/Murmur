"""开环记忆：持久、隔离、保守地追问和关闭。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import run_unittest  # noqa: E402

from murmur.memory import Memory  # noqa: E402

SHANGHAI = ZoneInfo("Asia/Shanghai")


class OpenLoopPersistenceTests(unittest.TestCase):
    def test_old_database_is_migrated_and_loop_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            conn = sqlite3.connect(path)
            conn.execute(
                """CREATE TABLE entries (
                    id INTEGER PRIMARY KEY,
                    logged_at TEXT NOT NULL,
                    shot_at TEXT,
                    bucket TEXT,
                    weekday TEXT,
                    place TEXT,
                    scene TEXT,
                    move TEXT,
                    said TEXT,
                    note TEXT
                )"""
            )
            conn.commit()
            conn.close()

            with Memory(path) as mem:
                loop = mem.upsert_open_loop(
                    7,
                    "明天面试",
                    "event",
                    due_at=datetime(2026, 8, 24, 9, tzinfo=SHANGHAI),
                    source_entry_id=11,
                    now=datetime(2026, 8, 23, 12, tzinfo=SHANGHAI),
                )

            with Memory(path) as reopened:
                pending = reopened.pending_open_loops(7)

            self.assertEqual([item.id for item in pending], [loop.id])
            self.assertEqual(pending[0].title, "明天面试")
            self.assertEqual(pending[0].source_entry_id, 11)

    def test_due_query_compares_instants_across_timezones_and_caps_at_four(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            created = datetime.fromisoformat("2026-08-23T00:00:00+00:00")
            for index in range(6):
                mem.upsert_open_loop(
                    7,
                    f"事项 {index}",
                    "task",
                    due_at=f"2026-08-24T{index:02d}:00:00+08:00",
                    now=created,
                )
            mem.upsert_open_loop(
                8,
                "别人的事项",
                "task",
                due_at="2026-08-23T16:00:00+00:00",
                now=created,
            )

            due = mem.due_open_loops(
                7, datetime.fromisoformat("2026-08-23T20:30:00+00:00"), limit=99
            )

            self.assertEqual([item.title for item in due], [
                "事项 0", "事项 1", "事项 2", "事项 3"
            ])

    def test_upsert_deduplicates_normalized_title_and_equivalent_due_instant(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            first = mem.upsert_open_loop(
                7, "  下周   Demo！ ", "event",
                due_at="2026-08-24T09:00:00+08:00",
                source_entry_id=10,
                now="2026-08-23T00:00:00+00:00",
            )
            second = mem.upsert_open_loop(
                7, "下周 demo", "event",
                due_at="2026-08-24T01:00:00+00:00",
                source_entry_id=12,
                now="2026-08-23T02:00:00+00:00",
            )

            self.assertEqual(second.id, first.id)
            self.assertEqual(second.created_at, first.created_at)
            self.assertEqual(len(mem.pending_open_loops(7)), 1)

    def test_invalid_or_naive_dates_are_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            for bad_date in ("明天下午", "2026-08-24T09:00:00"):
                with self.subTest(bad_date=bad_date):
                    with self.assertRaises(ValueError):
                        mem.upsert_open_loop(
                            7, "面试", "event", due_at=bad_date,
                            now="2026-08-23T00:00:00+00:00",
                        )
            self.assertEqual(mem.pending_open_loops(7), [])

    def test_followup_can_be_marked_once_and_only_after_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            with Memory(path) as mem:
                loop = mem.upsert_open_loop(
                    7, "周一复诊", "event",
                    due_at="2026-08-24T09:00:00+08:00",
                    now="2026-08-23T00:00:00+00:00",
                )
                self.assertFalse(mem.mark_open_loop_followed_up(
                    loop.id, "2026-08-24T00:59:59+00:00"
                ))
                self.assertTrue(mem.mark_open_loop_followed_up(
                    loop.id, "2026-08-24T01:00:00+00:00"
                ))
                self.assertFalse(mem.mark_open_loop_followed_up(
                    loop.id, "2026-08-25T01:00:00+00:00"
                ))
                self.assertEqual(mem.due_open_loops(
                    7, "2026-08-25T01:00:00+00:00"
                ), [])

            with Memory(path) as reopened:
                saved = reopened.pending_open_loops(7)[0]
                self.assertEqual(saved.followup_count, 1)
                self.assertEqual(saved.last_followup_at, "2026-08-24T01:00:00+00:00")

    def test_stale_followup_claim_recovers_after_process_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            with Memory(path) as mem:
                loop = mem.upsert_open_loop(
                    7,
                    "周一复诊",
                    "event",
                    due_at="2026-08-24T01:00:00+00:00",
                    now="2026-08-23T00:00:00+00:00",
                )
                self.assertTrue(mem.claim_open_loop_followup(
                    loop.id, "2026-08-24T01:00:00+00:00"
                ))
                self.assertTrue(mem.has_active_open_loop_claim(
                    7, "2026-08-24T01:14:59+00:00"
                ))
                self.assertEqual(mem.due_open_loops(
                    7, "2026-08-24T01:14:59+00:00"
                ), [])

            with Memory(path) as restarted:
                recovered = restarted.due_open_loops(
                    7, "2026-08-24T01:15:01+00:00"
                )
                self.assertEqual([item.id for item in recovered], [loop.id])
                self.assertTrue(restarted.claim_open_loop_followup(
                    loop.id, "2026-08-24T01:15:01+00:00"
                ))
                self.assertTrue(restarted.commit_open_loop_followup(
                    loop.id, "2026-08-24T01:15:01+00:00"
                ))
                self.assertEqual(restarted.due_open_loops(
                    7, "2026-08-25T01:00:00+00:00"
                ), [])

    def test_crash_orphan_is_hidden_then_removed_after_retry_commits(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(
            Path(tmp) / "memory.db"
        ) as mem:
            loop = mem.upsert_open_loop(
                7,
                "周一复诊",
                "event",
                due_at="2026-08-24T01:00:00+00:00",
                now="2026-08-23T00:00:00+00:00",
            )
            material_id = f"open_loop:{loop.id}"
            self.assertTrue(mem.claim_open_loop_followup(
                loop.id, "2026-08-24T01:00:00+00:00"
            ))
            orphan = mem.record(
                chat_id=7,
                thread="app:direct:user",
                shot_at=None,
                bucket="午间",
                weekday="周一",
                spot=None,
                scene="desk",
                move="speak",
                said="复诊怎么样了",
                note=None,
                kind="out",
                material_id=material_id,
                delivery_state="pending",
            )
            self.assertEqual(mem.recent(7), [])
            self.assertEqual(mem.recent_outbound(7), [])
            self.assertEqual(mem.unanswered_outbound(7), 0)

            self.assertTrue(mem.claim_open_loop_followup(
                loop.id, "2026-08-24T01:15:01+00:00"
            ))
            delivered = mem.record(
                chat_id=7,
                thread="app:direct:user",
                shot_at=None,
                bucket="午间",
                weekday="周一",
                spot=None,
                scene="desk",
                move="speak",
                said="复诊结果出来了吗",
                note=None,
                kind="out",
                material_id=material_id,
                delivery_state="pending",
            )
            self.assertTrue(mem.commit_open_loop_followup(
                loop.id, "2026-08-24T01:15:01+00:00"
            ))
            self.assertTrue(mem.commit_outbound_entry(delivered, 7))
            self.assertEqual(
                mem.discard_unlinked_outbound_entries(7, {delivered}),
                1,
            )
            self.assertIsNone(mem.conn.execute(
                "SELECT 1 FROM entries WHERE id=?", (orphan,)
            ).fetchone())
            self.assertEqual([entry.id for entry in mem.recent_outbound(7)], [delivered])

    def test_closing_or_expiring_a_loop_preserves_delivery_saga(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(
            Path(tmp) / "memory.db"
        ) as mem:
            resolved = mem.upsert_open_loop(
                7, "周一复诊", "event", now="2026-08-23T00:00:00+00:00"
            )
            expired = mem.upsert_open_loop(
                7, "交报告", "task", now="2026-08-01T00:00:00+00:00"
            )
            for loop in (resolved, expired):
                mem.record(
                    chat_id=7,
                    shot_at=None,
                    bucket=None,
                    weekday=None,
                    spot=None,
                    scene=None,
                    move="speak",
                    said="pending",
                    note=None,
                    kind="out",
                    material_id=f"open_loop:{loop.id}",
                    delivery_state="pending",
                )

            mem.resolve_open_loops_from_text(
                7, "复诊结束了", "2026-08-24T02:00:00+00:00"
            )
            mem.expire_open_loops(7, "2026-08-20T00:00:00+00:00")

            pending = mem.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE delivery_state='pending'"
            ).fetchone()[0]
            self.assertEqual(pending, 2)

    def test_text_closes_only_matching_loop_with_explicit_result_language(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            now = "2026-08-23T00:00:00+00:00"
            interview = mem.upsert_open_loop(7, "明天面试", "event", now=now)
            report = mem.upsert_open_loop(7, "下周交报告", "task", now=now)
            other = mem.upsert_open_loop(8, "明天面试", "event", now=now)

            self.assertEqual(mem.resolve_open_loops_from_text(
                7, "结束了", "2026-08-24T02:00:00+00:00"
            ), [])
            self.assertEqual(mem.resolve_open_loops_from_text(
                7, "面试怎么样？", "2026-08-24T02:00:00+00:00"
            ), [])
            self.assertEqual(mem.resolve_open_loops_from_text(
                7, "", "2026-08-24T02:00:00+00:00"
            ), [])

            resolved = mem.resolve_open_loops_from_text(
                7, "面试结束了，结果还不错", "2026-08-24T02:00:00+00:00"
            )

            self.assertEqual([item.id for item in resolved], [interview.id])
            self.assertEqual(resolved[0].status, "resolved")
            self.assertEqual([item.id for item in mem.pending_open_loops(7)], [report.id])
            self.assertEqual([item.id for item in mem.pending_open_loops(8)], [other.id])

    def test_explicit_cancellation_marks_matching_loop_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            loop = mem.upsert_open_loop(
                7, "周一复诊", "event", now="2026-08-23T00:00:00+00:00"
            )

            closed = mem.resolve_open_loops_from_text(
                7, "复诊取消了，暂时不去了", "2026-08-24T02:00:00+00:00"
            )

            self.assertEqual([item.id for item in closed], [loop.id])
            self.assertEqual(closed[0].status, "cancelled")

    def test_kind_backstops_expire_from_due_or_creation_after_2_7_14_days(self):
        with tempfile.TemporaryDirectory() as tmp, Memory(Path(tmp) / "memory.db") as mem:
            created = "2026-08-01T00:00:00+00:00"
            event = mem.upsert_open_loop(7, "面试", "event", now=created)
            task = mem.upsert_open_loop(7, "写报告", "task", now=created)
            unknown = mem.upsert_open_loop(7, "未知事项", "other", now=created)
            ongoing = mem.upsert_open_loop(7, "学游泳", "ongoing", now=created)
            due_event = mem.upsert_open_loop(
                7, "九号见医生", "event",
                due_at="2026-08-10T08:00:00+08:00", now=created,
            )

            first = mem.expire_open_loops(7, "2026-08-03T00:00:00+00:00")
            second = mem.expire_open_loops(7, "2026-08-08T00:00:00+00:00")
            final = mem.expire_open_loops(7, "2026-08-15T00:00:00+00:00")

            self.assertEqual([item.id for item in first], [event.id])
            self.assertEqual([item.id for item in second], [task.id, unknown.id])
            self.assertEqual(
                {item.id for item in final}, {ongoing.id, due_event.id}
            )
            self.assertTrue(all(item.status == "expired" for item in first + second + final))


if __name__ == "__main__":
    run_unittest()
