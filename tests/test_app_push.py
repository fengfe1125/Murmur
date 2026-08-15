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
                store, self.provider(transport), lambda _user: (["在干嘛"], "傍晚"),
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
                lambda _user: (["hello"], "scene"),
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
                lambda _user: (["routed"], "scene"),
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
                store, {"ios": self.provider(apns)}, lambda _user: (["stranded"], "scene")
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
                lambda _user: (["durable hello"], "scene"),
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
                lambda _user: (_ for _ in ()).throw(
                    AssertionError("must not generate another proactive moment")
                ),
            )
            self.assertEqual(second.run_once(now + timedelta(seconds=31)), 0)
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
                lambda _user: (["rotate me"], "scene"),
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
                lambda _user: (_ for _ in ()).throw(
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
