"""Daily review lifecycle with isolated SQLite data and fictional evidence."""

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur.app_store import AccountDeleting, AppStore, NotFound
from murmur.daily_review import DailyReviews, validate
from murmur.memory import Memory, thread_key


class DailyReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AppStore(self.root / "app.db")
        self.memory = Memory(self.root / "memory.db")
        self.user = self.enroll("a")
        self.reviews = DailyReviews(self.store)
        self.now = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)
        self.reviews.configure(self.user, now=self.now - timedelta(hours=10))

    def tearDown(self):
        self.memory.close()
        self.store.close()
        self.tmp.cleanup()

    def enroll(self, key):
        return self.store.redeem_invite(
            code=self.store.create_invite(),
            key_id=key,
            public_key=None,
            receipt=None,
            counter=0,
            environment="development",
        ).user_id

    def source(self, when=None, note="我想周末散步。", photo=False):
        when = when or self.now - timedelta(minutes=10)
        m = self.store.create_moment(
            user_id=self.user,
            note=note,
            image_path=None,
            idempotency_key=str(when),
            request_digest=str(when),
        )
        cid, label = thread_key("app", "direct", self.user)
        entry = self.memory.record(
            chat_id=cid,
            thread=label,
            shot_at=datetime.fromisoformat("2024-04-01T12:00:00+08:00") if photo else None,
            bucket=None,
            weekday=None,
            spot=None,
            scene="林间步道" if photo else "",
            move="speak",
            said="AI不应成为事实",
            note=note,
            kind="in",
            has_photo=photo,
        )
        with self.store._tx() as db:
            db.execute(
                "UPDATE app_moments SET status='complete',memory_entry_id=?,note=NULL,created_at=? WHERE id=?",
                (entry, when.isoformat(), m.moment_id),
            )
            db.execute("UPDATE app_jobs SET status='done' WHERE moment_id=?", (m.moment_id,))
        self.reviews.import_completed(self.root / "memory.db")
        return m.moment_id

    def output(self, task):
        sid = task["sources"][0]["id"]
        return {
            "summary": "今天提到想安排周末散步，还没决定具体时间。",
            "question": "你准备把散步安排在什么时间？",
            "source_ids": [sid],
            "memories": [{"text": "想周末散步。", "evidence": "user_stated", "source_ids": [sid]}],
        }

    def ready(self):
        self.source()
        self.reviews.enqueue_due(self.now)
        task = self.reviews.claim(self.now)
        self.assertTrue(self.reviews.publish(task, self.output(task), self.now))
        return self.reviews.detail(self.user, "2026-09-21")

    def test_due_local_time_empty_and_no_backfill(self):
        self.reviews.enqueue_due(self.now)
        self.assertIsNone(self.reviews.claim(self.now))
        self.source()
        self.reviews.enqueue_due(self.now - timedelta(seconds=1))
        self.assertIsNone(self.reviews.claim(self.now))
        self.reviews.enqueue_due(self.now)
        self.assertIsNotNone(self.reviews.claim(self.now))
        with self.assertRaises(NotFound):
            self.reviews.request(self.user, "2026-09-20")

    def test_photo_date_and_committed_user_source(self):
        self.source(photo=True)
        s = self.reviews.sources(self.user, "2026-09-21")[0]
        self.assertEqual(s["shot_at"], "2024-04-01T12:00:00+08:00")
        self.assertEqual(s["user_text"], "我想周末散步。")
        self.assertNotIn("AI不应", str(s))

    def test_late_content_updates_one_review_without_changing_question(self):
        first = self.ready()
        self.source(self.now + timedelta(minutes=1), note="先安排星期六。")
        self.reviews.enqueue_due(self.now + timedelta(minutes=2))
        task = self.reviews.claim(self.now + timedelta(minutes=2))
        output = self.output(task)
        output["question"] = "改成另一个问题？"
        self.reviews.publish(task, output)
        new = self.reviews.detail(self.user, "2026-09-21")
        self.assertEqual(new["question"], first["question"])
        self.assertEqual(new["version"], 2)
        self.reviews.enqueue_due(self.now + timedelta(minutes=3))
        self.assertIsNone(self.reviews.claim(self.now + timedelta(minutes=3)))

    def test_lease_prevents_duplicate_and_survives_restart(self):
        self.source()
        self.reviews.enqueue_due(self.now)
        a = self.reviews.claim(self.now)
        self.assertIsNone(self.reviews.claim(self.now))
        b = self.reviews.claim(self.now + timedelta(minutes=11))
        self.assertFalse(self.reviews.publish(a, self.output(a)))
        self.assertTrue(self.reviews.publish(b, self.output(b)))

    def test_correction_fences_inflight_and_survives_automatic_update(self):
        first = self.ready()
        self.reviews.request(self.user, "2026-09-21", force=True, now=self.now)
        stale = self.reviews.claim(self.now)
        ident = first["memories"][0]["id"]
        self.reviews.edit(self.user, ident, "只在晴天散步。")
        self.assertFalse(self.reviews.publish(stale, self.output(stale)))
        task = self.reviews.claim(self.now)
        self.assertIn("只在晴天", task["sources"][0]["user_text"])
        self.reviews.publish(task, self.output(task))
        self.assertEqual(
            self.reviews.detail(self.user, "2026-09-21")["memories"][0]["text"], "只在晴天散步。"
        )

    def test_forget_withdraws_derived_sources_and_is_not_reimported(self):
        first = self.ready()
        self.reviews.edit(self.user, first["memories"][0]["id"], forget=True)
        self.assertEqual(self.reviews.sources(self.user, "2026-09-21"), [])
        self.assertEqual(self.reviews.recalled(self.user, "散步"), [])
        self.reviews.import_completed(self.root / "memory.db")
        self.assertEqual(self.reviews.sources(self.user, "2026-09-21"), [])
        self.assertEqual(self.reviews.detail(self.user, "2026-09-21")["summary"], "")

    def test_account_isolation_and_delete_fence(self):
        first = self.ready()
        other = self.enroll("b")
        with self.assertRaises(NotFound):
            self.reviews.detail(other, "2026-09-21")
        with self.assertRaises(NotFound):
            self.reviews.edit(other, first["memories"][0]["id"], "更改")
        self.reviews.request(self.user, "2026-09-21", force=True, now=self.now)
        task = self.reviews.claim(self.now)
        self.store.begin_user_erasure(self.user)
        with self.assertRaises(AccountDeleting):
            self.reviews.publish(task, self.output(task))
        self.store.complete_user_erasure(self.user)
        for table in (
            "app_review_sources",
            "app_review_preferences",
            "app_daily_reviews",
            "app_review_memories",
        ):
            self.assertEqual(
                self.store.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
            )

    def test_correction_projects_history_without_overwriting_original_source(self):
        first = self.ready()
        cid, _ = thread_key("app", "direct", self.user)
        original = self.memory.recent(cid)[0]
        self.reviews.edit(self.user, first["memories"][0]["id"], "只在晴天散步。")
        view = self.reviews.history_view(self.user, self.memory)
        corrected = view.entries_by_ids([original.id])[0]
        self.assertIn("只在晴天", corrected.note)
        self.assertIsNone(corrected.said)
        self.assertEqual(self.memory.entries_by_ids([original.id])[0].note, original.note)
        self.assertEqual(
            self.reviews.detail(self.user, "2026-09-21")["sources"][0]["user_text"], original.note
        )
        self.reviews.edit(self.user, first["memories"][0]["id"], forget=True)
        self.assertEqual(self.reviews.history_view(self.user, self.memory).recent(cid), [])
        self.assertTrue(self.memory.entries_by_ids([original.id]))
        # A retry after a lost DELETE response succeeds without resurrecting it.
        self.reviews.edit(self.user, first["memories"][0]["id"], forget=True)

    def test_retry_backoff_and_reopened_worker_catches_up(self):
        self.source()
        self.reviews.enqueue_due(self.now)
        task = self.reviews.claim(self.now)
        self.reviews.fail(task)
        self.assertIsNone(self.reviews.claim(self.now))
        reopened = AppStore(self.root / "app.db")
        try:
            reviews = DailyReviews(reopened)
            later = datetime.now(UTC) + timedelta(days=1)
            reviews.enqueue_due(later)
            retry = reviews.claim(later)
            self.assertIsNotNone(retry)
            self.assertEqual(retry["day"], "2026-09-21")
        finally:
            reopened.close()

    def test_preference_change_revokes_generator_lease(self):
        self.source()
        self.reviews.enqueue_due(self.now)
        old = self.reviews.claim(self.now)
        self.reviews.configure(self.user, enabled=False, now=self.now)
        self.assertFalse(self.reviews.publish(old, self.output(old)))
        self.assertIsNone(self.reviews.claim(self.now))
        self.reviews.configure(self.user, enabled=True, now=self.now)
        self.assertIsNotNone(self.reviews.claim(self.now))

    def test_device_timezone_crosses_utc_midnight(self):
        with self.store._tx() as db:
            db.execute(
                "UPDATE app_devices SET timezone='America/New_York' WHERE user_id=?", (self.user,)
            )
        sent = datetime(2026, 9, 22, 2, 0, tzinfo=UTC)
        self.source(sent)
        self.assertEqual(self.reviews.sources(self.user, "2026-09-21")[0]["day"], "2026-09-21")
        self.reviews.enqueue_due(sent + timedelta(minutes=29))
        self.assertIsNone(self.reviews.claim(sent + timedelta(minutes=29)))
        self.reviews.enqueue_due(sent + timedelta(minutes=30))
        task = self.reviews.claim(sent + timedelta(minutes=30))
        self.assertEqual(task["timezone"], "America/New_York")
        self.assertEqual(task["day"], "2026-09-21")

    def test_recall_finds_old_relevant_memory_beyond_recent_window(self):
        first = self.ready()
        with self.store._tx() as db:
            db.executemany(
                "INSERT INTO app_review_memories(id,user_id,day,text,evidence,source_ids,updated_at) VALUES(?,?,?,'喜欢烘焙','user_stated','[]',?)",
                [
                    (f"filler-{i}", self.user, "2026-09-22", self.now.isoformat())
                    for i in range(400)
                ],
            )
        recalled = self.reviews.recalled(self.user, "我想去散步")
        self.assertEqual([r["text"] for r in recalled], [first["memories"][0]["text"]])

    def test_structure_rejects_fabricated_sources_and_ai_as_evidence(self):
        self.source()
        self.reviews.enqueue_due(self.now)
        task = self.reviews.claim(self.now)
        output = self.output(task)
        output["memories"][0]["source_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            validate(output, task["sources"])
        output = self.output(task)
        output["memories"][0]["evidence"] = "ai_said"
        with self.assertRaises(ValueError):
            validate(output, task["sources"])
        output = self.output(task)
        output["question"] = "怎么样？为什么？"
        with self.assertRaises(ValueError):
            validate(output, task["sources"])


if __name__ == "__main__":
    unittest.main()
