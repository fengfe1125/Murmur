"""Persistent state for the first-party Murmur app.

The app API deliberately has its own, prefixed tables.  They may live in the
same SQLite file as :mod:`murmur.memory`, but none of the public API exposes
the conversation entries kept there.
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat(timespec="microseconds")


# A "permanent" invitation still carries a concrete expiry so that the ordinary
# comparisons keep working; the year 9999 simply never arrives.
PERMANENT_EXPIRY = datetime(9999, 12, 31, tzinfo=UTC)

# A job reclaimed this many times killed the worker process itself (PIL
# segfault, OOM kill) before any Python-level fail_job() could run.  Past the
# cap the moment goes terminally failed instead of re-burning the model on
# every lease expiry.
MAX_JOB_ATTEMPTS = 5

# Push retries back off exponentially with jitter and give up here; the 24h
# moment expiry stays as the last resort, not the primary stop.
MAX_PUSH_ATTEMPTS = 12


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AppStoreError(RuntimeError):
    code = "store_error"


class NotFound(AppStoreError):
    code = "not_found"


class Conflict(AppStoreError):
    code = "conflict"


class InviteInvalid(AppStoreError):
    code = "invite_invalid"


class DeviceLimit(AppStoreError):
    code = "device_limit"


class LastDevice(AppStoreError):
    code = "last_device"


class ChallengeInvalid(AppStoreError):
    code = "challenge_invalid"


class ChallengeExpired(AppStoreError):
    code = "challenge_expired"


class IdempotencyConflict(AppStoreError):
    code = "idempotency_conflict"


class MomentInFlight(AppStoreError):
    code = "moment_in_flight"


class AccountDeleting(AppStoreError):
    code = "account_deleting"


@dataclass(frozen=True)
class Enrollment:
    user_id: str
    device_id: str
    key_id: str


@dataclass(frozen=True)
class AuthKey:
    key_id: str
    user_id: str
    device_id: str
    public_key: bytes | None
    counter: int
    environment: str
    platform: str


@dataclass(frozen=True)
class Job:
    id: str
    moment_id: str
    user_id: str
    note: str | None
    image_path: str | None


@dataclass(frozen=True)
class MomentResult:
    moment_id: str
    created: bool
    status: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_users (
    id          TEXT PRIMARY KEY,
    alias       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    deleting    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_invites (
    id              TEXT PRIMARY KEY,
    code_hash       TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL CHECK(kind IN ('user', 'device')),
    user_id         TEXT REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at      TEXT NOT NULL,
    alias           TEXT,
    redeemed_at     TEXT,
    redeemed_device TEXT,
    revoked_at      TEXT,
    -- NULL means "no ceiling"; 1 is the ordinary single-use invitation.
    max_uses        INTEGER DEFAULT 1,
    use_count       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_attest_keys (
    key_id       TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    public_key   BLOB,
    receipt      BLOB,
    counter      INTEGER NOT NULL DEFAULT 0,
    environment  TEXT NOT NULL,
    platform     TEXT NOT NULL DEFAULT 'ios',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_devices (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    key_id       TEXT NOT NULL UNIQUE REFERENCES app_attest_keys(key_id) ON DELETE CASCADE,
    push_token   TEXT UNIQUE,
    environment  TEXT NOT NULL,
    -- Denormalised from app_attest_keys: push delivery scans app_devices alone
    -- and must not pay for a join on its hot path.  Written once at enrolment.
    platform     TEXT NOT NULL DEFAULT 'ios',
    timezone     TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    device_name  TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_challenges (
    id          TEXT PRIMARY KEY,
    value       BLOB NOT NULL,
    purpose     TEXT NOT NULL CHECK(purpose IN ('enrollment', 'request')),
    key_id      TEXT,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_preferences (
    user_id             TEXT PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
    daily_frequency     INTEGER NOT NULL DEFAULT 3 CHECK(daily_frequency IN (0, 2, 3, 4)),
    quiet_start         TEXT NOT NULL DEFAULT '22:30',
    quiet_end           TEXT NOT NULL DEFAULT '08:30',
    consecutive_missed  INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_moments (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    source           TEXT NOT NULL CHECK(source IN ('inbound', 'proactive')),
    note             TEXT,
    image_path       TEXT,
    preview_path     TEXT,
    request_digest   TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    status           TEXT NOT NULL,
    scene            TEXT,
    move             TEXT,
    memory_entry_id  INTEGER,
    push_preview     TEXT,
    acked            INTEGER NOT NULL DEFAULT 0,
    failure_retryable INTEGER CHECK(failure_retryable IN (0, 1)),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS app_jobs (
    id           TEXT PRIMARY KEY,
    moment_id    TEXT NOT NULL UNIQUE REFERENCES app_moments(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'queued',
    worker_id    TEXT,
    lease_until  TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_events (
    moment_id   TEXT NOT NULL REFERENCES app_moments(id) ON DELETE CASCADE,
    sequence    INTEGER NOT NULL,
    event       TEXT NOT NULL CHECK(event IN ('accepted','bubble','quiet','done','error')),
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY(moment_id, sequence)
);

CREATE TABLE IF NOT EXISTS app_proactive_slots (
    user_id     TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    local_day   TEXT NOT NULL,
    slot_at     TEXT NOT NULL,
    delivered_at TEXT,
    PRIMARY KEY(user_id, slot_at)
);

CREATE TABLE IF NOT EXISTS app_push_deliveries (
    moment_id       TEXT NOT NULL REFERENCES app_moments(id) ON DELETE CASCADE,
    device_id       TEXT NOT NULL REFERENCES app_devices(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','sent','dead')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_status     INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY(moment_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_app_challenge_expiry
    ON app_challenges(expires_at, used_at);
CREATE INDEX IF NOT EXISTS idx_app_job_claim
    ON app_jobs(status, lease_until, created_at);
CREATE INDEX IF NOT EXISTS idx_app_event_ttl
    ON app_events(created_at);
CREATE INDEX IF NOT EXISTS idx_app_moment_current
    ON app_moments(user_id, source, acked, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_push_due
    ON app_push_deliveries(status, next_attempt_at);
"""


