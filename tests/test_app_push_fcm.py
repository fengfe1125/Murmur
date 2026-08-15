"""FCM HTTP v1 provider tests.

Run directly: ``python tests/test_app_push_fcm.py``.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from murmur.app_push import ProactiveScheduler  # noqa: E402
from murmur.app_push_fcm import SCOPE, TOKEN_URI, FCMProvider  # noqa: E402
from murmur.app_store import AppStore  # noqa: E402

SEND_HOST = "https://fcm.googleapis.com"


def b64url_decode(value: str) -> bytes:
    raw = value.encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


class Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def token_response(token="ya29.access", expires_in=3600):
    return Response(200, {"access_token": token, "expires_in": expires_in})


def fcm_error(status, code):
    return Response(status, {"error": {
        "status": "ERROR", "message": "nope",
        "details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                     "errorCode": code}],
    }})


class RoutingTransport:
    """Serves the token endpoint from one queue and messages:send from another."""

    def __init__(self, sends=None, tokens=None):
        self.sends = list(sends or [Response(200, {})])
        self.tokens = list(tokens or [token_response()])
        self.token_calls = []
        self.send_calls = []

    def post(self, url, *, headers, content):
        if url == TOKEN_URI:
            self.token_calls.append((headers, content))
            return self.tokens.pop(0) if self.tokens else token_response()
        self.send_calls.append((url, headers, content))
        return self.sends.pop(0) if self.sends else Response(200, {})


class FCMProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pem = cls.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def provider(self, transport, invalid=None):
        return FCMProvider(
            project_id="murmur-app", client_email="push@murmur.iam.gserviceaccount.com",
            private_key_pem=self.pem, private_key_id="KID1",
            transport=transport, on_invalid_token=invalid,
        )

    def test_access_token_is_a_signed_jwt_bearer_grant_and_is_cached(self):
        transport = RoutingTransport(sends=[Response(200, {}), Response(200, {})])
        provider = self.provider(transport)
        provider.send("droid-token", moment_id="m1", preview="hello")
        provider.send("droid-token", moment_id="m2", preview="hello")

        # One exchange serves both sends.
        self.assertEqual(len(transport.token_calls), 1)
        headers, content = transport.token_calls[0]
        self.assertEqual(headers["content-type"], "application/x-www-form-urlencoded")
        fields = dict(
            pair.split("=", 1) for pair in content.decode().split("&")
        )
        self.assertEqual(
            fields["grant_type"], "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
        )

        head, claims, signature = fields["assertion"].split(".")
        self.assertEqual(json.loads(b64url_decode(head)),
                         {"alg": "RS256", "typ": "JWT", "kid": "KID1"})
        body = json.loads(b64url_decode(claims))
        self.assertEqual(body["iss"], "push@murmur.iam.gserviceaccount.com")
        self.assertEqual(body["scope"], SCOPE)
        self.assertEqual(body["aud"], TOKEN_URI)
        self.assertEqual(body["exp"] - body["iat"], 3600)
        # A grant Google would actually accept has to verify.
        self.key.public_key().verify(
            b64url_decode(signature), f"{head}.{claims}".encode("ascii"),
            padding.PKCS1v15(), hashes.SHA256(),
        )
        for _, headers, _ in transport.send_calls:
            self.assertEqual(headers["authorization"], "Bearer ya29.access")

    def test_a_short_lived_token_is_renewed_before_it_expires(self):
        transport = RoutingTransport(
            sends=[Response(200, {}), Response(200, {})],
            tokens=[token_response("first", expires_in=60),
                    token_response("second", expires_in=3600)],
        )
        provider = self.provider(transport)
        provider.send("droid-token", moment_id="m1", preview="hi")
        # 60s of life minus the 60s safety margin: already due for renewal.
        provider.send("droid-token", moment_id="m2", preview="hi")
        self.assertEqual(len(transport.token_calls), 2)
        self.assertEqual(
            transport.send_calls[1][1]["authorization"], "Bearer second"
        )

    def test_message_carries_only_a_preview_and_the_moment_id(self):
        body = json.loads(FCMProvider.payload("proj", "tok", "moment-7", "  在干嘛  "))
        self.assertEqual(body["message"]["token"], "tok")
        self.assertEqual(body["message"]["data"], {"moment_id": "moment-7"})
        self.assertEqual(body["message"]["notification"]["body"], "在干嘛")
        self.assertEqual(body["message"]["android"]["collapse_key"], "moment-7")
        self.assertEqual(body["message"]["android"]["priority"], "HIGH")

    def test_oversized_preview_is_trimmed_under_the_fcm_limit(self):
        raw = FCMProvider.payload("proj", "t" * 300, "m" * 64, "长" * 4000)
        self.assertLessEqual(len(raw), 4096)

    def test_unregistered_token_is_cleared_and_retired(self):
        cleared = []
        transport = RoutingTransport(sends=[fcm_error(404, "UNREGISTERED")])
        result = self.provider(transport, cleared.append).send(
            "dead-token", moment_id="m", preview="hi"
        )
        self.assertEqual(cleared, ["dead-token"])
        self.assertTrue(result.permanent)
        self.assertFalse(result.delivered)
        self.assertEqual(result.reason, "UNREGISTERED")

    def test_invalid_argument_is_permanent_but_keeps_the_registration(self):
        cleared = []
        transport = RoutingTransport(sends=[fcm_error(400, "INVALID_ARGUMENT")])
        result = self.provider(transport, cleared.append).send(
            "odd-token", moment_id="m", preview="hi"
        )
        # Mirrors the APNs 400: the row is retired, the device is not deregistered.
        self.assertEqual(cleared, [])
        self.assertTrue(result.permanent)

    def test_server_errors_stay_retryable(self):
        for status, code in ((503, "UNAVAILABLE"), (429, "QUOTA_EXCEEDED"),
                             (500, "INTERNAL")):
            transport = RoutingTransport(sends=[fcm_error(status, code)])
            result = self.provider(transport).send("t", moment_id="m", preview="hi")
            self.assertFalse(result.permanent, code)
            self.assertEqual(result.status, status)

    def test_expired_access_token_is_refreshed_once_and_the_send_retried(self):
        transport = RoutingTransport(
            sends=[Response(401, {"error": {"status": "UNAUTHENTICATED"}}),
                   Response(200, {})],
            tokens=[token_response("stale"), token_response("fresh")],
        )
        result = self.provider(transport).send("t", moment_id="m", preview="hi")
        self.assertTrue(result.delivered)
        self.assertEqual(len(transport.token_calls), 2)
        self.assertEqual(transport.send_calls[1][1]["authorization"], "Bearer fresh")

    def test_a_persistent_401_gives_up_instead_of_looping(self):
        transport = RoutingTransport(
            sends=[Response(401, {"error": {"status": "UNAUTHENTICATED"}}),
                   Response(401, {"error": {"status": "UNAUTHENTICATED"}})],
            tokens=[token_response("a"), token_response("b")],
        )
        result = self.provider(transport).send("t", moment_id="m", preview="hi")
        self.assertFalse(result.delivered)
        self.assertEqual(len(transport.send_calls), 2)

    def test_message_is_posted_to_the_configured_project(self):
        transport = RoutingTransport()
        self.provider(transport).send("t", moment_id="m", preview="hi")
        self.assertEqual(
            transport.send_calls[0][0],
            f"{SEND_HOST}/v1/projects/murmur-app/messages:send",
        )

    def test_service_account_file_supplies_the_project_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "service-account.json"
            path.write_text(json.dumps({
                "type": "service_account",
                "project_id": "from-file",
                "private_key_id": "KID2",
                "private_key": self.pem.decode(),
                "client_email": "file@murmur.iam.gserviceaccount.com",
                "token_uri": TOKEN_URI,
            }))
            transport = RoutingTransport()
            provider = FCMProvider.from_service_account(path, transport=transport)
            self.assertEqual(provider.project_id, "from-file")
            self.assertEqual(provider.private_key_id, "KID2")
            provider.send("t", moment_id="m", preview="hi")
            self.assertIn("/v1/projects/from-file/", transport.send_calls[0][0])

    def test_a_non_rsa_service_account_key_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric import ec

        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with self.assertRaises(RuntimeError):
            FCMProvider(
                project_id="p", client_email="e@example.com", private_key_pem=pem,
                transport=RoutingTransport(),
            )


class FCMSchedulerTests(unittest.TestCase):
    """The scheduler must reach FCM for an Android device and APNs for an iOS one."""

    @classmethod
    def setUpClass(cls):
        cls.pem = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AppStore(Path(self.tmp.name) / "fcm.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_android_device_is_delivered_through_fcm(self):
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        invite = self.store.create_invite()
        enrolled = self.store.redeem_invite(
            code=invite, key_id="dev-droid", public_key=None, receipt=None,
            counter=0, environment="development", platform="android",
        )
        self.store.update_device(
            enrolled.key_id, push_token="fcm-" + "z" * 60,
            environment="development", timezone="Asia/Shanghai", device_name="pixel",
        )
        self.store.create_moment(
            user_id=enrolled.user_id, note="hello", image_path=None,
            idempotency_key="inbound", request_digest="inbound",
        )
        job = self.store.claim_job("test")
        self.store.finish_job(job, scene="", move="speak", memory_entry_id=1,
                              preview_path=None)
        self.store.replace_slots(
            enrolled.user_id, now.date(), [now - timedelta(seconds=1)]
        )
        transport = RoutingTransport()
        provider = FCMProvider(
            project_id="murmur-app", client_email="push@murmur.iam.gserviceaccount.com",
            private_key_pem=self.pem, transport=transport,
            on_invalid_token=self.store.invalidate_push_token,
        )
        scheduler = ProactiveScheduler(
            self.store, {"android": provider}, lambda _u: (["在干嘛"], "傍晚")
        )
        self.assertEqual(scheduler.run_once(now), 1)
        self.assertEqual(len(transport.send_calls), 1)
        body = json.loads(transport.send_calls[0][2])
        self.assertEqual(body["message"]["notification"]["body"], "在干嘛")
        row = self.store.conn.execute(
            "SELECT status FROM app_push_deliveries"
        ).fetchone()
        self.assertEqual(row["status"], "sent")

    def test_unregistered_android_token_retires_the_delivery(self):
        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        invite = self.store.create_invite()
        enrolled = self.store.redeem_invite(
            code=invite, key_id="dev-gone", public_key=None, receipt=None,
            counter=0, environment="development", platform="android",
        )
        self.store.update_device(
            enrolled.key_id, push_token="fcm-" + "y" * 60,
            environment="development", timezone="Asia/Shanghai", device_name="pixel",
        )
        self.store.create_moment(
            user_id=enrolled.user_id, note="hello", image_path=None,
            idempotency_key="inbound", request_digest="inbound",
        )
        job = self.store.claim_job("test")
        self.store.finish_job(job, scene="", move="speak", memory_entry_id=1,
                              preview_path=None)
        self.store.replace_slots(
            enrolled.user_id, now.date(), [now - timedelta(seconds=1)]
        )
        transport = RoutingTransport(sends=[fcm_error(404, "UNREGISTERED")])
        provider = FCMProvider(
            project_id="murmur-app", client_email="push@murmur.iam.gserviceaccount.com",
            private_key_pem=self.pem, transport=transport,
            on_invalid_token=self.store.invalidate_push_token,
        )
        scheduler = ProactiveScheduler(
            self.store, {"android": provider}, lambda _u: (["在干嘛"], "傍晚")
        )
        scheduler.run_once(now)
        row = self.store.conn.execute(
            "SELECT status FROM app_push_deliveries"
        ).fetchone()
        self.assertEqual(row["status"], "dead")
        device = self.store.conn.execute(
            "SELECT push_token FROM app_devices WHERE key_id='dev-gone'"
        ).fetchone()
        self.assertIsNone(device["push_token"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
