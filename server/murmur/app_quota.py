"""Account-wide inbound AI budget, reserved inside the moment transaction."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAILY_LIMIT = 50
TIMEZONE = ZoneInfo("Asia/Shanghai")


class QuotaExceeded(RuntimeError):
    code = "daily_quota_exhausted"


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_ai_usage (
            moment_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            charged INTEGER NOT NULL DEFAULT 1 CHECK(charged IN (0,1))
        );
        CREATE INDEX IF NOT EXISTS app_ai_usage_day ON app_ai_usage(user_id,day,charged);
        CREATE TRIGGER IF NOT EXISTS app_ai_usage_failed
        AFTER UPDATE OF status ON app_moments WHEN NEW.status='failed'
        BEGIN
            UPDATE app_ai_usage SET charged=0 WHERE moment_id=NEW.id;
        END;
    """)


def status(db, user_id, now=None):
    local = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
    used = db.execute(
        "SELECT COUNT(*) FROM app_ai_usage WHERE user_id=? AND day=? AND charged=1",
        (user_id, local.date().isoformat()),
    ).fetchone()[0]
    reset = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), TIMEZONE)
    return {
        "limit": DAILY_LIMIT,
        "used": used,
        "remaining": max(0, DAILY_LIMIT - used),
        "resets_at": reset.isoformat(),
        "timezone": "Asia/Shanghai",
    }


def reserve(db, user_id, moment_id, now=None):
    # Called with BEGIN IMMEDIATE held: different connections/devices serialize.
    prior = db.execute(
        "SELECT charged FROM app_ai_usage WHERE moment_id=?", (moment_id,)
    ).fetchone()
    if prior and prior[0]:
        return
    local = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
    if status(db, user_id, local)["remaining"] == 0:
        raise QuotaExceeded("今日 50 次请求已用完，北京时间零点恢复。")
    db.execute(
        "INSERT INTO app_ai_usage(moment_id,user_id,day,charged) VALUES(?,?,?,1) "
        "ON CONFLICT(moment_id) DO UPDATE SET day=excluded.day,charged=1",
        (moment_id, user_id, local.date().isoformat()),
    )
