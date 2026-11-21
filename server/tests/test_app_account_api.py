"""Account features through actual request authentication and HTTP errors."""

import io
import unittest
from dataclasses import replace

import test_app_api as fixtures
from fastapi.testclient import TestClient
from PIL import Image

from murmur.app_api import create_app


class AccountAPITests(unittest.TestCase):
    tearDown = fixtures.AppAPITests.tearDown
    authenticated_headers = fixtures.AppAPITests.authenticated_headers

    def setUp(self):
        fixtures.AppAPITests.setUp(self)
        self.client.close()
        self.mail = []
        self.settings = replace(
            self.settings, email_registration_enabled=True, email_otp_secret="testing-secret-" * 4
        )
        self.client = TestClient(
            create_app(
                self.settings,
                cfg=self.cfg,
                store=self.store,
                email_sender=lambda *args: self.mail.append(args),
            )
        )

    def test_email_enrollment_uses_device_proof_and_recovers(self):
        issued = self.client.post(
            "/v1/email/codes", json={"email": "test@example.com", "key_id": "dev-new-email-key"}
        )
        self.assertEqual(issued.status_code, 200, issued.text)
        verified = self.client.post(
            "/v1/email/verify",
            json={
                "verification_id": issued.json()["verification_id"],
                "key_id": "dev-new-email-key",
                "code": self.mail[-1][1],
            },
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        challenge = self.client.post("/v1/auth/challenges", json={"purpose": "enrollment"}).json()
        payload = {
            "challenge_id": challenge["challenge_id"],
            "email_grant": verified.json()["email_grant"],
            "key_id": "dev-new-email-key",
            "environment": "development",
        }
        rejected = self.client.post("/v1/enrollments", json=payload)
        self.assertEqual(rejected.status_code, 401, rejected.text)
        payload["challenge_id"] = self.client.post(
            "/v1/auth/challenges", json={"purpose": "enrollment"}
        ).json()["challenge_id"]
        enrolled = self.client.post("/v1/enrollments", headers=self.dev_headers, json=payload)
        self.assertEqual(enrolled.status_code, 201, enrolled.text)
        self.identity = enrolled.json()
        recovered = self.client.post(
            "/v1/enrollments/recover", headers=self.authenticated_headers(), json={}
        )
        self.assertEqual(recovered.json(), self.identity)
        quota = self.client.get("/v1/account/quota", headers=self.authenticated_headers())
        self.assertEqual(quota.json()["remaining"], 50)

    def test_binding_cannot_be_completed_by_anonymous_verifier(self):
        issued = self.client.post(
            "/v1/account/email/codes",
            headers=self.authenticated_headers(),
            json={"email": "bind@example.com"},
        )
        self.assertEqual(issued.status_code, 200, issued.text)
        proof = {
            "verification_id": issued.json()["verification_id"],
            "code": self.mail[-1][1],
            "key_id": self.identity["key_id"],
        }
        self.assertEqual(self.client.post("/v1/email/verify", json=proof).status_code, 400)
        verified = self.client.post(
            "/v1/account/email/verify", headers=self.authenticated_headers(), json=proof
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        bound = self.client.post(
            "/v1/account/email",
            headers=self.authenticated_headers(),
            json={"email_grant": verified.json()["email_grant"]},
        )
        self.assertEqual(bound.json()["email"], "bind@example.com")

    def test_sync_binary_preview_and_delete_are_authenticated(self):
        self.assertEqual(self.client.get("/v1/sync/settings").status_code, 401)
        response = self.client.put(
            "/v1/sync/settings", headers=self.authenticated_headers(), json={"enabled": True}
        )
        self.assertEqual(response.status_code, 200, response.text)
        record = {
            "id": "message",
            "author": "you",
            "text": "hello",
            "sent_at": "2026-09-21T12:00:00Z",
        }
        response = self.client.post(
            "/v1/sync/chat/records",
            headers=self.authenticated_headers(),
            json={"generation": 0, "records": [record]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = io.BytesIO()
        Image.new("RGB", (30, 20)).save(data, format="JPEG")
        response = self.client.put(
            "/v1/sync/chat/preview/message?generation=0",
            headers=self.authenticated_headers(),
            content=data.getvalue(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get(
            "/v1/sync/chat/preview/message", headers=self.authenticated_headers()
        )
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        deleted = self.client.request(
            "DELETE",
            "/v1/sync/chat/records",
            headers=self.authenticated_headers(),
            json={"generation": 0},
        )
        self.assertEqual(deleted.json()["chat_generation"], 1)
        stale = self.client.post(
            "/v1/sync/chat/records",
            headers=self.authenticated_headers(),
            json={"generation": 0, "records": [record]},
        )
        self.assertEqual(stale.status_code, 409)


if __name__ == "__main__":
    unittest.main()
