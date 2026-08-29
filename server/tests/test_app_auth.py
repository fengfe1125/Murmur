"""Development authentication and request replay protection."""

from __future__ import annotations

import base64
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.app_auth import (  # noqa: E402
    AppAuthenticator,
    AppAuthError,
    AttestationResult,
)
from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore, ChallengeInvalid  # noqa: E402


def settings(root: Path, *, production: bool = False) -> AppSettings:
    return AppSettings(
        db_path=root / "app.db", memory_db_path=root / "memory.db", data_root=root,
        upload_dir=root / "uploads", public_base_url=(
            "https://murmur.example" if production else "http://127.0.0.1:8766"
        ),
        app_id="TEAM.com.sakura.Murmur", team_id="TEAM",
        attest_mode="production" if production else "development",
        attest_root_path=None, allow_development=not production,
        development_token=None if production else "d" * 32,
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        timezone=ZoneInfo("Asia/Shanghai"),
    )


class FakeVerifier:
    def __init__(self):
        self.assertion_hash = None

    def verify_attestation(self, attestation, *, key_id, client_data_hash):
        self.attestation_hash = client_data_hash
        return AttestationResult(b"public", b"receipt", 0, "production")

    def verify_assertion(
        self, assertion, *, public_key, client_data_hash, previous_counter
    ):
        self.assertion_hash = client_data_hash
        self.assertEqual = (assertion, public_key, previous_counter)
        return previous_counter + 1


class AppAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_development_is_explicit_and_challenges_cannot_replay(self):
        cfg = settings(self.root)
        cfg.validate()
        with AppStore(cfg.db_path) as store:
            invite = store.create_invite()
            challenge_id, _challenge, _ = store.issue_challenge("enrollment")
            auth = AppAuthenticator(cfg, store)
            result = auth.enroll(
                challenge_id=challenge_id, key_id="dev-phone-001", attestation_b64=None,
                development_token="d" * 32,
            )
            enrolled = store.redeem_invite(
                code=invite, key_id="dev-phone-001", public_key=result.public_key or None,
                receipt=result.receipt, counter=result.counter, environment=result.environment,
            )
            request_id, challenge, _ = store.issue_challenge(
                "request", key_id=enrolled.key_id
            )
            context = auth.authenticate(
                method="POST", path="/v1/moments", body=b"body",
                key_id=enrolled.key_id, challenge_id=request_id, assertion_b64=None,
                development_token="d" * 32,
            )
            self.assertEqual(context.user_id, enrolled.user_id)
            expected = hashlib.sha256(
                challenge + b"POST" + b"/v1/moments" + hashlib.sha256(b"body").digest()
            ).digest()
            self.assertEqual(
                auth.request_client_data_hash(challenge, "POST", "/v1/moments", b"body"),
                expected,
            )
            with self.assertRaises(ChallengeInvalid):
                auth.authenticate(
                    method="POST", path="/v1/moments", body=b"body",
                    key_id=enrolled.key_id, challenge_id=request_id, assertion_b64=None,
                    development_token="d" * 32,
                )

    def test_wrong_development_token_burns_challenge(self):
        cfg = settings(self.root)
        with AppStore(cfg.db_path) as store:
            store.create_invite()
            challenge_id, _, _ = store.issue_challenge("enrollment")
            auth = AppAuthenticator(cfg, store)
            with self.assertRaises(AppAuthError):
                auth.enroll(
                    challenge_id=challenge_id, key_id="dev-phone-002", attestation_b64=None,
                    development_token="wrong",
                )
            with self.assertRaises(ChallengeInvalid):
                auth.enroll(
                    challenge_id=challenge_id, key_id="dev-phone-002", attestation_b64=None,
                    development_token="d" * 32,
                )
            # The invite was not consumed even though the security challenge was.
            self.assertIsNone(store.conn.execute(
                "SELECT redeemed_at FROM app_invites"
            ).fetchone()["redeemed_at"])

    def test_production_uses_verifier_and_advances_counter_atomically(self):
        cfg = settings(self.root, production=True)
        verifier = FakeVerifier()
        with AppStore(cfg.db_path) as store:
            invite = store.create_invite()
            enrollment_challenge, challenge, _ = store.issue_challenge("enrollment")
            auth = AppAuthenticator(cfg, store, verifier=verifier)
            result = auth.enroll(
                challenge_id=enrollment_challenge, key_id="production-key",
                attestation_b64=base64.urlsafe_b64encode(b"attestation").decode(),
                development_token=None,
            )
            self.assertEqual(verifier.attestation_hash, hashlib.sha256(challenge).digest())
            enrolled = store.redeem_invite(
                code=invite, key_id="production-key", public_key=result.public_key,
                receipt=result.receipt, counter=0, environment="production",
            )
            request_id, _, _ = store.issue_challenge("request", key_id=enrolled.key_id)
            auth.authenticate(
                method="PATCH", path="/v1/preferences", body=b"{}",
                key_id=enrolled.key_id, challenge_id=request_id,
                assertion_b64=base64.urlsafe_b64encode(b"assertion").decode(),
                development_token=None,
            )
            self.assertEqual(store.auth_key(enrolled.key_id).counter, 1)

    def test_production_configuration_rejects_development_bypass(self):
        cfg = settings(self.root, production=True)
        bad = AppSettings(**{
            **cfg.__dict__, "allow_development": True, "development_token": "x" * 32
        })
        with self.assertRaises(RuntimeError):
            bad.validate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
