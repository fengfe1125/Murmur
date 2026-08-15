"""AppStore security and lifecycle invariants.

Run directly: ``python tests/test_app_store.py``.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.app_store import (  # noqa: E402
    AppStore,
    ChallengeInvalid,
    DeviceLimit,
    IdempotencyConflict,
    InviteInvalid,
    LastDevice,
    MomentInFlight,
)


class AppStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AppStore(Path(self.tmp.name) / "app.db")
        code = self.store.create_invite()
        self.enrollment = self.store.redeem_invite(
            code=code, key_id="dev-first-key", public_key=None, receipt=None,
            counter=0, environment="development",
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_invites_are_hashed_one_time_and_device_limit_is_three(self):
        code = self.store.create_invite(kind="device", user_id=self.enrollment.user_id)
        row = self.store.conn.execute("SELECT code_hash FROM app_invites WHERE kind='device'").fetchone()
        self.assertNotEqual(row["code_hash"], code)
        self.store.redeem_invite(
            code=code, key_id="dev-second-key", public_key=None, receipt=None,
            counter=0, environment="development",
        )
        with self.assertRaises(InviteInvalid):
            self.store.redeem_invite(
                code=code, key_id="dev-replay-key", public_key=None, receipt=None,
                counter=0, environment="development",
            )
        third = self.store.create_invite(kind="device", user_id=self.enrollment.user_id)
        self.store.redeem_invite(
            code=third, key_id="dev-third-key", public_key=None, receipt=None,
            counter=0, environment="development",
        )
        fourth = self.store.create_invite(kind="device", user_id=self.enrollment.user_id)
        with self.assertRaises(DeviceLimit):
            self.store.redeem_invite(
                code=fourth, key_id="dev-fourth-key", public_key=None, receipt=None,
                counter=0, environment="development",
            )

    def test_invite_default_lifetimes_are_fixed_at_store_boundary(self):
        user_code = self.store.create_invite()
        device_code = self.store.create_invite(
            kind="device", user_id=self.enrollment.user_id
        )
        rows = self.store.conn.execute(
            "SELECT code_hash,created_at,expires_at FROM app_invites "
            "WHERE redeemed_at IS NULL ORDER BY created_at"
        ).fetchall()
        by_hash = {row["code_hash"]: row for row in rows}
        import hashlib

        user = by_hash[hashlib.sha256(user_code.encode()).hexdigest()]
        device = by_hash[hashlib.sha256(device_code.encode()).hexdigest()]
        user_ttl = datetime.fromisoformat(user["expires_at"]) - datetime.fromisoformat(
            user["created_at"]
        )
        device_ttl = datetime.fromisoformat(device["expires_at"]) - datetime.fromisoformat(
            device["created_at"]
        )
        self.assertAlmostEqual(user_ttl.total_seconds(), timedelta(days=7).total_seconds(), delta=1)
        self.assertAlmostEqual(
            device_ttl.total_seconds(), timedelta(minutes=30).total_seconds(), delta=1
        )

    def test_challenge_default_ttl_is_five_minutes(self):
        challenge_id, _value, expires_at = self.store.issue_challenge(
            "request", key_id=self.enrollment.key_id
        )
        row = self.store.conn.execute(
            "SELECT created_at, expires_at FROM app_challenges WHERE id=?",
            (challenge_id,),
        ).fetchone()
        ttl = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(
            row["created_at"]
        )
        self.assertAlmostEqual(ttl.total_seconds(), timedelta(minutes=5).total_seconds(), delta=1)
        self.assertEqual(expires_at, row["expires_at"])

    def test_invite_can_be_listed_and_revoked_without_revealing_code(self):
        code = self.store.create_invite(alias="Alice")
        listed = self.store.list_invites()
        invite = next(row for row in listed if row["alias"] == "Alice")
        self.assertNotIn("code", invite)
        self.assertTrue(self.store.revoke_invite(invite["id"]))
        self.assertFalse(self.store.revoke_invite(invite["id"]))
        with self.assertRaises(InviteInvalid):
            self.store.redeem_invite(
                code=code, key_id="dev-revoked", public_key=None, receipt=None,
                counter=0, environment="development",
            )
    def test_permanent_reusable_invite_keeps_working_and_stays_revocable(self):
        code = self.store.create_invite(
            alias="Front door", max_uses=None, permanent=True
        )
        # Redeems repeatedly, minting a distinct identity each time.
        users = set()
        for n in range(3):
            enrollment = self.store.redeem_invite(
                code=code, key_id=f"dev-open-{n}", public_key=None, receipt=None,
                counter=0, environment="development",
            )
            users.add(enrollment.user_id)
        self.assertEqual(len(users), 3)
        self.assertNotIn(self.enrollment.user_id, users)

        # Still listed as open after use, and never expires.
        invite = next(
            row for row in self.store.list_invites() if row["alias"] == "Front door"
        )
        self.assertIsNone(invite["max_uses"])
        self.assertEqual(invite["use_count"], 3)
        self.assertTrue(invite["expires_at"].startswith("9999-"))

        # An already-used permanent code must remain switchable-off.
        self.assertTrue(self.store.revoke_invite(invite["id"]))
        with self.assertRaises(InviteInvalid):
            self.store.redeem_invite(
                code=code, key_id="dev-after-revoke", public_key=None, receipt=None,
                counter=0, environment="development",
            )

    def test_bounded_multi_use_invite_stops_at_its_ceiling(self):
        code = self.store.create_invite(max_uses=2)
        for n in range(2):
            self.store.redeem_invite(
                code=code, key_id=f"dev-cap-{n}", public_key=None, receipt=None,
                counter=0, environment="development",
            )
        with self.assertRaises(InviteInvalid):
            self.store.redeem_invite(
                code=code, key_id="dev-cap-over", public_key=None, receipt=None,
                counter=0, environment="development",
            )

    def test_default_invite_is_still_single_use(self):
        code = self.store.create_invite()
        self.store.redeem_invite(
            code=code, key_id="dev-once", public_key=None, receipt=None,
            counter=0, environment="development",
        )
        with self.assertRaises(InviteInvalid):
            self.store.redeem_invite(
                code=code, key_id="dev-twice", public_key=None, receipt=None,
                counter=0, environment="development",
            )

    def test_challenge_is_single_use_even_under_race(self):
        challenge_id, expected, _ = self.store.issue_challenge(
            "request", key_id=self.enrollment.key_id
        )
        outcomes: list[bytes | Exception] = []

        def consume():
            try:
                outcomes.append(self.store.consume_challenge(
                    challenge_id, "request", key_id=self.enrollment.key_id
                ))
            except Exception as exc:  # result is the assertion under test
                outcomes.append(exc)

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(value == expected for value in outcomes), 1)
        self.assertEqual(sum(isinstance(value, ChallengeInvalid) for value in outcomes), 1)

    def test_idempotency_and_single_inflight_are_database_constraints(self):
        first = self.store.create_moment(
            user_id=self.enrollment.user_id, note="hello", image_path=None,
            idempotency_key="request-0001", request_digest="same",
        )
        replay = self.store.create_moment(
            user_id=self.enrollment.user_id, note="hello", image_path=None,
            idempotency_key="request-0001", request_digest="same",
        )
        self.assertFalse(replay.created)
        self.assertEqual(first.moment_id, replay.moment_id)
        with self.assertRaises(IdempotencyConflict):
            self.store.create_moment(
                user_id=self.enrollment.user_id, note="changed", image_path=None,
                idempotency_key="request-0001", request_digest="different",
            )
        with self.assertRaises(MomentInFlight):
            self.store.create_moment(
                user_id=self.enrollment.user_id, note="next", image_path=None,
                idempotency_key="request-0002", request_digest="next",
            )
        job = self.store.claim_job("worker")
        self.store.fail_job(job, code="test", message="failed", retryable=True)
        second = self.store.create_moment(
            user_id=self.enrollment.user_id, note="next", image_path=None,
            idempotency_key="request-0002", request_digest="next",
        )
        self.assertTrue(second.created)

    def test_failed_idempotent_retry_replaces_terminal_events_and_upload(self):
        first = self.store.create_moment(
            user_id=self.enrollment.user_id, note="same", image_path="/tmp/old-upload",
            idempotency_key="request-retry", request_digest="same-digest",
        )
        job = self.store.claim_job("worker-one")
        self.store.append_job_event(job, "worker-one", "bubble", {"text": "partial"})
        self.assertTrue(self.store.fail_job(
            job, code="processing_failed", message="safe", retryable=True,
            worker_id="worker-one",
        ))
        retried = self.store.create_moment(
            user_id=self.enrollment.user_id, note="same", image_path="/tmp/new-upload",
            idempotency_key="request-retry", request_digest="same-digest",
        )
        self.assertTrue(retried.created)
        self.assertEqual(retried.moment_id, first.moment_id)
        moment = self.store.moment_for_user(first.moment_id, self.enrollment.user_id)
        self.assertEqual((moment["status"], moment["image_path"]),
                         ("queued", "/tmp/new-upload"))
        events = self.store.events_after(first.moment_id, self.enrollment.user_id)
        self.assertEqual([event["event"] for event in events], ["accepted"])
        self.assertEqual(events[0]["sequence"], 1)
        row = self.store.conn.execute(
            "SELECT status,worker_id,lease_until,last_error FROM app_jobs WHERE moment_id=?",
            (first.moment_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("queued", None, None, None))
        with self.assertRaises(IdempotencyConflict):
            self.store.create_moment(
                user_id=self.enrollment.user_id, note="changed", image_path=None,
                idempotency_key="request-retry", request_digest="changed-digest",
            )

    def test_nonretryable_failure_keeps_terminal_event_and_never_requeues(self):
        first = self.store.create_moment(
            user_id=self.enrollment.user_id, note="image", image_path="/tmp/invalid-one",
            idempotency_key="invalid-image", request_digest="same-invalid",
        )
        job = self.store.claim_job("worker")
        self.assertTrue(self.store.fail_job(
            job, code="image_too_large", message="too large", retryable=False,
            worker_id="worker",
        ))
        replay = self.store.create_moment(
            user_id=self.enrollment.user_id, note="image", image_path="/tmp/invalid-two",
            idempotency_key="invalid-image", request_digest="same-invalid",
        )
        self.assertFalse(replay.created)
        self.assertEqual(replay.status, "failed")
        self.assertIsNone(self.store.claim_job("another-worker"))
        events = self.store.events_after(first.moment_id, self.enrollment.user_id)
        self.assertEqual([event["event"] for event in events], ["accepted", "error"])
        self.assertFalse(events[-1]["data"]["retryable"])

    def test_worker_terminal_updates_require_current_lease_owner(self):
        result = self.store.create_moment(
            user_id=self.enrollment.user_id, note="hello", image_path=None,
            idempotency_key="owner-cas", request_digest="owner-cas",
        )
        job = self.store.claim_job("old-worker")
        self.store.conn.execute(
            "UPDATE app_jobs SET worker_id='new-worker' WHERE id=?", (job.id,)
        )
        self.store.conn.commit()
        self.assertFalse(self.store.append_job_event(
            job, "old-worker", "bubble", {"text": "stale"}
        ))
        self.assertFalse(self.store.complete_owned_job(
            job, "old-worker", scene="", move="speak", memory_entry_id=1,
            preview_path=None, quiet=False,
        ))
        self.assertFalse(self.store.fail_job(
            job, worker_id="old-worker", code="stale", message="stale", retryable=True
        ))
        self.assertEqual(
            [event["event"] for event in self.store.events_after(
                result.moment_id, self.enrollment.user_id
            )],
            ["accepted"],
        )

    def test_sse_resume_and_ttl(self):
        result = self.store.create_moment(
            user_id=self.enrollment.user_id, note="hello", image_path=None,
            idempotency_key="request-0003", request_digest="x",
        )
        self.store.append_event(result.moment_id, "bubble", {"text": "one"})
        self.store.append_event(result.moment_id, "done", {"move": "speak", "scene": ""})
        resumed = self.store.events_after(result.moment_id, self.enrollment.user_id, 1)
        self.assertEqual([event["event"] for event in resumed], ["bubble", "done"])
        self.store.conn.execute(
            "UPDATE app_events SET created_at='2000-01-01T00:00:00+00:00' WHERE moment_id=?",
            (result.moment_id,),
        )
        self.store.conn.commit()
        self.assertEqual(self.store.cleanup_events(timedelta(hours=24)), 3)
        self.assertEqual(self.store.events_after(result.moment_id, self.enrollment.user_id), [])

    def test_cleanup_removes_challenge_at_its_own_expiry(self):
        challenge_id, _, _ = self.store.issue_challenge(
            "request", key_id=self.enrollment.key_id
        )
        self.store.conn.execute(
            "UPDATE app_challenges SET expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), challenge_id),
        )
        self.store.conn.commit()
        self.store.cleanup_events(timedelta(hours=24))
        self.assertIsNone(self.store.conn.execute(
            "SELECT 1 FROM app_challenges WHERE id=?", (challenge_id,)
        ).fetchone())

    def test_proactive_exposes_only_current_and_ack_resets_missed(self):
        moment_id = self.store.create_proactive(self.enrollment.user_id, ["one", "two"])
        current = self.store.current_proactive(self.enrollment.user_id)
        self.assertEqual(current["moment_id"], moment_id)
        self.assertEqual(current["bubbles"], ["one", "two"])
        self.store.conn.execute(
            "UPDATE app_preferences SET consecutive_missed=3 WHERE user_id=?",
            (self.enrollment.user_id,),
        )
        self.store.conn.commit()
        self.store.acknowledge(moment_id, self.enrollment.user_id)
        self.assertIsNone(self.store.current_proactive(self.enrollment.user_id))
        self.assertEqual(self.store.preferences(self.enrollment.user_id)["consecutive_missed"], 0)

    def test_inbound_activity_and_explicit_preferences_resume_four_miss_hold(self):
        self.store.conn.execute(
            "UPDATE app_preferences SET consecutive_missed=4 WHERE user_id=?",
            (self.enrollment.user_id,),
        )
        self.store.conn.commit()
        self.store.create_moment(
            user_id=self.enrollment.user_id, note="I'm back", image_path=None,
            idempotency_key="resume-inbound", request_digest="resume-inbound",
        )
        prefs = self.store.preferences(self.enrollment.user_id)
        self.assertEqual(prefs["consecutive_missed"], 0)
        self.assertEqual(prefs["daily_frequency"], 3)
        self.store.conn.execute(
            "UPDATE app_preferences SET consecutive_missed=4 WHERE user_id=?",
            (self.enrollment.user_id,),
        )
        self.store.conn.commit()
        self.store.update_preferences(
            self.enrollment.user_id, daily_frequency=2,
            quiet_start="22:30", quiet_end="08:30",
        )
        self.assertEqual(
            self.store.preferences(self.enrollment.user_id)["consecutive_missed"], 0
        )

    def test_proactive_timezone_follows_last_seen_device(self):
        self.store.update_device(
            self.enrollment.key_id, apns_token="a" * 64,
            environment="development", timezone="Asia/Shanghai", device_name="old",
        )
        code = self.store.create_invite(kind="device", user_id=self.enrollment.user_id)
        second = self.store.redeem_invite(
            code=code, key_id="dev-tz-second", public_key=None, receipt=None,
            counter=0, environment="development",
        )
        self.store.update_device(
            second.key_id, apns_token="b" * 64, environment="development",
            timezone="America/Los_Angeles", device_name="new",
        )
        users = {user["id"]: user for user in self.store.active_users()}
        self.assertEqual(users[self.enrollment.user_id]["timezone"], "America/Los_Angeles")

    def test_last_device_cannot_be_revoked(self):
        with self.assertRaises(LastDevice):
            self.store.revoke_device(
                self.enrollment.user_id, self.enrollment.device_id
            )
        code = self.store.create_invite(
            kind="device", user_id=self.enrollment.user_id
        )
        second = self.store.redeem_invite(
            code=code, key_id="dev-revocable", public_key=None, receipt=None,
            counter=0, environment="development",
        )
        self.assertTrue(self.store.revoke_device(
            self.enrollment.user_id, second.device_id
        ))

    def test_account_delete_cascades_all_app_tables(self):
        self.store.create_moment(
            user_id=self.enrollment.user_id, note="hello", image_path="/tmp/not-real",
            idempotency_key="request-delete", request_digest="x",
        )
        artefacts = self.store.erase_user(self.enrollment.user_id)
        self.assertIn("/tmp/not-real", artefacts["paths"])
        for table in (
            "app_users", "app_devices", "app_attest_keys", "app_preferences",
            "app_moments", "app_jobs", "app_events",
            "app_push_deliveries",
        ):
            count = self.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)


if __name__ == "__main__":
    unittest.main(verbosity=2)
