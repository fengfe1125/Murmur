"""Budget invariants across transactions, workers, retries and local midnight."""

import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur.app_quota import QuotaExceeded, reserve
from murmur.app_store import AppStore


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "app.db"
        self.store = AppStore(self.path)
        self.user = self.store.redeem_invite(
            code=self.store.create_invite(),
            key_id="key",
            public_key=None,
            receipt=None,
            counter=0,
            environment="development",
        ).user_id

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def moment(self, key="request"):
        return self.store.create_moment(
            user_id=self.user,
            note="hello",
            image_path=None,
            idempotency_key=key,
            request_digest=key,
        )

    def test_retry_failure_and_success(self):
        first = self.moment()
        self.assertEqual(first.moment_id, self.moment().moment_id)
        self.assertEqual(self.store.ai_quota(self.user)["used"], 1)
        job = self.store.claim_job("worker")
        self.store.fail_job(job, code="transient", message="retry", retryable=True)
        self.assertEqual(self.store.ai_quota(self.user)["used"], 0)
        self.assertEqual(first.moment_id, self.moment().moment_id)
        job = self.store.claim_job("worker")
        self.store.finish_job(job, scene="", move="quiet", memory_entry_id=1, preview_path=None)
        self.moment()
        self.assertEqual(self.store.ai_quota(self.user)["used"], 1)
        # History maintenance must not refund successful requests.
        with self.store._tx() as db:
            db.execute("DELETE FROM app_moments WHERE id=?", (first.moment_id,))
        self.assertEqual(self.store.ai_quota(self.user)["used"], 1)

    def test_cross_connection_reservations_never_exceed_fifty(self):
        def attempt(index):
            store = AppStore(self.path)
            try:
                with store._tx() as db:
                    reserve(db, self.user, str(index))
                return True
            except QuotaExceeded:
                return False
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(65)))
        self.assertEqual(sum(results), 50)
        with self.assertRaises(QuotaExceeded):
            self.moment()
        self.assertEqual(
            self.store.conn.execute("SELECT COUNT(*) FROM app_moments").fetchone()[0], 0
        )

    def test_beijing_midnight_and_erasure(self):
        with patch(
            "murmur.app_store._now", return_value=datetime(2026, 9, 21, 15, 59, 59, tzinfo=UTC)
        ):
            self.moment()
            self.assertEqual(self.store.ai_quota(self.user)["remaining"], 49)
            self.assertEqual(
                self.store.ai_quota(self.user)["resets_at"], "2026-09-22T00:00:00+08:00"
            )
        with patch("murmur.app_store._now", return_value=datetime(2026, 9, 21, 16, tzinfo=UTC)):
            self.assertEqual(self.store.ai_quota(self.user)["remaining"], 50)
        self.store.erase_user(self.user)
        self.assertEqual(
            self.store.conn.execute("SELECT COUNT(*) FROM app_ai_usage").fetchone()[0], 0
        )


if __name__ == "__main__":
    unittest.main()
