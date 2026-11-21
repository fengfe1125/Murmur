"""Email proofs augment, rather than replace, device attestation."""

import hashlib
import hmac
import re
import secrets
import time
import uuid

from .app_store import Conflict, DeviceLimit, Enrollment, _iso


class EmailError(RuntimeError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_emails (
            email TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL REFERENCES app_users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS app_email_codes (
            id TEXT PRIMARY KEY, email TEXT NOT NULL, key_id TEXT NOT NULL,
            user_id TEXT REFERENCES app_users(id) ON DELETE CASCADE,
            digest TEXT NOT NULL, created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS app_email_codes_email ON app_email_codes(email,created);
        CREATE TABLE IF NOT EXISTS app_email_sends (
            id TEXT PRIMARY KEY, email_hash TEXT NOT NULL, source_hash TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS app_email_sends_created ON app_email_sends(created);
        CREATE TABLE IF NOT EXISTS app_email_grants (
            digest TEXT PRIMARY KEY, email TEXT NOT NULL, key_id TEXT NOT NULL,
            user_id TEXT REFERENCES app_users(id) ON DELETE CASCADE, expires REAL NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS app_email_erase_proofs BEFORE DELETE ON app_users
        BEGIN
            DELETE FROM app_email_codes WHERE email IN (SELECT email FROM app_emails WHERE user_id=OLD.id);
            DELETE FROM app_email_grants WHERE email IN (SELECT email FROM app_emails WHERE user_id=OLD.id);
        END;
    """)


def normalize(email):
    if not isinstance(email, str) or len(email) > 254:
        raise ValueError("邮箱格式无效。")
    email = email.strip().lower()
    if not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", email
    ):
        raise ValueError("邮箱格式无效。")
    return email


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class EmailIdentity:
    def __init__(self, store, secret, send):
        self.store, self.secret, self.send = store, secret, send

    def proof(self, challenge, code):
        return hmac.new(
            self.secret.encode(), f"{challenge}:{code}".encode(), hashlib.sha256
        ).hexdigest()

    def issue(self, email, key_id, source, user_id=None):
        if len(self.secret) < 32:
            raise EmailError("email_unavailable", "邮箱服务尚未配置。", 503)
        email = normalize(email)
        if not isinstance(key_id, str) or not 1 <= len(key_id) <= 180:
            raise ValueError("设备密钥无效。")
        now, challenge, code = time.time(), str(uuid.uuid4()), f"{secrets.randbelow(1000000):06d}"
        with self.store._tx() as db:
            if user_id:
                self.store.require_user_ready(user_id)
            db.execute("DELETE FROM app_email_sends WHERE created<?", (now - 86400,))
            db.execute("DELETE FROM app_email_codes WHERE created<?", (now - 600,))
            db.execute("DELETE FROM app_email_grants WHERE expires<?", (now,))
            mailbox, origin = digest(email), digest(source)
            recent = db.execute(
                "SELECT MAX(created) FROM app_email_sends WHERE email_hash=?", (mailbox,)
            ).fetchone()[0]
            hourly = db.execute(
                "SELECT COUNT(*) FROM app_email_sends WHERE email_hash=? AND created>?",
                (mailbox, now - 3600),
            ).fetchone()[0]
            sources = db.execute(
                "SELECT COUNT(*) FROM app_email_sends WHERE source_hash=? AND created>?",
                (origin, now - 3600),
            ).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM app_email_sends").fetchone()[0]
            if (
                (recent is not None and now - recent < 60)
                or hourly >= 10
                or sources >= 30
                or total >= 1000
            ):
                raise EmailError("email_rate_limited", "发送太频繁，请稍后再试。", 429)
            db.execute(
                "INSERT INTO app_email_sends VALUES(?,?,?,?)", (challenge, mailbox, origin, now)
            )
            db.execute("DELETE FROM app_email_codes WHERE email=? AND key_id=?", (email, key_id))
            db.execute(
                "INSERT INTO app_email_codes(id,email,key_id,user_id,digest,created) VALUES(?,?,?,?,?,?)",
                (challenge, email, key_id, user_id, self.proof(challenge, code), now),
            )
        try:
            self.send(email, code, challenge)
        except Exception:
            with self.store._tx() as db:
                db.execute("DELETE FROM app_email_codes WHERE id=?", (challenge,))
            raise EmailError("email_send_failed", "验证码暂时未能发送，请稍后重试。", 503) from None
        return {"verification_id": challenge, "expires_in": 600, "resend_after": 60}

    def verify(self, challenge, code, key_id, user_id=None):
        grant, valid = secrets.token_urlsafe(32), False
        with self.store._tx() as db:
            row = db.execute("SELECT * FROM app_email_codes WHERE id=?", (challenge,)).fetchone()
            if (
                row
                and row["key_id"] == key_id
                and row["user_id"] == user_id
                and row["created"] > time.time() - 600
                and row["attempts"] < 5
                and not row["verified"]
            ):
                db.execute(
                    "UPDATE app_email_codes SET attempts=attempts+1 WHERE id=?", (challenge,)
                )
                valid = (
                    isinstance(code, str)
                    and bool(re.fullmatch(r"[0-9]{6}", code))
                    and hmac.compare_digest(row["digest"], self.proof(challenge, code))
                )
                if valid:
                    db.execute("UPDATE app_email_codes SET verified=1 WHERE id=?", (challenge,))
                    db.execute(
                        "INSERT INTO app_email_grants VALUES(?,?,?,?,?)",
                        (digest(grant), row["email"], key_id, user_id, time.time() + 300),
                    )
        # Failed attempts commit before returning an error.
        if not valid:
            raise EmailError("email_code_invalid", "验证码错误、已使用或已过期。")
        return {"email_grant": grant, "expires_in": 300}

    def consume(self, db, grant, key_id, user_id=None):
        row = db.execute(
            "SELECT * FROM app_email_grants WHERE digest=?", (digest(grant),)
        ).fetchone()
        if (
            not row
            or row["expires"] <= time.time()
            or row["key_id"] != key_id
            or row["user_id"] != user_id
        ):
            raise EmailError("email_grant_invalid", "邮箱验证已过期，请重新验证。", 401)
        db.execute("DELETE FROM app_email_grants WHERE digest=?", (digest(grant),))
        return row["email"]

    def bind(self, grant, key_id, user_id):
        with self.store._tx() as db:
            self.store.require_user_ready(user_id)
            email = self.consume(db, grant, key_id, user_id)
            owner = db.execute("SELECT user_id FROM app_emails WHERE email=?", (email,)).fetchone()
            current = db.execute(
                "SELECT email FROM app_emails WHERE user_id=?", (user_id,)
            ).fetchone()
            if owner and owner[0] != user_id:
                raise EmailError(
                    "email_already_bound", "这个邮箱已属于其他账号，无法自动合并。", 409
                )
            if current and current[0] != email:
                raise EmailError("email_change_unsupported", "当前版本不支持更换邮箱。", 409)
            db.execute("INSERT OR IGNORE INTO app_emails VALUES(?,?)", (email, user_id))
        return {"email": email}

    def enroll(
        self, *, grant, key_id, public_key, receipt, counter, environment, platform, device_name
    ):
        with self.store._tx() as db:
            email = self.consume(db, grant, key_id)
            if db.execute("SELECT 1 FROM app_attest_keys WHERE key_id=?", (key_id,)).fetchone():
                raise Conflict("key is already enrolled")
            owner = db.execute("SELECT user_id FROM app_emails WHERE email=?", (email,)).fetchone()
            user_id, now = owner[0] if owner else str(uuid.uuid4()), _iso()
            if owner:
                self.store.require_user_ready(user_id)
            else:
                db.execute("INSERT INTO app_users(id,created_at) VALUES(?,?)", (user_id, now))
                db.execute(
                    "INSERT INTO app_preferences(user_id,updated_at) VALUES(?,?)", (user_id, now)
                )
                db.execute("INSERT INTO app_emails VALUES(?,?)", (email, user_id))
            if (
                db.execute(
                    "SELECT COUNT(*) FROM app_devices WHERE user_id=? AND active=1", (user_id,)
                ).fetchone()[0]
                >= 3
            ):
                raise DeviceLimit("at most three active devices are allowed")
            device_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO app_attest_keys(key_id,user_id,public_key,receipt,counter,environment,platform,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (key_id, user_id, public_key, receipt, counter, environment, platform, now),
            )
            db.execute(
                "INSERT INTO app_devices(id,user_id,key_id,environment,platform,device_name,last_seen_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (device_id, user_id, key_id, environment, platform, device_name, now, now),
            )
        return Enrollment(user_id, device_id, key_id)


def resend_sender(api_key, sender):
    def send(email, code, request_id):
        import httpx

        if not api_key:
            raise RuntimeError("Resend is not configured")
        with httpx.Client(timeout=15) as client:
            response = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": request_id},
                json={
                    "from": sender,
                    "to": [email],
                    "subject": "Murmur 邮箱验证码",
                    "text": f"你的 Murmur 验证码是 {code}，10 分钟内有效。若非本人操作，请忽略这封邮件。",
                },
            )
            response.raise_for_status()

    return send
