"""End-to-end App API wire contract in explicit development mode."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from _helpers import make_config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from murmur.app_api import RateLimiter, create_app, validate_bind_host  # noqa: E402
from murmur.app_lock import UserOperationLock  # noqa: E402
from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore, NotFound  # noqa: E402
from murmur.app_worker import AppWorker, ProcessedMoment  # noqa: E402
from murmur.engine import Reply  # noqa: E402
from murmur.moment import Moment  # noqa: E402


def settings(root: Path) -> AppSettings:
    return AppSettings(
        db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
        data_root=root, upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766", app_id="", team_id="",
        attest_mode="development", attest_root_path=None, allow_development=True,
        development_token="development-token-0123456789",
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        requests_per_minute=500, timezone=ZoneInfo("Asia/Shanghai"),
    )


class AppAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = settings(self.root)
        self.cfg = make_config(self.settings.memory_db_path)
        self.store = AppStore(self.settings.db_path)
        self.app = create_app(self.settings, cfg=self.cfg, store=self.store)
        self.client = TestClient(self.app)
        self.dev_headers = {"X-Murmur-Development-Token": self.settings.development_token}
        invite = self.store.create_invite()
        challenge = self.client.post(
            "/v1/auth/challenges", json={"purpose": "enrollment"}
        ).json()
        response = self.client.post(
            "/v1/enrollments",
            headers=self.dev_headers,
            json={
                "challenge_id": challenge["challenge_id"], "invite_code": invite,
                "key_id": "dev-api-phone", "environment": "development",
                "device_name": "iPhone",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.identity = response.json()

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.tmp.cleanup()

    def authenticated_headers(self, method_path: str = "") -> dict:
        response = self.client.post(
            "/v1/auth/challenges", headers=self.dev_headers,
            json={"purpose": "request", "key_id": self.identity["key_id"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {
            **self.dev_headers,
            "X-Murmur-Key-ID": self.identity["key_id"],
            "X-Murmur-Challenge-ID": response.json()["challenge_id"],
        }

    def test_multipart_moment_idempotency_sse_and_no_history_route(self):
        headers = self.authenticated_headers()
        response = self.client.post(
            "/v1/moments", headers=headers,
            data={"note": "看看这个", "idempotency_key": "api-request-0001"},
            files={"image": ("photo.jpg", b"\xff\xd8\xfffake-jpeg", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        moment_id = response.json()["moment_id"]
        self.assertTrue(Path(self.store.moment_for_user(
            moment_id, self.identity["user_id"]
        )["image_path"]).is_file())
        self.store.append_event(moment_id, "bubble", {"text": "看见了"})
        self.store.append_event(moment_id, "done", {"move": "speak", "scene": "desk"})
        events = self.client.get(
            f"/v1/moments/{moment_id}/events",
            headers={**self.authenticated_headers(), "Last-Event-ID": "1"},
        )
        self.assertEqual(events.status_code, 200, events.text)
        self.assertIn("event: bubble", events.text)
        self.assertIn("event: done", events.text)
        self.assertNotIn("event: accepted", events.text)
        no_history = self.client.get("/v1/history")
        self.assertEqual(no_history.status_code, 404)

    def test_stop_command_disables_push_before_job_runs(self):
        response = self.client.post(
            "/v1/moments", headers=self.authenticated_headers(),
            data={"note": "别发了", "idempotency_key": "api-stop-0001"},
            files={"_multipart": (None, "1")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        prefs = self.store.preferences(self.identity["user_id"])
        self.assertEqual(prefs["daily_frequency"], 0)
        self.assertEqual((prefs["quiet_start"], prefs["quiet_end"]), ("22:30", "08:30"))

    def test_device_preferences_current_ack_and_account_erasure(self):
        device = self.client.put(
            "/v1/device", headers=self.authenticated_headers(),
            json={"apns_token": "a" * 64, "environment": "development",
                  "timezone": "Asia/Shanghai", "device_name": "Phone"},
        )
        self.assertEqual(device.status_code, 200, device.text)
        self.assertIs(type(device.json()["push_enabled"]), bool)
        self.assertTrue(device.json()["push_enabled"])
        listed = self.client.get(
            "/v1/devices", headers=self.authenticated_headers()
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIs(type(listed.json()["devices"][0]["push_enabled"]), bool)
        last_device = self.client.delete(
            f"/v1/devices/{self.identity['device_id']}",
            headers=self.authenticated_headers(),
        )
        self.assertEqual(last_device.status_code, 409, last_device.text)
        self.assertEqual(last_device.json()["error"]["code"], "last_device")
        loaded = self.client.get(
            "/v1/preferences", headers=self.authenticated_headers()
        )
        self.assertEqual(loaded.status_code, 200, loaded.text)
        self.assertEqual(loaded.json()["daily_frequency"], 3)
        self.assertEqual(
            set(loaded.json()), {"daily_frequency", "quiet_start", "quiet_end"}
        )
        prefs = self.client.patch(
            "/v1/preferences", headers=self.authenticated_headers(),
            json={"daily_frequency": 2, "quiet_start": "21:45", "quiet_end": "09:00"},
        )
        self.assertEqual(prefs.status_code, 200, prefs.text)
        reread = self.client.get(
            "/v1/preferences", headers=self.authenticated_headers()
        )
        self.assertEqual(reread.json()["daily_frequency"], 2)
        self.assertEqual(reread.json()["quiet_start"], "21:45")
        proactive = self.store.create_proactive(self.identity["user_id"], ["醒着吗"])
        current = self.client.get(
            "/v1/proactive/current", headers=self.authenticated_headers()
        )
        self.assertEqual(current.json()["moment_id"], proactive)
        ack = self.client.post(
            f"/v1/moments/{proactive}/ack", headers=self.authenticated_headers(), json={}
        )
        self.assertEqual(ack.status_code, 200, ack.text)
        empty = self.client.get(
            "/v1/proactive/current", headers=self.authenticated_headers()
        )
        self.assertEqual(empty.status_code, 204)
        deleted = self.client.delete(
            "/v1/account", headers=self.authenticated_headers()
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) FROM app_users"
        ).fetchone()[0], 0)

    def test_limits_replay_and_uniform_errors(self):
        headers = self.authenticated_headers()
        first = self.client.patch(
            "/v1/preferences", headers=headers, json={"daily_frequency": 3}
        )
        self.assertEqual(first.status_code, 200)
        replay = self.client.patch(
            "/v1/preferences", headers=headers, json={"daily_frequency": 3}
        )
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(set(replay.json()), {"error"})
        too_long = self.client.post(
            "/v1/moments", headers=self.authenticated_headers(),
            data={"note": "x" * 2001, "idempotency_key": "api-too-long"},
            files={"_multipart": (None, "1")},
        )
        self.assertEqual(too_long.status_code, 400)
        error = too_long.json()["error"]
        self.assertEqual(set(error), {"code", "message", "retryable", "request_id"})

    def test_unexpected_exception_log_does_not_include_exception_message(self):
        original = self.store.preferences

        def fail(_user_id):
            raise RuntimeError("secret user body and token")

        self.store.preferences = fail
        try:
            with self.assertLogs("murmur.app_api", level="ERROR") as captured:
                response = self.client.patch(
                    "/v1/preferences", headers=self.authenticated_headers(), json={}
                )
            self.assertEqual(response.status_code, 500)
            logs = "\n".join(captured.output)
            self.assertNotIn("secret user body", logs)
            self.assertNotIn("token", logs)
        finally:
            self.store.preferences = original

    def test_failed_worker_retry_rearms_same_moment_and_hides_old_error(self):
        key = "api-worker-retry-0001"
        payload = b"\xff\xd8\xffsame-private-image"
        first = self.client.post(
            "/v1/moments", headers=self.authenticated_headers(),
            data={"note": "same", "idempotency_key": key},
            files={"image": ("photo.jpg", payload, "image/jpeg")},
        )
        self.assertEqual(first.status_code, 202, first.text)
        moment_id = first.json()["moment_id"]

        def fail(*_args):
            raise RuntimeError("private model detail")

        AppWorker(
            self.store, self.cfg, self.settings, processor=fail
        ).process_one()
        self.assertEqual(
            self.store.events_after(moment_id, self.identity["user_id"])[-1]["event"],
            "error",
        )
        conflict = self.client.post(
            "/v1/moments", headers=self.authenticated_headers(),
            data={"note": "changed", "idempotency_key": key},
            files={"image": ("photo.jpg", payload, "image/jpeg")},
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(list(self.settings.upload_dir.glob("murmur-upload-*")), [])

        retried = self.client.post(
            "/v1/moments", headers=self.authenticated_headers(),
            data={"note": "same", "idempotency_key": key},
            files={"image": ("photo.jpg", payload, "image/jpeg")},
        )
        self.assertEqual(retried.status_code, 202, retried.text)
        self.assertEqual(retried.json()["moment_id"], moment_id)
        self.assertEqual(
            [event["event"] for event in self.store.events_after(
                moment_id, self.identity["user_id"]
            )],
            ["accepted"],
        )

        def succeed(_job, _memory, _on_bubble):
            return ProcessedMoment(
                Reply("desk", "speak", ["recovered"]),
                Moment.text_only(self.cfg.tz), None,
            )

        AppWorker(
            self.store, self.cfg, self.settings, processor=succeed
        ).process_one()
        stream = self.client.get(
            f"/v1/moments/{moment_id}/events",
            headers=self.authenticated_headers(),
        )
        self.assertEqual(stream.status_code, 200, stream.text)
        self.assertIn("event: accepted", stream.text)
        self.assertIn("event: bubble", stream.text)
        self.assertIn("event: done", stream.text)
        self.assertNotIn("event: error", stream.text)
        self.assertEqual(list(self.settings.upload_dir.glob("murmur-upload-*")), [])

    def test_account_cleanup_failure_keeps_identity_for_authenticated_retry(self):
        blocked = self.settings.upload_dir / "murmur-upload-blocked-directory"
        blocked.mkdir()
        self.store.create_moment(
            user_id=self.identity["user_id"], note="erase me", image_path=str(blocked),
            idempotency_key="delete-retry-artifact", request_digest="delete-retry",
        )
        with self.assertLogs("murmur.app_api", level="ERROR") as captured:
            first = self.client.delete(
                "/v1/account", headers=self.authenticated_headers()
            )
        self.assertEqual(first.status_code, 500, first.text)
        self.store.auth_key(self.identity["key_id"])
        row = self.store.conn.execute(
            "SELECT deleting FROM app_users WHERE id=?", (self.identity["user_id"],)
        ).fetchone()
        self.assertEqual(row["deleting"], 1)
        self.assertNotIn(str(blocked), "\n".join(captured.output))
        blocked.rmdir()
        retried = self.client.delete(
            "/v1/account", headers=self.authenticated_headers()
        )
        self.assertEqual(retried.status_code, 204, retried.text)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) FROM app_users"
        ).fetchone()[0], 0)

    def test_event_stream_ends_quietly_when_erasure_removes_the_moment(self):
        created = self.client.post(
            "/v1/moments", headers=self.authenticated_headers(),
            data={"note": "边删边听", "idempotency_key": "api-stream-erase-01"},
            files={"_multipart": (None, "1")},
        )
        self.assertEqual(created.status_code, 202, created.text)
        moment_id = created.json()["moment_id"]
        # Account erasure can delete the moment mid-stream; the SSE generator
        # must end quietly instead of faulting the connection.
        with patch.object(
            self.store, "events_after", side_effect=NotFound("moment not found")
        ):
            with self.assertNoLogs("murmur.app_api", level="ERROR"):
                stream = self.client.get(
                    f"/v1/moments/{moment_id}/events",
                    headers=self.authenticated_headers(),
                )
        self.assertEqual(stream.status_code, 200, stream.text)
        self.assertEqual(stream.text, "")

    def test_unauthenticated_rate_limit_cannot_be_bypassed_with_random_key_headers(self):
        root = self.root / "rate-limit"
        limited_settings = replace(
            self.settings,
            db_path=root / "app.db",
            memory_db_path=root / "memory.db",
            data_root=root,
            upload_dir=root / "uploads",
            requests_per_minute=2,
        )
        with AppStore(limited_settings.db_path) as limited_store:
            limited_app = create_app(
                limited_settings, cfg=self.cfg, store=limited_store
            )
            with TestClient(limited_app) as client:
                statuses = [
                    client.post(
                        "/v1/auth/challenges",
                        headers={"X-Murmur-Key-ID": f"random-{index}"},
                        json={"purpose": "enrollment"},
                    ).status_code
                    for index in range(3)
                ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_rate_limiter_key_space_is_bounded_and_expires_old_buckets(self):
        limiter = RateLimiter(limit=30, window_seconds=60, max_keys=8)
        with patch("murmur.app_api.time.monotonic", return_value=0):
            for index in range(100):
                limiter.check(f"ip:{index}")
        self.assertEqual(limiter.tracked_keys, 8)
        with patch("murmur.app_api.time.monotonic", return_value=120):
            limiter.check("ip:fresh")
        self.assertEqual(limiter.tracked_keys, 1)

    def test_enrollment_response_loss_can_recover_with_enrolled_key_assertion(self):
        recovered = self.client.post(
            "/v1/enrollments/recover",
            headers=self.authenticated_headers(),
            json={},
        )
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json(), self.identity)
        unknown = self.client.post(
            "/v1/auth/challenges",
            headers=self.dev_headers,
            json={"purpose": "request", "key_id": "dev-never-enrolled"},
        )
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(unknown.json()["error"]["code"], "attestation_key_unknown")

    def test_global_body_gate_bounds_three_chunked_anonymous_uploads(self):
        tracker = {"active": 0, "maximum": 0}
        guard = asyncio.Lock()

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                async with guard:
                    tracker["active"] += 1
                    tracker["maximum"] = max(tracker["maximum"], tracker["active"])
                try:
                    for _ in range(3):
                        yield b"not-a-real-multipart-body"
                        await asyncio.sleep(0.04)
                finally:
                    async with guard:
                        tracker["active"] -= 1

        async def exercise():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                requests = [
                    client.post(
                        "/v1/moments",
                        headers={"Content-Type": "multipart/form-data; boundary=test"},
                        content=SlowBody(),
                    )
                    for _ in range(3)
                ]
                return await asyncio.gather(*requests)

        responses = asyncio.run(exercise())
        self.assertEqual([response.status_code for response in responses], [401, 401, 401])
        self.assertEqual(tracker["maximum"], self.settings.max_concurrent_uploads)

    def test_json_body_limit_is_independent_from_25mb_upload_limit(self):
        response = self.client.post(
            "/v1/auth/challenges",
            content=b"x" * (self.settings.max_json_body_bytes + 1),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()["error"]["code"], "body_too_large")

    def test_image_one_byte_over_limit_returns_413_and_deletes_temp(self):
        self.assertEqual(AppSettings.__dataclass_fields__["max_image_bytes"].default, 25 * 1024 * 1024)
        limited = replace(self.settings, max_image_bytes=2048)
        limited_app = create_app(limited, cfg=self.cfg, store=self.store)
        with TestClient(limited_app) as client:
            invite = self.store.create_invite()
            challenge = client.post(
                "/v1/auth/challenges", json={"purpose": "enrollment"}
            ).json()
            identity = client.post(
                "/v1/enrollments",
                headers=self.dev_headers,
                json={
                    "challenge_id": challenge["challenge_id"], "invite_code": invite,
                    "key_id": "dev-api-size-limit", "environment": "development",
                    "device_name": "iPhone",
                },
            ).json()
            challenge = client.post(
                "/v1/auth/challenges", headers=self.dev_headers,
                json={"purpose": "request", "key_id": identity["key_id"]},
            ).json()
            headers = {
                **self.dev_headers,
                "X-Murmur-Key-ID": identity["key_id"],
                "X-Murmur-Challenge-ID": challenge["challenge_id"],
            }
            payload = b"\xff\xd8\xff" + b"x" * 2046
            response = client.post(
                "/v1/moments", headers=headers,
                data={"note": "过大", "idempotency_key": "api-image-oversize"},
                files={"image": ("photo.jpg", payload, "image/jpeg")},
            )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()["error"]["code"], "image_too_large")
        self.assertEqual(list(limited.upload_dir.glob("murmur-upload-*")), [])

    def test_chunk_idle_timeout_aborts_before_authentication(self):
        timed_settings = replace(
            self.settings,
            body_read_timeout_seconds=1,
            body_idle_timeout_seconds=0.02,
            body_speed_grace_seconds=0.5,
        )
        timed_app = create_app(timed_settings, cfg=self.cfg, store=self.store)

        class StalledBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first"
                await asyncio.sleep(0.1)
                yield b"second"

        async def exercise():
            transport = httpx.ASGITransport(app=timed_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.post(
                    "/v1/moments",
                    headers={"Content-Type": "multipart/form-data; boundary=test"},
                    content=StalledBody(),
                )

        response = asyncio.run(exercise())
        self.assertEqual(response.status_code, 408, response.text)
        self.assertEqual(response.json()["error"]["code"], "body_timeout")

    def test_delete_waiting_for_user_lock_does_not_block_other_api_requests(self):
        locked = threading.Event()
        release = threading.Event()

        def hold_lock():
            with UserOperationLock(self.settings.data_root, self.identity["user_id"]):
                locked.set()
                release.wait(3)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(locked.wait(1))
        delete_headers = self.authenticated_headers()

        async def exercise():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                deleting = asyncio.create_task(client.delete(
                    "/v1/account", headers=delete_headers
                ))
                await asyncio.sleep(0.05)
                other = await asyncio.wait_for(client.post(
                    "/v1/auth/challenges", json={"purpose": "enrollment"}
                ), timeout=1)
                release.set()
                return other, await asyncio.wait_for(deleting, timeout=2)

        try:
            other, deleted = asyncio.run(exercise())
        finally:
            release.set()
            holder.join(2)
        self.assertEqual(other.status_code, 200, other.text)
        self.assertEqual(deleted.status_code, 204, deleted.text)

    def test_production_api_bind_is_loopback_only(self):
        production = replace(
            self.settings,
            attest_mode="production",
            allow_development=False,
            development_token=None,
            app_id="TEAM.com.sakura.Murmur",
            team_id="TEAM",
            public_base_url="https://murmur.example",
            apns_environment="production",
        )
        validate_bind_host(production, "127.0.0.1")
        validate_bind_host(production, "::1")
        with self.assertRaises(RuntimeError):
            validate_bind_host(production, "0.0.0.0")
        validate_bind_host(self.settings, "0.0.0.0")


class PlatformWireTests(unittest.TestCase):
    """The wire contract for a second client platform, in development mode."""

    FCM_TOKEN = "cZx1kQ_gTb2:APA91bH" + "z" * 140

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = settings(self.root)
        self.cfg = make_config(self.settings.memory_db_path)
        self.store = AppStore(self.settings.db_path)
        self.app = create_app(self.settings, cfg=self.cfg, store=self.store)
        self.client = TestClient(self.app)
        self.dev_headers = {
            "X-Murmur-Development-Token": self.settings.development_token
        }

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.tmp.cleanup()

    def enroll(self, key_id, **extra):
        invite = self.store.create_invite()
        challenge = self.client.post(
            "/v1/auth/challenges", json={"purpose": "enrollment"}
        ).json()
        return self.client.post(
            "/v1/enrollments", headers=self.dev_headers,
            json={"challenge_id": challenge["challenge_id"], "invite_code": invite,
                  "key_id": key_id, "environment": "development", **extra},
        )

    def headers_for(self, key_id):
        response = self.client.post(
            "/v1/auth/challenges", headers=self.dev_headers,
            json={"purpose": "request", "key_id": key_id},
        )
        return {**self.dev_headers, "X-Murmur-Key-ID": key_id,
                "X-Murmur-Challenge-ID": response.json()["challenge_id"]}

    def test_enrolment_without_a_platform_is_still_ios(self):
        """The shipped iOS client sends no platform field and must keep working."""
        response = self.enroll("dev-legacy-phone")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            self.store.auth_key(response.json()["key_id"]).platform, "ios"
        )

    def test_android_enrolment_is_recorded_as_android(self):
        response = self.enroll("dev-droid", platform="android")
        self.assertEqual(response.status_code, 201, response.text)
        key_id = response.json()["key_id"]
        self.assertEqual(self.store.auth_key(key_id).platform, "android")
        device = self.client.get(
            "/v1/devices", headers=self.headers_for(key_id)
        ).json()["devices"][0]
        self.assertEqual(device["platform"], "android")

    def test_an_unknown_platform_is_refused(self):
        response = self.enroll("dev-weird", platform="symbian")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_fcm_token_is_accepted_for_android_and_refused_for_ios(self):
        droid = self.enroll("dev-droid-token", platform="android").json()["key_id"]
        accepted = self.client.put(
            "/v1/device", headers=self.headers_for(droid),
            json={"push_token": self.FCM_TOKEN, "environment": "development",
                  "timezone": "Asia/Shanghai", "device_name": "Pixel"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["push_enabled"])
        self.assertEqual(accepted.json()["platform"], "android")

        phone = self.enroll("dev-ios-token").json()["key_id"]
        refused = self.client.put(
            "/v1/device", headers=self.headers_for(phone),
            json={"push_token": self.FCM_TOKEN, "environment": "development",
                  "timezone": "Asia/Shanghai", "device_name": "iPhone"},
        )
        self.assertEqual(refused.status_code, 400, refused.text)

    def test_ios_client_may_still_spell_the_field_apns_token(self):
        phone = self.enroll("dev-legacy-field").json()["key_id"]
        response = self.client.put(
            "/v1/device", headers=self.headers_for(phone),
            json={"apns_token": "a" * 64, "environment": "development",
                  "timezone": "Asia/Shanghai", "device_name": "iPhone"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["push_enabled"])

    def test_a_short_or_malformed_android_token_is_refused(self):
        droid = self.enroll("dev-droid-bad", platform="android").json()["key_id"]
        for bad in ("short", "!" * 200, "a" * 600):
            response = self.client.put(
                "/v1/device", headers=self.headers_for(droid),
                json={"push_token": bad, "environment": "development",
                      "timezone": "Asia/Shanghai", "device_name": "Pixel"},
            )
            self.assertEqual(response.status_code, 400, bad)

    def test_the_platform_comes_from_the_key_not_the_request(self):
        """A caller cannot widen its own token rules by claiming a platform."""
        phone = self.enroll("dev-claimer").json()["key_id"]
        response = self.client.put(
            "/v1/device", headers=self.headers_for(phone),
            json={"push_token": self.FCM_TOKEN, "platform": "android",
                  "environment": "development", "timezone": "Asia/Shanghai"},
        )
        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
