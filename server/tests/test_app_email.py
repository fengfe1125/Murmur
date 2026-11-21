import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur.app_email import EmailError, EmailIdentity
from murmur.app_store import AppStore, DeviceLimit


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AppStore(Path(self.tmp.name) / "app.db")
        self.mail = []
        self.email = EmailIdentity(
            self.store, "test-secret-" * 4, lambda *args: self.mail.append(args)
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def grant(self, key="one", email="test@example.com", user=None):
        proof = self.email.issue(email, key, "test", user)
        return self.email.verify(proof["verification_id"], self.mail[-1][1], key, user)[
            "email_grant"
        ]

    def enroll(self, key, grant):
        return self.email.enroll(
            grant=grant,
            key_id=key,
            public_key=None,
            receipt=None,
            counter=0,
            environment="development",
            platform="ios",
            device_name="Test",
        )

    def test_digest_five_attempts_and_key_binding(self):
        result = self.email.issue("test@example.com", "one", "test")
        code = self.mail[-1][1]
        row = self.store.conn.execute("SELECT * FROM app_email_codes").fetchone()
        self.assertNotEqual(row["digest"], code)
        with self.assertRaises(EmailError):
            self.email.verify(result["verification_id"], code, "other")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            with self.assertRaises(EmailError):
                self.email.verify(result["verification_id"], wrong, "one")
        with self.assertRaises(EmailError):
            self.email.verify(result["verification_id"], code, "one")
        self.assertEqual(
            self.store.conn.execute("SELECT attempts FROM app_email_codes").fetchone()[0], 5
        )

    def test_same_account_limit_recovery_and_single_use(self):
        base = 1800000000
        users = []
        for index in range(4):
            with patch("murmur.app_email.time.time", return_value=base + index * 61):
                key = str(index)
                grant = self.grant(key)
                if index == 3:
                    with self.assertRaises(DeviceLimit):
                        self.enroll(key, grant)
                else:
                    identity = self.enroll(key, grant)
                    users.append(identity.user_id)
                    self.assertEqual(self.store.enrollment_for_key(key), identity)
                    with self.assertRaises(EmailError):
                        self.enroll("stolen", grant)
        self.assertEqual(len(set(users)), 1)

    def test_bind_preserves_user_and_conflict_never_merges(self):
        old = self.store.redeem_invite(
            code=self.store.create_invite(),
            key_id="old",
            public_key=None,
            receipt=None,
            counter=0,
            environment="development",
        )
        grant = self.grant("old", user=old.user_id)
        self.email.bind(grant, "old", old.user_id)
        self.assertEqual(self.store.enrollment_for_key("old").user_id, old.user_id)
        other = self.store.redeem_invite(
            code=self.store.create_invite(),
            key_id="other",
            public_key=None,
            receipt=None,
            counter=0,
            environment="development",
        )
        with patch("murmur.app_email.time.time", return_value=2000000000):
            grant = self.grant("other", user=other.user_id)
            with self.assertRaises(EmailError) as error:
                self.email.bind(grant, "other", other.user_id)
            self.assertEqual(error.exception.code, "email_already_bound")
        self.store.erase_user(old.user_id)
        from murmur.app_email import digest

        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM app_email_sends WHERE email_hash=?",
                (digest("test@example.com"),),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.conn.execute("SELECT COUNT(*) FROM app_emails").fetchone()[0], 0
        )
        self.assertEqual(
            self.store.conn.execute("SELECT COUNT(*) FROM app_email_grants").fetchone()[0], 0
        )

    def test_resend_expiry_and_send_failure(self):
        with patch("murmur.app_email.time.time", return_value=1000):
            result = self.email.issue("test@example.com", "one", "test")
            with self.assertRaises(EmailError):
                self.email.issue("test@example.com", "two", "test")
        with patch("murmur.app_email.time.time", return_value=1601):
            with self.assertRaises(EmailError):
                self.email.verify(result["verification_id"], self.mail[-1][1], "one")
        self.email.send = lambda *args: (_ for _ in ()).throw(RuntimeError("provider error"))
        with self.assertRaises(EmailError):
            self.email.issue("other@example.com", "one", "test")
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM app_email_codes WHERE email='other@example.com'"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
