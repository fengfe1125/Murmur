"""APNs provider and restrained proactive schedule tests."""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from murmur.app_push import (  # noqa: E402
    APNsProvider,
    ProactiveScheduler,
    plan_proactive_day,
)
from murmur.app_store import AppStore  # noqa: E402


class Response:
    def __init__(self, status=200, reason=None):
        self.status_code = status
        self.reason = reason
        self.text = json.dumps({"reason": reason}) if reason else ""

    def json(self):
        return {"reason": self.reason} if self.reason else {}


class Transport:
    def __init__(self, responses=None):
        self.responses = list(responses or [Response()])
        self.calls = []

    def post(self, url, *, headers, content):
        self.calls.append((url, headers, content))
        return self.responses.pop(0) if self.responses else Response()


class LeakyTransport:
    def post(self, url, *, headers, content):
        raise RuntimeError(f"transport failed for private URL {url}")


class APNsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        key = ec.generate_private_key(ec.SECP256R1())
        self.key_path = self.root / "AuthKey_TEST.p8"
        self.key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, transport, invalid=None):
        return APNsProvider(
            key_path=self.key_path, key_id="KEY123", team_id="TEAM123",
            topic="com.sakura.Murmur", environment="development",
            transport=transport, on_invalid_token=invalid,
        )

    def test_es256_provider_token_is_raw_jws_and_cached(self):
        transport = Transport([Response(), Response()])
        provider = self.provider(transport)
        provider.send("a" * 64, moment_id="moment", preview="hello")
        provider.send("b" * 64, moment_id="moment", preview="hello")
        first = transport.calls[0]
        self.assertTrue(first[0].startswith("https://api.sandbox.push.apple.com/3/device/"))
        token1 = first[1]["authorization"].split()[1]
        token2 = transport.calls[1][1]["authorization"].split()[1]
        self.assertEqual(token1, token2)
        encoded_signature = token1.split(".")[2]
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        self.assertEqual(len(signature), 64)
        payload = json.loads(first[2])
        self.assertEqual(payload["moment_id"], "moment")
        self.assertLessEqual(len(first[2]), 4096)

    def test_invalid_tokens_are_removed_through_callback(self):
        invalid = []
        transport = Transport([Response(410, "Unregistered")])
        result = self.provider(transport, invalid.append).send(
            "c" * 64, moment_id="moment", preview="hello"
        )
        self.assertFalse(result.delivered)
        self.assertEqual(invalid, ["c" * 64])

    def test_expired_provider_token_is_refreshed_once_immediately(self):
        transport = Transport([
            Response(403, "ExpiredProviderToken"),
            Response(200),
        ])
        result = self.provider(transport).send(
            "f" * 64, moment_id="moment", preview="hello"
        )
        self.assertTrue(result.delivered)
        self.assertEqual(len(transport.calls), 2)
        first = transport.calls[0][1]["authorization"]
        second = transport.calls[1][1]["authorization"]
        self.assertNotEqual(first, second)

    def test_schedule_is_restrained_and_inside_active_window(self):
        tz = ZoneInfo("Asia/Shanghai")
        slots = plan_proactive_day("user", date(2026, 8, 14), tz, 3)
        self.assertEqual(len(slots), 3)
        for slot in slots:
            self.assertGreaterEqual(slot.timetz().replace(tzinfo=None), time(8, 30))
            self.assertLessEqual(slot.timetz().replace(tzinfo=None), time(22, 30))
        self.assertEqual(slots, plan_proactive_day("user", date(2026, 8, 14), tz, 3))
        self.assertEqual(plan_proactive_day("user", date(2026, 8, 14), tz, 0), [])

    def test_scheduler_creates_one_current_message_and_pushes_each_device(self):
        store = AppStore(self.root / "app.db")
        try:
            invite = store.create_invite()
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-push", public_key=None, receipt=None,
                counter=0, environment="development",
            )
            store.update_device(
                enrolled.key_id, push_token="d" * 64, environment="development",
                timezone="Asia/Shanghai", device_name="phone",
            )
            transport = Transport([Response()])
            scheduler = ProactiveScheduler(
                store, self.provider(transport), lambda _user, _tz: (["在干嘛"], "傍晚"),
            )
            # Force one due slot rather than depending on the deterministic jitter.
            now = datetime(2026, 8, 14, 12, tzinfo=UTC)
            store.replace_slots(
                enrolled.user_id, now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                [now - timedelta(seconds=1)],
            )
            # Enrollment alone is not enough: notifications are requested only
            # after the first successful inbound reply.
            self.assertEqual(scheduler.run_once(now), 0)
            self.assertEqual(len(transport.calls), 0)
            store.create_moment(
                user_id=enrolled.user_id, note="hello", image_path=None,
                idempotency_key="first-inbound", request_digest="first",
            )
            job = store.claim_job("test")
            store.finish_job(
                job, scene="", move="speak", memory_entry_id=1, preview_path=None
            )
            self.assertEqual(scheduler.run_once(now), 1)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(store.current_proactive(enrolled.user_id)["bubbles"], ["在干嘛"])
            # One pending current moment suppresses all later slots.
            store.replace_slots(
                enrolled.user_id, now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                [now - timedelta(seconds=1)],
            )
            self.assertEqual(scheduler.run_once(now), 0)
            self.assertEqual(len(transport.calls), 1)
        finally:
            store.close()

    def test_scheduler_without_providers_generates_for_in_app_polling(self):
        store = AppStore(self.root / "app.db")
        try:
            invite = store.create_invite()
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-poll", public_key=None, receipt=None,
                counter=0, environment="development",
            )
            store.update_device(
                enrolled.key_id, push_token="dev-token", environment="development",
                timezone="Asia/Shanghai", device_name="phone",
            )
            scheduler = ProactiveScheduler(
                store, {}, lambda _user, _tz: (["在干嘛"], "傍晚"),
            )
            now = datetime(2026, 8, 14, 12, tzinfo=UTC)
            store.replace_slots(
                enrolled.user_id, now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                [now - timedelta(seconds=1)],
            )
            store.create_moment(
                user_id=enrolled.user_id, note="hello", image_path=None,
                idempotency_key="first-inbound", request_digest="first",
            )
            job = store.claim_job("test")
            store.finish_job(
                job, scene="", move="speak", memory_entry_id=1, preview_path=None
            )
            self.assertEqual(scheduler.run_once(now), 1)
            # Nothing is pushed, but the moment waits for in-app polling.
            self.assertEqual(scheduler.deliver_pending(now), 0)
            current = store.current_proactive(enrolled.user_id)
            self.assertEqual(current["bubbles"], ["在干嘛"])
        finally:
            store.close()

    def test_transport_exception_log_never_contains_device_token(self):
        store = AppStore(self.root / "private-log.db")
        token = "private-device-token-" + "e" * 48
        try:
            invite = store.create_invite()
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-private-log", public_key=None, receipt=None,
                counter=0, environment="development",
            )
            store.update_device(
                enrolled.key_id, push_token=token, environment="development",
                timezone="Asia/Shanghai", device_name=None,
            )
            store.create_moment(
                user_id=enrolled.user_id, note="hello", image_path=None,
                idempotency_key="eligible", request_digest="eligible",
            )
            job = store.claim_job("test")
            store.finish_job(job, scene="", move="speak", memory_entry_id=1,
                             preview_path=None)
            now = datetime.now(UTC)
            store.replace_slots(enrolled.user_id, now.date(), [now - timedelta(seconds=1)])
            scheduler = ProactiveScheduler(
                store, self.provider(LeakyTransport()),
                lambda _user, _tz: (["hello"], "scene"),
            )
            with self.assertLogs("murmur.app_push", level="ERROR") as captured:
                scheduler.run_once(now)
            logs = "\n".join(captured.output)
            self.assertNotIn(token, logs)
            self.assertNotIn("/3/device/", logs)
        finally:
            store.close()

    def enrolled_device_on(self, store, platform, *, token, key_id):
        """Enrol one device on ``platform`` with a proactive moment already due."""
        invite = store.create_invite()
        enrolled = store.redeem_invite(
            code=invite, key_id=key_id, public_key=None, receipt=None,
            counter=0, environment="development", platform=platform,
        )
        store.update_device(
            enrolled.key_id, push_token=token, environment="development",
            timezone="Asia/Shanghai", device_name=platform,
        )
        store.create_moment(
            user_id=enrolled.user_id, note="hello", image_path=None,
            idempotency_key=f"inbound-{platform}", request_digest=platform,
        )
        job = store.claim_job("test")
        store.finish_job(job, scene="", move="speak", memory_entry_id=1,
                         preview_path=None)
        return enrolled

    def test_delivery_routes_on_the_platform_recorded_at_enrolment(self):
        store = AppStore(self.root / "routed.db")
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        try:
            ios = self.enrolled_device_on(
                store, "ios", token="a" * 64, key_id="dev-ios"
            )
            store.replace_slots(ios.user_id, now.date(), [now - timedelta(seconds=1)])
            apns = Transport([Response(200)])
            android = Transport([Response(200)])
            android_provider = self.provider(android)
            android_provider.platform = "android"
            scheduler = ProactiveScheduler(
                store,
                {"ios": self.provider(apns), "android": android_provider},
                lambda _user, _tz: (["routed"], "scene"),
            )
            self.assertEqual(scheduler.run_once(now), 1)
            self.assertEqual(len(apns.calls), 1)
            self.assertEqual(len(android.calls), 0)
        finally:
            store.close()

    def test_platform_without_a_provider_is_retried_not_buried(self):
        store = AppStore(self.root / "unrouted.db")
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        try:
            droid = self.enrolled_device_on(
                store, "android", token="fcm-" + "z" * 60, key_id="dev-droid"
            )
            store.replace_slots(droid.user_id, now.date(), [now - timedelta(seconds=1)])
            apns = Transport([Response(200)])
            # Only iOS is configured, so the Android delivery has nowhere to go.
            scheduler = ProactiveScheduler(
                store, {"ios": self.provider(apns)}, lambda _user, _tz: (["stranded"], "scene")
            )
            self.assertEqual(scheduler.run_once(now), 1)
            self.assertEqual(len(apns.calls), 0)
            # Missing configuration is an operator problem: the delivery stays
            # pending so it flows the moment a provider is added.
            row = store.conn.execute(
                "SELECT status,attempts FROM app_push_deliveries"
            ).fetchone()
            self.assertEqual(tuple(row), ("pending", 1))
        finally:
            store.close()

    def test_transient_push_failure_is_durable_and_replayed_after_restart(self):
        db_path = self.root / "durable-push.db"
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        store = AppStore(db_path)
        try:
            invite = store.create_invite()
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-durable", public_key=None, receipt=None,
                counter=0, environment="development",
            )
            store.update_device(
                enrolled.key_id, push_token="a" * 64, environment="development",
                timezone="Asia/Shanghai", device_name="phone",
            )
            store.create_moment(
                user_id=enrolled.user_id, note="hello", image_path=None,
                idempotency_key="durable-inbound", request_digest="inbound",
            )
            job = store.claim_job("test")
            store.finish_job(
                job, scene="", move="speak", memory_entry_id=1, preview_path=None
            )
            store.replace_slots(enrolled.user_id, now.date(), [now - timedelta(seconds=1)])
            first_transport = Transport([Response(500, "InternalServerError")])
            first = ProactiveScheduler(
                store, self.provider(first_transport),
                lambda _user, _tz: (["durable hello"], "scene"),
            )
            self.assertEqual(first.run_once(now), 1)
            row = store.conn.execute(
                "SELECT status,attempts FROM app_push_deliveries"
            ).fetchone()
            self.assertEqual(tuple(row), ("pending", 1))
            moment_id = store.current_proactive(enrolled.user_id)["moment_id"]
        finally:
            store.close()

        reopened = AppStore(db_path)
        try:
            second_transport = Transport([Response(200)])
            second = ProactiveScheduler(
                reopened, self.provider(second_transport),
                lambda _user, _tz: (_ for _ in ()).throw(
                    AssertionError("must not generate another proactive moment")
                ),
            )
            # Backoff carries ±20% jitter (24–36s here); 40s is past the worst case.
            self.assertEqual(second.run_once(now + timedelta(seconds=40)), 0)
            self.assertEqual(len(second_transport.calls), 1)
            row = reopened.conn.execute(
                "SELECT status,attempts FROM app_push_deliveries WHERE moment_id=?",
                (moment_id,),
            ).fetchone()
            self.assertEqual(tuple(row), ("sent", 2))
            self.assertEqual(reopened.conn.execute(
                "SELECT COUNT(*) FROM app_moments WHERE source='proactive'"
            ).fetchone()[0], 1)
        finally:
            reopened.close()

    def test_delivery_round_stops_when_the_time_budget_is_exhausted(self):
        store = AppStore(self.root / "budget.db")
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        try:
            for key_id, token in (("dev-budget-a", "a" * 64), ("dev-budget-b", "b" * 64)):
                enrolled = self.enrolled_device_on(store, "ios", token=token, key_id=key_id)
                store.replace_slots(
                    enrolled.user_id, now.date(), [now - timedelta(seconds=1)]
                )
            transport = Transport([Response(200), Response(200)])
            # A zero budget still delivers the first candidate, then stops.
            scheduler = ProactiveScheduler(
                store, self.provider(transport), lambda _user, _tz: (["budget"], "scene"),
                delivery_budget_seconds=0.0,
            )
            self.assertEqual(scheduler.run_once(now), 2)
            self.assertEqual(len(transport.calls), 1)
            # The skipped delivery kept its past-due next_attempt_at, so the
            # next round picks it up without waiting out a backoff.
            self.assertEqual(scheduler.deliver_pending(now), 1)
            self.assertEqual(len(transport.calls), 2)
        finally:
            store.close()

    def test_failed_generation_backs_off_instead_of_hammering_the_model(self):
        store = AppStore(self.root / "gen-backoff.db")
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        try:
            enrolled = self.enrolled_device_on(
                store, "ios", token="a" * 64, key_id="dev-gen-backoff"
            )
            store.replace_slots(
                enrolled.user_id, now.date(), [now - timedelta(seconds=1)]
            )
            calls = []

            def failing(_user, _tz):
                calls.append(1)
                raise RuntimeError("model gateway down")

            scheduler = ProactiveScheduler(store, self.provider(Transport()), failing)
            with self.assertLogs("murmur.app_push", level="ERROR"):
                self.assertEqual(scheduler.run_once(now), 0)
                # The slot is still due, but the per-user backoff suppresses
                # an immediate second full model call.
                self.assertEqual(scheduler.run_once(now), 0)
            self.assertEqual(len(calls), 1)
            row = store.conn.execute(
                "SELECT delivered_at FROM app_proactive_slots"
            ).fetchone()
            self.assertIsNone(row["delivered_at"])
            # Once the backoff expires the same-day slot is retried, not lost.
            scheduler._gen_retry_after[enrolled.user_id] = 0.0
            with self.assertLogs("murmur.app_push", level="ERROR"):
                self.assertEqual(scheduler.run_once(now), 0)
            self.assertEqual(len(calls), 2)
        finally:
            store.close()

    def test_generator_receives_the_user_device_timezone(self):
        store = AppStore(self.root / "gen-tz.db")
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        try:
            invite = store.create_invite()
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-gen-tz", public_key=None, receipt=None,
                counter=0, environment="development",
            )
            store.update_device(
                enrolled.key_id, push_token="b" * 64, environment="development",
                timezone="America/New_York", device_name="phone",
            )
            store.create_moment(
                user_id=enrolled.user_id, note="hello", image_path=None,
                idempotency_key="tz-inbound", request_digest="tz",
            )
            job = store.claim_job("test")
            store.finish_job(job, scene="", move="speak", memory_entry_id=1,
                             preview_path=None)
            store.replace_slots(
                enrolled.user_id, now.date(), [now - timedelta(seconds=1)]
            )
            seen = []

            def generating(_user, tz):
                seen.append(tz)
                return (["morning"], "scene")

            scheduler = ProactiveScheduler(
                store, self.provider(Transport([Response()])), generating,
            )
            self.assertEqual(scheduler.run_once(now), 1)
            self.assertEqual(seen, [ZoneInfo("America/New_York")])
        finally:
            store.close()

    def test_invalid_old_token_is_rearmed_when_device_registers_rotated_token(self):
        store = AppStore(self.root / "token-rotation.db")
        old_token, new_token = "1" * 64, "2" * 64
        try:
            invite = store.create_invite()
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-token-rotation", public_key=None,
                receipt=None, counter=0, environment="development",
            )
            store.update_device(
                enrolled.key_id, push_token=old_token, environment="development",
                timezone="Asia/Shanghai", device_name="phone",
            )
            store.create_moment(
                user_id=enrolled.user_id, note="hello", image_path=None,
                idempotency_key="token-rotation-inbound", request_digest="inbound",
            )
            job = store.claim_job("test")
            store.finish_job(
                job, scene="", move="speak", memory_entry_id=1, preview_path=None
            )
            now = datetime(2026, 8, 14, 12, tzinfo=UTC)
            store.replace_slots(enrolled.user_id, now.date(), [now - timedelta(seconds=1)])
            invalid_transport = Transport([Response(410, "Unregistered")])
            scheduler = ProactiveScheduler(
                store,
                self.provider(invalid_transport, store.invalidate_push_token),
                lambda _user, _tz: (["rotate me"], "scene"),
            )
            self.assertEqual(scheduler.run_once(now), 1)
            delivery = store.conn.execute(
                "SELECT status,attempts FROM app_push_deliveries"
            ).fetchone()
            self.assertEqual(tuple(delivery), ("dead", 0))
            self.assertFalse(store.devices(enrolled.user_id)[0]["push_enabled"])

            store.update_device(
                enrolled.key_id, push_token=new_token, environment="development",
                timezone="Asia/Shanghai", device_name="phone",
            )
            delivery = store.conn.execute(
                "SELECT status,attempts,last_status FROM app_push_deliveries"
            ).fetchone()
            self.assertEqual(tuple(delivery), ("pending", 0, None))
            rotated_transport = Transport([Response(200)])
            restarted = ProactiveScheduler(
                store, self.provider(rotated_transport),
                lambda _user, _tz: (_ for _ in ()).throw(
                    AssertionError("must reuse current proactive moment")
                ),
            )
            self.assertEqual(restarted.deliver_pending(datetime.now(UTC)), 1)
            self.assertIn(new_token, rotated_transport.calls[0][0])
            self.assertNotIn(old_token, rotated_transport.calls[0][0])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