class AppStore:
    """Thread-safe transactional interface to the app tables."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        try:
            self.path.chmod(0o600)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    sidecar.chmod(0o600)
        except OSError:
            pass

    def _migrate(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(app_invites)")
        }
        for name in ("alias", "revoked_at"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE app_invites ADD COLUMN {name} TEXT")
        # Older rows predate reusable invitations.  Backfilling max_uses=1 and
        # deriving use_count from redeemed_at keeps their single-use meaning.
        if "max_uses" not in columns:
            self.conn.execute(
                "ALTER TABLE app_invites ADD COLUMN max_uses INTEGER DEFAULT 1"
            )
            self.conn.execute("UPDATE app_invites SET max_uses=1 WHERE max_uses IS NULL")
        if "use_count" not in columns:
            self.conn.execute(
                "ALTER TABLE app_invites ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0"
            )
            self.conn.execute(
                "UPDATE app_invites SET use_count=1 WHERE redeemed_at IS NOT NULL"
            )
        user_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(app_users)")
        }
        if "deleting" not in user_columns:
            self.conn.execute(
                "ALTER TABLE app_users ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0"
            )
        moment_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(app_moments)")
        }
        if "failure_retryable" not in moment_columns:
            self.conn.execute(
                "ALTER TABLE app_moments ADD COLUMN failure_retryable INTEGER"
            )
        # Every row that predates a second client platform is an iOS row, so the
        # column default backfills them correctly and no data migration is due.
        key_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(app_attest_keys)")
        }
        if "platform" not in key_columns:
            self.conn.execute(
                "ALTER TABLE app_attest_keys ADD COLUMN platform TEXT NOT NULL DEFAULT 'ios'"
            )
        device_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(app_devices)")
        }
        if "platform" not in device_columns:
            self.conn.execute(
                "ALTER TABLE app_devices ADD COLUMN platform TEXT NOT NULL DEFAULT 'ios'"
            )
        if "push_token" not in device_columns and "apns_token" in device_columns:
            self.conn.execute(
                "ALTER TABLE app_devices RENAME COLUMN apns_token TO push_token"
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> AppStore:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except Exception:
                self.conn.rollback()
                raise
            else:
                self.conn.commit()

    # ---- Invitations and devices -------------------------------------------------

    def create_invite(
        self,
        *,
        kind: str = "user",
        user_id: str | None = None,
        ttl: timedelta | None = None,
        alias: str | None = None,
        max_uses: int | None = 1,
        permanent: bool = False,
    ) -> str:
        if kind not in {"user", "device"}:
            raise ValueError("invite kind must be user or device")
        if kind == "device" and not user_id:
            raise ValueError("device invite requires user_id")
        if max_uses is not None and max_uses < 1:
            raise ValueError("invite must allow at least one use")
        if permanent:
            # Storing a concrete far-future date rather than NULL keeps every
            # existing expiry comparison working untouched.
            expires_at = _iso(PERMANENT_EXPIRY)
        else:
            # The lifetime is a store invariant, not a convention each caller has
            # to remember: new users get seven days, add-device codes 30 minutes.
            ttl = ttl or (timedelta(days=7) if kind == "user" else timedelta(minutes=30))
            if ttl <= timedelta(0):
                raise ValueError("invite lifetime must be positive")
            expires_at = _iso(_now() + ttl)
        code = secrets.token_urlsafe(24)
        now = _now()
        with self._tx() as db:
            if user_id and not db.execute(
                "SELECT 1 FROM app_users WHERE id=? AND active=1", (user_id,)
            ).fetchone():
                raise NotFound("user not found")
            # alias is only meaningful to a newly created user.  It is embedded in
            # the invitation id metadata without weakening the random code.
            invite_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO app_invites"
                "(id,code_hash,kind,user_id,expires_at,alias,max_uses,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (invite_id, _hash_secret(code), kind, user_id, expires_at,
                 alias[:80] if alias else None, max_uses, _iso(now)),
            )
        return code

    def list_invites(self, *, include_closed: bool = False) -> list[dict]:
        # "Open" means still redeemable: a reusable code stays listed after its
        # first use, which is exactly when an admin most needs to see it.
        where = "" if include_closed else (
            "WHERE revoked_at IS NULL AND (max_uses IS NULL OR use_count < max_uses)"
        )
        with self._lock:
            return [dict(row) for row in self.conn.execute(
                "SELECT id,kind,user_id,alias,created_at,expires_at,redeemed_at,revoked_at,"
                "max_uses,use_count "
                f"FROM app_invites {where} ORDER BY created_at DESC"
            ).fetchall()]

    def revoke_invite(self, invite_id: str) -> bool:
        with self._tx() as db:
            # A reusable code has to stay revocable after it has been redeemed --
            # otherwise a permanent invitation could never be switched off.
            cur = db.execute(
                "UPDATE app_invites SET revoked_at=? WHERE id=? AND revoked_at IS NULL "
                "AND (max_uses IS NULL OR use_count < max_uses)",
                (_iso(), invite_id),
            )
            return cur.rowcount == 1

    def redeem_invite(
        self,
        *,
        code: str,
        key_id: str,
        public_key: bytes | None,
        receipt: bytes | None,
        counter: int,
        environment: str,
        platform: str = "ios",
        device_name: str | None = None,
        max_devices: int = 3,
    ) -> Enrollment:
        now = _now()
        with self._tx() as db:
            row = db.execute(
                "SELECT * FROM app_invites WHERE code_hash=?", (_hash_secret(code),)
            ).fetchone()
            # A code is spent when it hits its ceiling, not merely when it has
            # been used once -- max_uses NULL means it never runs out.
            exhausted = (
                row is not None
                and row["max_uses"] is not None
                and row["use_count"] >= row["max_uses"]
            )
            if (not row or exhausted or row["revoked_at"]
                    or row["expires_at"] <= _iso(now)):
                raise InviteInvalid("invite is invalid, expired, or already used")
            if db.execute(
                "SELECT 1 FROM app_attest_keys WHERE key_id=?", (key_id,)
            ).fetchone():
                raise Conflict("key is already enrolled")

            if row["kind"] == "user":
                user_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO app_users(id,alias,created_at) VALUES(?,?,?)",
                    (user_id, row["alias"], _iso(now)),
                )
                db.execute(
                    "INSERT INTO app_preferences(user_id,updated_at) VALUES(?,?)",
                    (user_id, _iso(now)),
                )
            else:
                user_id = row["user_id"]
                if not db.execute(
                    "SELECT 1 FROM app_users WHERE id=? AND active=1", (user_id,)
                ).fetchone():
                    raise InviteInvalid("invite owner no longer exists")

            count = db.execute(
                "SELECT COUNT(*) AS n FROM app_devices WHERE user_id=? AND active=1",
                (user_id,),
            ).fetchone()["n"]
            if count >= max_devices:
                raise DeviceLimit(f"at most {max_devices} active devices are allowed")

            device_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO app_attest_keys"
                "(key_id,user_id,public_key,receipt,counter,environment,platform,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (key_id, user_id, public_key, receipt, counter, environment, platform,
                 _iso(now)),
            )
            db.execute(
                "INSERT INTO app_devices"
                "(id,user_id,key_id,environment,platform,device_name,last_seen_at,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (device_id, user_id, key_id, environment, platform, device_name,
                 _iso(now), _iso(now)),
            )
            # redeemed_at/redeemed_device record the most recent redemption; the
            # counter is what actually governs whether the code still works.
            # A reusable "user" code keeps user_id NULL so it stays unbound and
            # mints a fresh identity on every redemption.
            bind_user = user_id if row["max_uses"] == 1 else row["user_id"]
            db.execute(
                "UPDATE app_invites SET user_id=?,redeemed_at=?,redeemed_device=?,"
                "use_count=use_count+1 WHERE id=?",
                (bind_user, _iso(now), device_id, row["id"]),
            )
        return Enrollment(user_id, device_id, key_id)

    def auth_key(self, key_id: str) -> AuthKey:
        with self._lock:
            row = self.conn.execute(
                "SELECT k.*,d.id AS device_id FROM app_attest_keys k "
                "JOIN app_devices d ON d.key_id=k.key_id "
                "JOIN app_users u ON u.id=k.user_id "
                "WHERE k.key_id=? AND k.active=1 AND d.active=1 AND u.active=1",
                (key_id,),
            ).fetchone()
        if not row:
            raise NotFound("unknown app key")
        return AuthKey(
            key_id=row["key_id"], user_id=row["user_id"], device_id=row["device_id"],
            public_key=row["public_key"], counter=int(row["counter"]),
            environment=row["environment"], platform=row["platform"],
        )

    def enrollment_for_key(self, key_id: str) -> Enrollment:
        key = self.auth_key(key_id)
        return Enrollment(key.user_id, key.device_id, key.key_id)

    def require_user_ready(self, user_id: str) -> None:
        with self._lock:
            row = self.conn.execute(
                "SELECT active,deleting FROM app_users WHERE id=?", (user_id,)
            ).fetchone()
        if not row or not row["active"]:
            raise NotFound("user not found")
        if row["deleting"]:
            raise AccountDeleting("account deletion is in progress")

    def advance_counter(self, key_id: str, previous: int, new: int) -> None:
        if new <= previous:
            raise Conflict("assertion counter did not advance")
        with self._tx() as db:
            cur = db.execute(
                "UPDATE app_attest_keys SET counter=? WHERE key_id=? AND counter=? AND active=1",
                (new, key_id, previous),
            )
            if cur.rowcount != 1:
                raise Conflict("assertion was replayed")
            db.execute(
                "UPDATE app_devices SET last_seen_at=? WHERE key_id=?", (_iso(), key_id)
            )

    # ---- Challenges --------------------------------------------------------------

    def issue_challenge(
        self, purpose: str, *, key_id: str | None = None, ttl: timedelta = timedelta(minutes=5)
    ) -> tuple[str, bytes, str]:
        if purpose not in {"enrollment", "request"}:
            raise ValueError("unsupported challenge purpose")
        if purpose == "request":
            if not key_id:
                raise ValueError("request challenge requires key_id")
            self.auth_key(key_id)
        challenge_id = str(uuid.uuid4())
        value = secrets.token_bytes(32)
        now = _now()
        expires_at = _iso(now + ttl)
        with self._tx() as db:
            db.execute(
                "INSERT INTO app_challenges"
                "(id,value,purpose,key_id,expires_at,created_at) VALUES(?,?,?,?,?,?)",
                (challenge_id, value, purpose, key_id, expires_at, _iso(now)),
            )
        return challenge_id, value, expires_at

    def consume_challenge(
        self, challenge_id: str, purpose: str, *, key_id: str | None = None
    ) -> bytes:
        with self._tx() as db:
            row = db.execute(
                "SELECT * FROM app_challenges WHERE id=?", (challenge_id,)
            ).fetchone()
            if not row or row["used_at"] or row["purpose"] != purpose:
                raise ChallengeInvalid("challenge is unknown or already used")
            if row["expires_at"] <= _iso():
                db.execute(
                    "UPDATE app_challenges SET used_at=? WHERE id=?", (_iso(), challenge_id)
                )
                raise ChallengeExpired("challenge expired")
            if row["key_id"] != key_id:
                raise ChallengeInvalid("challenge belongs to another key")
            db.execute(
                "UPDATE app_challenges SET used_at=? WHERE id=? AND used_at IS NULL",
                (_iso(), challenge_id),
            )
            return bytes(row["value"])

    # ---- Moments and SSE ----------------------------------------------------------

    def create_moment(
        self,
        *,
        user_id: str,
        note: str | None,
        image_path: str | None,
        idempotency_key: str,
        request_digest: str,
    ) -> MomentResult:
        now = _iso()
        with self._tx() as db:
            user = db.execute(
                "SELECT active,deleting FROM app_users WHERE id=?", (user_id,)
            ).fetchone()
            if not user or not user["active"]:
                raise NotFound("user not found")
            if user["deleting"]:
                raise AccountDeleting("account deletion is in progress")
            existing = db.execute(
                "SELECT id,status,request_digest,failure_retryable FROM app_moments "
                "WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if existing:
                if not secrets.compare_digest(existing["request_digest"], request_digest):
                    raise IdempotencyConflict("idempotency key was used for another request")
                if existing["status"] == "failed":
                    if existing["failure_retryable"] == 0:
                        # Invalid media and other permanent client errors keep
                        # their terminal SSE event.  Re-uploading the same
                        # payload under the same key must not create an endless
                        # worker retry loop.
                        return MomentResult(existing["id"], False, "failed")
                    # A transport retry while work is queued/processing merely
                    # returns the original moment.  A deliberate retry after a
                    # terminal processing error re-arms that same moment so the
                    # client's stable idempotency key remains useful.  The new
                    # upload path is now owned by this moment; the old failure
                    # path was cleared by fail_job().
                    inflight = db.execute(
                        "SELECT 1 FROM app_moments WHERE user_id=? AND id<>? "
                        "AND status IN ('queued','processing')",
                        (user_id, existing["id"]),
                    ).fetchone()
                    if inflight:
                        raise MomentInFlight("another moment is still being processed")
                    db.execute(
                        "UPDATE app_moments SET note=?,image_path=?,status='queued',"
                        "scene=NULL,move=NULL,memory_entry_id=NULL,preview_path=NULL,"
                        "push_preview=NULL,failure_retryable=NULL,updated_at=? WHERE id=?",
                        (note, image_path, now, existing["id"]),
                    )
                    db.execute("DELETE FROM app_events WHERE moment_id=?", (existing["id"],))
                    db.execute(
                        "INSERT INTO app_events(moment_id,sequence,event,data,created_at) "
                        "VALUES(?,1,'accepted',?,?)",
                        (existing["id"], json.dumps(
                            {"moment_id": existing["id"]}, ensure_ascii=False
                        ), now),
                    )
                    job = db.execute(
                        "SELECT id FROM app_jobs WHERE moment_id=?", (existing["id"],)
                    ).fetchone()
                    if job:
                        db.execute(
                            "UPDATE app_jobs SET status='queued',worker_id=NULL,lease_until=NULL,"
                            "last_error=NULL,updated_at=? WHERE id=?",
                            (now, job["id"]),
                        )
                    else:
                        db.execute(
                            "INSERT INTO app_jobs(id,moment_id,created_at,updated_at) "
                            "VALUES(?,?,?,?)",
                            (str(uuid.uuid4()), existing["id"], now, now),
                        )
                    db.execute(
                        "UPDATE app_preferences SET consecutive_missed=0,updated_at=? "
                        "WHERE user_id=?", (now, user_id),
                    )
                    return MomentResult(existing["id"], True, "queued")
                return MomentResult(existing["id"], False, existing["status"])
            inflight = db.execute(
                "SELECT 1 FROM app_moments WHERE user_id=? AND status IN ('queued','processing')",
                (user_id,),
            ).fetchone()
            if inflight:
                raise MomentInFlight("another moment is still being processed")
            moment_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
            db.execute(
                "INSERT INTO app_moments"
                "(id,user_id,source,note,image_path,request_digest,idempotency_key,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (moment_id, user_id, "inbound", note, image_path, request_digest,
                 idempotency_key, "queued", now, now),
            )
            db.execute(
                "INSERT INTO app_jobs(id,moment_id,created_at,updated_at) VALUES(?,?,?,?)",
                (job_id, moment_id, now, now),
            )
            db.execute(
                "INSERT INTO app_events(moment_id,sequence,event,data,created_at) "
                "VALUES(?,1,'accepted',?,?)",
                (moment_id, json.dumps({"moment_id": moment_id}, ensure_ascii=False), now),
            )
            # Any new inbound activity resumes a four-miss proactive hold.  An
            # explicit “别发了” already set frequency=0 and remains disabled.
            db.execute(
                "UPDATE app_preferences SET consecutive_missed=0,updated_at=? "
                "WHERE user_id=?", (now, user_id),
            )
        return MomentResult(moment_id, True, "queued")

    def moment_for_user(self, moment_id: str, user_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM app_moments WHERE id=? AND user_id=?", (moment_id, user_id)
            ).fetchone()
        if not row:
            raise NotFound("moment not found")
        return dict(row)

    def events_after(
        self, moment_id: str, user_id: str, sequence: int = 0, verify: bool = True
    ) -> list[dict]:
        with self._lock:
            # SSE 轮询每 0.25s 调一次；入口已验过 ownership 的调用方传
            # verify=False，省掉每轮重复的 moment_for_user 鉴权查询。
            if verify:
                self.moment_for_user(moment_id, user_id)
            rows = self.conn.execute(
                "SELECT sequence,event,data,created_at FROM app_events "
                "WHERE moment_id=? AND sequence>? ORDER BY sequence",
                (moment_id, sequence),
            ).fetchall()
        return [
            {"sequence": int(r["sequence"]), "event": r["event"],
             "data": json.loads(r["data"]), "created_at": r["created_at"]}
            for r in rows
        ]

    def append_event(self, moment_id: str, event: str, data: dict | None = None) -> int:
        if event not in {"accepted", "bubble", "quiet", "done", "error"}:
            raise ValueError("invalid SSE event")
        with self._tx() as db:
            if not db.execute("SELECT 1 FROM app_moments WHERE id=?", (moment_id,)).fetchone():
                raise NotFound("moment not found")
            sequence = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM app_events WHERE moment_id=?",
                (moment_id,),
            ).fetchone()["n"])
            db.execute(
                "INSERT INTO app_events(moment_id,sequence,event,data,created_at) "
                "VALUES(?,?,?,?,?)",
                (moment_id, sequence, event, json.dumps(data or {}, ensure_ascii=False), _iso()),
            )
        return sequence

    @staticmethod
    def _append_event_tx(
        db: sqlite3.Connection, moment_id: str, event: str, data: dict | None = None
    ) -> int:
        sequence = int(db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM app_events WHERE moment_id=?",
            (moment_id,),
        ).fetchone()["n"])
        db.execute(
            "INSERT INTO app_events(moment_id,sequence,event,data,created_at) VALUES(?,?,?,?,?)",
            (moment_id, sequence, event, json.dumps(data or {}, ensure_ascii=False), _iso()),
        )
        return sequence

    def append_job_event(
        self, job: Job, worker_id: str, event: str, data: dict | None = None
    ) -> bool:
        with self._tx() as db:
            owned = db.execute(
                "SELECT 1 FROM app_jobs WHERE id=? AND status='processing' AND worker_id=?",
                (job.id, worker_id),
            ).fetchone()
            if not owned:
                return False
            self._append_event_tx(db, job.moment_id, event, data)
            return True

    def event_bubbles(self, moment_id: str) -> list[str]:
        """Return transient bubble text for worker retry de-duplication only."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM app_events WHERE moment_id=? AND event='bubble' "
                "ORDER BY sequence", (moment_id,),
            ).fetchall()
        bubbles: list[str] = []
        for row in rows:
            try:
                text = json.loads(row["data"]).get("text")
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(text, str) and text:
                bubbles.append(text)
        return bubbles

    def cleanup_events(self, ttl: timedelta = timedelta(hours=24)) -> int:
        cutoff = _iso(_now() - ttl)
        with self._tx() as db:
            cur = db.execute("DELETE FROM app_events WHERE created_at<?", (cutoff,))
            db.execute(
                "UPDATE app_moments SET note=NULL,scene=NULL,push_preview=NULL "
                "WHERE updated_at<? AND NOT(source='proactive' AND acked=0)",
                (cutoff,),
            )
            db.execute(
                "DELETE FROM app_push_deliveries WHERE status<>'pending' AND updated_at<?",
                (cutoff,),
            )
            # Challenges are independent 5-minute secrets, not 24-hour events.
            db.execute("DELETE FROM app_challenges WHERE expires_at<?", (_iso(),))
            return cur.rowcount

    # ---- Durable worker queue -----------------------------------------------------

    def claim_job(self, worker_id: str, lease: timedelta = timedelta(minutes=3)) -> Job | None:
        now, until = _iso(), _iso(_now() + lease)
        with self._tx() as db:
            while True:
                row = db.execute(
                    "SELECT j.id,j.moment_id,j.attempts,m.user_id,m.note,m.image_path "
                    "FROM app_jobs j JOIN app_moments m ON m.id=j.moment_id "
                    "JOIN app_users u ON u.id=m.user_id "
                    "WHERE (j.status='queued' OR (j.status='processing' AND j.lease_until<?)) "
                    "AND u.active=1 AND u.deleting=0 "
                    "ORDER BY j.created_at LIMIT 1",
                    (now,),
                ).fetchone()
                if not row:
                    return None
                if int(row["attempts"]) >= MAX_JOB_ATTEMPTS:
                    # Each past claim ended in a process-level crash, so no
                    # error event was ever written.  Failing the moment here
                    # gives the client its terminal event and breaks the
                    # crash/reclaim loop.
                    db.execute(
                        "UPDATE app_jobs SET status='failed',lease_until=NULL,"
                        "last_error='attempts_exceeded',updated_at=? WHERE id=?",
                        (now, row["id"]),
                    )
                    db.execute(
                        "UPDATE app_moments SET status='failed',note=NULL,image_path=NULL,"
                        "failure_retryable=0,updated_at=? WHERE id=?",
                        (now, row["moment_id"]),
                    )
                    self._append_event_tx(db, row["moment_id"], "error", {
                        "code": "attempts_exceeded",
                        "message": "Murmur 暂时没有接住，请重新发送一次。",
                        "retryable": False,
                    })
                    continue
                db.execute(
                    "UPDATE app_jobs SET status='processing',worker_id=?,lease_until=?,"
                    "attempts=attempts+1,updated_at=? WHERE id=?",
                    (worker_id, until, now, row["id"]),
                )
                db.execute(
                    "UPDATE app_moments SET status='processing',updated_at=? WHERE id=?",
                    (now, row["moment_id"]),
                )
                return Job(row["id"], row["moment_id"], row["user_id"], row["note"],
                           row["image_path"])

    def renew_job(
        self, job: Job, worker_id: str, lease: timedelta = timedelta(minutes=3)
    ) -> bool:
        with self._tx() as db:
            cur = db.execute(
                "UPDATE app_jobs SET lease_until=?,updated_at=? WHERE id=? "
                "AND status='processing' AND worker_id=?",
                (_iso(_now() + lease), _iso(), job.id, worker_id),
            )
            return cur.rowcount == 1

    def owns_job(self, job: Job, worker_id: str) -> bool:
        with self._lock:
            return self.conn.execute(
                "SELECT 1 FROM app_jobs WHERE id=? AND status='processing' AND worker_id=?",
                (job.id, worker_id),
            ).fetchone() is not None

    def complete_owned_job(
        self,
        job: Job,
        worker_id: str,
        *,
        scene: str,
        move: str,
        memory_entry_id: int,
        preview_path: str | None,
        quiet: bool,
    ) -> bool:
        with self._tx() as db:
            owned = db.execute(
                "SELECT 1 FROM app_jobs j JOIN app_moments m ON m.id=j.moment_id "
                "JOIN app_users u ON u.id=m.user_id WHERE j.id=? "
                "AND j.status='processing' AND j.worker_id=? "
                "AND u.active=1 AND u.deleting=0",
                (job.id, worker_id),
            ).fetchone()
            if not owned:
                return False
            if quiet:
                self._append_event_tx(db, job.moment_id, "quiet", {})
            self._append_event_tx(
                db, job.moment_id, "done", {"move": move, "scene": scene}
            )
            db.execute(
                "UPDATE app_moments SET status='complete',note=NULL,scene=?,move=?,"
                "memory_entry_id=?,preview_path=?,image_path=NULL,failure_retryable=NULL,"
                "updated_at=? WHERE id=?",
                (scene, move, memory_entry_id, preview_path, _iso(), job.moment_id),
            )
            db.execute(
                "UPDATE app_jobs SET status='done',lease_until=NULL,updated_at=? WHERE id=?",
                (_iso(), job.id),
            )
            return True

    def finish_job(
        self,
        job: Job,
        *,
        scene: str,
        move: str,
        memory_entry_id: int,
        preview_path: str | None,
    ) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE app_moments SET status='complete',note=NULL,scene=?,move=?,memory_entry_id=?,"
                "preview_path=?,image_path=NULL,failure_retryable=NULL,updated_at=? WHERE id=?",
                (scene, move, memory_entry_id, preview_path, _iso(), job.moment_id),
            )
            db.execute(
                "UPDATE app_jobs SET status='done',lease_until=NULL,updated_at=? WHERE id=?",
                (_iso(), job.id),
            )

    def fail_job(
        self, job: Job, *, code: str, message: str, retryable: bool,
        worker_id: str | None = None,
    ) -> bool:
        safe_message = message[:240]
        with self._tx() as db:
            if worker_id and not db.execute(
                "SELECT 1 FROM app_jobs WHERE id=? AND status='processing' AND worker_id=?",
                (job.id, worker_id),
            ).fetchone():
                return False
            db.execute(
                "UPDATE app_moments SET status='failed',note=NULL,image_path=NULL,"
                "failure_retryable=?,updated_at=? WHERE id=?",
                (1 if retryable else 0, _iso(), job.moment_id),
            )
            db.execute(
                "UPDATE app_jobs SET status='failed',lease_until=NULL,last_error=?,updated_at=? "
                "WHERE id=?",
                (code, _iso(), job.id),
            )
            self._append_event_tx(db, job.moment_id, "error", {
                "code": code, "message": safe_message, "retryable": bool(retryable)
            })
            return True

    # ---- Current proactive moment and preferences --------------------------------

    def current_proactive(self, user_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT id,push_preview,created_at FROM app_moments "
                "WHERE user_id=? AND source='proactive' AND status='complete' AND acked=0 "
                "ORDER BY created_at DESC LIMIT 1", (user_id,),
            ).fetchone()
            if not row:
                return None
            bubbles = [e["data"]["text"] for e in self.events_after(row["id"], user_id)
                       if e["event"] == "bubble" and e["data"].get("text")]
            return {"moment_id": row["id"], "bubbles": bubbles,
                    "preview": row["push_preview"], "created_at": row["created_at"]}

    def create_proactive(
        self, user_id: str, bubbles: list[str], *, scene: str = "",
        memory_entry_id: int | None = None, now: datetime | None = None,
    ) -> str:
        if not bubbles:
            raise ValueError("proactive moment needs a bubble")
        now_text, moment_id = _iso(now), str(uuid.uuid4())
        with self._tx() as db:
            user = db.execute(
                "SELECT active,deleting FROM app_users WHERE id=?", (user_id,)
            ).fetchone()
            if not user or not user["active"]:
                raise NotFound("user not found")
            if user["deleting"]:
                raise AccountDeleting("account deletion is in progress")
            if db.execute(
                "SELECT 1 FROM app_moments WHERE user_id=? AND source='proactive' "
                "AND status='complete' AND acked=0", (user_id,),
            ).fetchone():
                raise MomentInFlight("a proactive moment is still pending")
            db.execute(
                "INSERT INTO app_moments"
                "(id,user_id,source,request_digest,idempotency_key,status,scene,move,push_preview,"
                "memory_entry_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'complete',?,'speak',?,?,?,?)",
                (moment_id, user_id, "proactive", "proactive", f"proactive:{moment_id}",
                 scene, bubbles[0][:120], memory_entry_id, now_text, now_text),
            )
            seq = 1
            for bubble in bubbles[:3]:
                db.execute(
                    "INSERT INTO app_events(moment_id,sequence,event,data,created_at) "
                    "VALUES(?,?,'bubble',?,?)",
                    (moment_id, seq, json.dumps({"text": bubble}, ensure_ascii=False), now_text),
                )
                seq += 1
            db.execute(
                "INSERT INTO app_events(moment_id,sequence,event,data,created_at) "
                "VALUES(?,?,'done',?,?)",
                (moment_id, seq, json.dumps({"move": "speak", "scene": scene},
                                            ensure_ascii=False), now_text),
            )
            db.execute(
                "INSERT INTO app_push_deliveries"
                "(moment_id,device_id,next_attempt_at,created_at,updated_at) "
                "SELECT ?,id,?,?,? FROM app_devices WHERE user_id=? AND active=1 "
                "AND push_token IS NOT NULL",
                (moment_id, now_text, now_text, now_text, user_id),
            )
        return moment_id

    def acknowledge(self, moment_id: str, user_id: str) -> dict:
        with self._tx() as db:
            row = db.execute(
                "SELECT * FROM app_moments WHERE id=? AND user_id=?", (moment_id, user_id)
            ).fetchone()
            if not row:
                raise NotFound("moment not found")
            db.execute("UPDATE app_moments SET acked=1,updated_at=? WHERE id=?", (_iso(), moment_id))
            db.execute(
                "UPDATE app_push_deliveries SET status='dead',updated_at=? "
                "WHERE moment_id=? AND status='pending'",
                (_iso(), moment_id),
            )
            if row["source"] == "proactive":
                db.execute(
                    "UPDATE app_preferences SET consecutive_missed=0,updated_at=? WHERE user_id=?",
                    (_iso(), user_id),
                )
            return dict(row)

    def preferences(self, user_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT daily_frequency,quiet_start,quiet_end,consecutive_missed "
                "FROM app_preferences WHERE user_id=?", (user_id,),
            ).fetchone()
        if not row:
            raise NotFound("preferences not found")
        return dict(row)

    def update_preferences(
        self, user_id: str, *, daily_frequency: int, quiet_start: str, quiet_end: str
    ) -> dict:
        if daily_frequency not in {0, 2, 3, 4}:
            raise ValueError("daily_frequency must be 0, 2, 3, or 4")
        for value in (quiet_start, quiet_end):
            try:
                datetime.strptime(value, "%H:%M")
            except ValueError as exc:
                raise ValueError("quiet times must use HH:MM") from exc
        start = datetime.strptime(quiet_start, "%H:%M")
        end = datetime.strptime(quiet_end, "%H:%M")
        if (start.hour, start.minute) <= (end.hour, end.minute):
            raise ValueError("quiet interval must cross midnight")
        with self._tx() as db:
            cur = db.execute(
                "UPDATE app_preferences SET daily_frequency=?,quiet_start=?,quiet_end=?,"
                "consecutive_missed=CASE WHEN ?>0 THEN 0 ELSE consecutive_missed END,"
                "updated_at=? WHERE user_id=?",
                (daily_frequency, quiet_start, quiet_end, daily_frequency, _iso(), user_id),
            )
            if cur.rowcount != 1:
                raise NotFound("preferences not found")
            # Frequency/window changes invalidate today's undelivered plan.  The
            # scheduler rebuilds it from the new preferences on its next cycle.
            db.execute("DELETE FROM app_proactive_slots WHERE user_id=?", (user_id,))
        return self.preferences(user_id)

    def update_device(
        self,
        key_id: str,
        *,
        push_token: str | None,
        environment: str,
        timezone: str,
        device_name: str | None,
    ) -> dict:
        with self._tx() as db:
            if push_token:
                # Push tokens may rotate or move during a restore.  Transfer the
                # token atomically instead of surfacing the UNIQUE constraint.
                db.execute(
                    "UPDATE app_devices SET push_token=NULL WHERE push_token=? AND key_id<>?",
                    (push_token, key_id),
                )
            cur = db.execute(
                "UPDATE app_devices SET push_token=?,environment=?,timezone=?,device_name=?,"
                "last_seen_at=? WHERE key_id=? AND active=1",
                (push_token, environment, timezone, device_name, _iso(), key_id),
            )
            if cur.rowcount != 1:
                raise NotFound("device not found")
            if push_token:
                device = db.execute(
                    "SELECT id,user_id FROM app_devices WHERE key_id=?", (key_id,)
                ).fetchone()
                # A token registered after a proactive moment was created should
                # still receive that one current message; no historical list is
                # exposed or backfilled.
                now = _iso()
                db.execute(
                    "INSERT INTO app_push_deliveries"
                    "(moment_id,device_id,next_attempt_at,created_at,updated_at) "
                    "SELECT m.id,?,?,?,? FROM app_moments m WHERE m.user_id=? "
                    "AND m.source='proactive' AND m.status='complete' AND m.acked=0 "
                    "ORDER BY m.created_at DESC LIMIT 1 "
                    "ON CONFLICT(moment_id,device_id) DO UPDATE SET "
                    "status='pending',attempts=0,next_attempt_at=excluded.next_attempt_at,"
                    "last_status=NULL,updated_at=excluded.updated_at "
                    "WHERE app_push_deliveries.status='dead'",
                    (device["id"], now, now, now, device["user_id"]),
                )
        with self._lock:
            row = self.conn.execute(
                "SELECT id,user_id,environment,platform,timezone,device_name,"
                "push_token IS NOT NULL AS push_enabled "
                "FROM app_devices WHERE key_id=?", (key_id,),
            ).fetchone()
        result = dict(row)
        result["push_enabled"] = bool(result["push_enabled"])
        return result

    def devices(self, user_id: str) -> list[dict]:
        with self._lock:
            devices = [dict(row) for row in self.conn.execute(
                "SELECT id,key_id,environment,platform,timezone,device_name,"
                "push_token IS NOT NULL AS push_enabled,last_seen_at,created_at "
                "FROM app_devices WHERE user_id=? AND active=1 ORDER BY created_at",
                (user_id,),
            ).fetchall()]
        for device in devices:
            device["push_enabled"] = bool(device["push_enabled"])
        return devices

    def revoke_device(self, user_id: str, device_id: str) -> bool:
        with self._tx() as db:
            row = db.execute(
                "SELECT key_id FROM app_devices WHERE id=? AND user_id=? AND active=1",
                (device_id, user_id),
            ).fetchone()
            if not row:
                return False
            count = int(db.execute(
                "SELECT COUNT(*) AS n FROM app_devices WHERE user_id=? AND active=1",
                (user_id,),
            ).fetchone()["n"])
            if count <= 1:
                raise LastDevice("delete the account instead of its last device")
            db.execute(
                "UPDATE app_devices SET active=0,push_token=NULL WHERE id=?", (device_id,)
            )
            db.execute(
                "UPDATE app_push_deliveries SET status='dead',updated_at=? "
                "WHERE device_id=? AND status='pending'", (_iso(), device_id),
            )
            db.execute(
                "UPDATE app_attest_keys SET active=0 WHERE key_id=?", (row["key_id"],)
            )
            db.execute(
                "UPDATE app_challenges SET used_at=COALESCE(used_at,?) WHERE key_id=?",
                (_iso(), row["key_id"]),
            )
            return True

    def invalidate_push_token(self, token: str) -> None:
        with self._tx() as db:
            devices = db.execute(
                "SELECT id FROM app_devices WHERE push_token=?", (token,)
            ).fetchall()
            db.execute("UPDATE app_devices SET push_token=NULL WHERE push_token=?", (token,))
            for device in devices:
                db.execute(
                    "UPDATE app_push_deliveries SET status='dead',updated_at=? "
                    "WHERE device_id=? AND status='pending'",
                    (_iso(), device["id"]),
                )

    def due_push_deliveries(
        self, now: datetime | None = None, *, limit: int = 100
    ) -> list[dict]:
        now = now or _now()
        with self._lock:
            return [dict(row) for row in self.conn.execute(
                "SELECT q.moment_id,q.device_id,q.attempts,d.push_token,d.platform,"
                "m.push_preview,m.user_id FROM app_push_deliveries q "
                "JOIN app_devices d ON d.id=q.device_id "
                "JOIN app_moments m ON m.id=q.moment_id "
                "JOIN app_users u ON u.id=m.user_id "
                "WHERE q.status='pending' AND q.next_attempt_at<=? "
                "AND d.active=1 AND d.push_token IS NOT NULL "
                "AND m.source='proactive' AND m.status='complete' AND m.acked=0 "
                "AND u.active=1 AND u.deleting=0 "
                "ORDER BY q.next_attempt_at LIMIT ?",
                (_iso(now), max(1, min(limit, 500))),
            ).fetchall()]

    def pending_push_delivery(
        self, moment_id: str, device_id: str, now: datetime | None = None
    ) -> dict | None:
        now = now or _now()
        with self._lock:
            row = self.conn.execute(
                "SELECT q.moment_id,q.device_id,q.attempts,d.push_token,d.platform,"
                "m.push_preview,m.user_id FROM app_push_deliveries q "
                "JOIN app_devices d ON d.id=q.device_id "
                "JOIN app_moments m ON m.id=q.moment_id "
                "JOIN app_users u ON u.id=m.user_id "
                "WHERE q.moment_id=? AND q.device_id=? AND q.status='pending' "
                "AND q.next_attempt_at<=? AND d.active=1 AND d.push_token IS NOT NULL "
                "AND m.acked=0 AND u.active=1 AND u.deleting=0",
                (moment_id, device_id, _iso(now)),
            ).fetchone()
        return dict(row) if row else None

    def mark_push_sent(self, moment_id: str, device_id: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE app_push_deliveries SET status='sent',attempts=attempts+1,"
                "last_status=200,updated_at=? WHERE moment_id=? AND device_id=? "
                "AND status='pending'",
                (_iso(), moment_id, device_id),
            )

    def mark_push_dead(
        self, moment_id: str, device_id: str, *, status: int | None = None
    ) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE app_push_deliveries SET status='dead',attempts=attempts+1,"
                "last_status=?,updated_at=? WHERE moment_id=? AND device_id=? "
                "AND status='pending'",
                (status, _iso(), moment_id, device_id),
            )

    def retry_push(
        self,
        moment_id: str,
        device_id: str,
        *,
        status: int | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or _now()
        with self._tx() as db:
            row = db.execute(
                "SELECT attempts FROM app_push_deliveries WHERE moment_id=? "
                "AND device_id=? AND status='pending'",
                (moment_id, device_id),
            ).fetchone()
            if not row:
                return
            attempts = int(row["attempts"]) + 1
            if attempts >= MAX_PUSH_ATTEMPTS:
                db.execute(
                    "UPDATE app_push_deliveries SET status='dead',attempts=?,"
                    "last_status=?,updated_at=? WHERE moment_id=? AND device_id=? "
                    "AND status='pending'",
                    (attempts, status, _iso(now), moment_id, device_id),
                )
                return
            # ±20% jitter keeps a fleet of devices from retrying in lockstep.
            delay = min(3600, 30 * (2 ** min(attempts - 1, 7)) * random.uniform(0.8, 1.2))
            db.execute(
                "UPDATE app_push_deliveries SET attempts=?,next_attempt_at=?,"
                "last_status=?,updated_at=? WHERE moment_id=? AND device_id=? "
                "AND status='pending'",
                (attempts, _iso(now + timedelta(seconds=delay)), status, _iso(now),
                 moment_id, device_id),
            )

    def push_devices(self, user_id: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM app_devices WHERE user_id=? AND active=1 "
                "AND push_token IS NOT NULL",
                (user_id,),
            ).fetchall()]

    def active_users(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT u.id,u.alias,p.daily_frequency,p.quiet_start,p.quiet_end,"
                "p.consecutive_missed,"
                "COALESCE((SELECT d.timezone FROM app_devices d WHERE d.user_id=u.id "
                "AND d.active=1 ORDER BY d.last_seen_at DESC,d.created_at DESC "
                "LIMIT 1),'Asia/Shanghai') AS timezone "
                "FROM app_users u JOIN app_preferences p ON p.user_id=u.id "
                "WHERE u.active=1 AND u.deleting=0"
            ).fetchall()]

    def proactive_users(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT u.id,u.alias,p.daily_frequency,p.quiet_start,p.quiet_end,"
                "p.consecutive_missed,"
                "COALESCE((SELECT d.timezone FROM app_devices d WHERE d.user_id=u.id "
                "AND d.active=1 ORDER BY d.last_seen_at DESC,d.created_at DESC "
                "LIMIT 1),'Asia/Shanghai') AS timezone "
                "FROM app_users u JOIN app_preferences p ON p.user_id=u.id "
                "WHERE u.active=1 AND u.deleting=0 "
                "AND p.daily_frequency>0 "
                "AND EXISTS(SELECT 1 FROM app_moments m WHERE m.user_id=u.id "
                "AND m.source='inbound' AND m.status='complete') "
                "AND EXISTS(SELECT 1 FROM app_devices pd WHERE pd.user_id=u.id "
                "AND pd.active=1 AND pd.push_token IS NOT NULL)"
            ).fetchall()]

    def expire_stale_proactive(
        self, max_age: timedelta = timedelta(hours=24), *, now: datetime | None = None
    ) -> int:
        cutoff = _iso((now or _now()) - max_age)
        with self._tx() as db:
            rows = db.execute(
                "SELECT id,user_id FROM app_moments WHERE source='proactive' AND acked=0 "
                "AND created_at<?", (cutoff,),
            ).fetchall()
            for row in rows:
                db.execute("UPDATE app_moments SET acked=1,updated_at=? WHERE id=?",
                           (_iso(), row["id"]))
                db.execute(
                    "UPDATE app_push_deliveries SET status='dead',updated_at=? "
                    "WHERE moment_id=? AND status='pending'", (_iso(), row["id"]),
                )
                db.execute(
                    "UPDATE app_preferences SET consecutive_missed=MIN(consecutive_missed+1,4),"
                    "updated_at=? WHERE user_id=?", (_iso(), row["user_id"]),
                )
            return len(rows)

    def replace_slots(self, user_id: str, local_day: date, slots: list[datetime]) -> None:
        with self._tx() as db:
            db.execute(
                "DELETE FROM app_proactive_slots WHERE user_id=? AND local_day=?",
                (user_id, local_day.isoformat()),
            )
            db.executemany(
                "INSERT INTO app_proactive_slots(user_id,local_day,slot_at) VALUES(?,?,?)",
                [(user_id, local_day.isoformat(), _iso(slot)) for slot in slots],
            )

    def has_slots(self, user_id: str, local_day: date) -> bool:
        with self._lock:
            return self.conn.execute(
                "SELECT 1 FROM app_proactive_slots WHERE user_id=? AND local_day=? LIMIT 1",
                (user_id, local_day.isoformat()),
            ).fetchone() is not None

    def due_slots(self, now: datetime) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT s.user_id,s.slot_at FROM app_proactive_slots s "
                "JOIN app_preferences p ON p.user_id=s.user_id "
                "JOIN app_users u ON u.id=s.user_id "
                "WHERE s.delivered_at IS NULL AND s.slot_at<=? AND p.daily_frequency>0 "
                "AND p.consecutive_missed<4 AND u.active=1 AND u.deleting=0 "
                "AND EXISTS(SELECT 1 FROM app_moments m WHERE m.user_id=s.user_id "
                "AND m.source='inbound' AND m.status='complete') "
                "AND EXISTS(SELECT 1 FROM app_devices pd WHERE pd.user_id=s.user_id "
                "AND pd.active=1 AND pd.push_token IS NOT NULL) ORDER BY s.slot_at",
                (_iso(now),),
            ).fetchall()]

    def mark_slot_delivered(self, user_id: str, slot_at: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE app_proactive_slots SET delivered_at=? WHERE user_id=? AND slot_at=?",
                (_iso(), user_id, slot_at),
            )

    # ---- Account erasure ---------------------------------------------------------

    def begin_user_erasure(self, user_id: str) -> dict:
        """Tombstone the user and cancel work, but retain keys for a retry.

        The caller removes Memory/files under the same cross-process user lock,
        then calls :meth:`complete_user_erasure`.  If external cleanup fails the
        assertion key remains valid solely so DELETE can be retried.
        """
        with self._tx() as db:
            if not db.execute(
                "SELECT 1 FROM app_users WHERE id=? AND active=1", (user_id,)
            ).fetchone():
                raise NotFound("user not found")
            db.execute("UPDATE app_users SET deleting=1 WHERE id=?", (user_id,))
            moments = db.execute(
                "SELECT image_path,preview_path,memory_entry_id FROM app_moments WHERE user_id=?",
                (user_id,),
            ).fetchall()
            # A moment cancelled mid-flight must still end its SSE stream with a
            # terminal event; otherwise the client only finds out via the 120s
            # stream deadline.
            inflight = db.execute(
                "SELECT id FROM app_moments WHERE user_id=? "
                "AND status IN ('queued','processing')",
                (user_id,),
            ).fetchall()
            for moment in inflight:
                self._append_event_tx(db, moment["id"], "error", {
                    "code": "account_deleted",
                    "message": "账号已删除。",
                    "retryable": False,
                })
            db.execute(
                "UPDATE app_jobs SET status='cancelled',lease_until=NULL,updated_at=? "
                "WHERE moment_id IN (SELECT id FROM app_moments WHERE user_id=?) "
                "AND status IN ('queued','processing')",
                (_iso(), user_id),
            )
            db.execute(
                "UPDATE app_moments SET status='cancelled',updated_at=? WHERE user_id=? "
                "AND status IN ('queued','processing')",
                (_iso(), user_id),
            )
        return {
            "paths": [p for row in moments
                      for p in (row["image_path"], row["preview_path"]) if p],
            "memory_entry_ids": [int(row["memory_entry_id"]) for row in moments
                                 if row["memory_entry_id"] is not None],
        }

    def complete_user_erasure(self, user_id: str) -> None:
        with self._tx() as db:
            row = db.execute(
                "SELECT deleting FROM app_users WHERE id=? AND active=1", (user_id,)
            ).fetchone()
            if not row:
                raise NotFound("user not found")
            if not row["deleting"]:
                raise Conflict("account erasure was not started")
            keys = [item["key_id"] for item in db.execute(
                "SELECT key_id FROM app_attest_keys WHERE user_id=?", (user_id,)
            ).fetchall()]
            if keys:
                db.executemany(
                    "DELETE FROM app_challenges WHERE key_id=?", [(key,) for key in keys]
                )
            db.execute("DELETE FROM app_users WHERE id=?", (user_id,))

    def erase_user(self, user_id: str) -> dict:
        """Administrative immediate erase; API callers use the two-phase methods."""
        artefacts = self.begin_user_erasure(user_id)
        self.complete_user_erasure(user_id)
        return artefacts
