"""Durable, source-grounded daily reviews. No photos, push or raw-history API.

Model work runs outside transactions and outside the chat worker. A lease plus
an account revision fences concurrent generators, correction and deletion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .app_store import AppStore, NotFound, _iso
from .memory import thread_key
from .objectivity import RULES

log = logging.getLogger("murmur.daily_review")
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_review_preferences (
 user_id TEXT PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
 enabled INTEGER NOT NULL DEFAULT 1, local_time TEXT NOT NULL DEFAULT '22:30',
 enabled_at TEXT NOT NULL, first_day TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_review_sources (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
 day TEXT NOT NULL, timezone TEXT NOT NULL, occurred_at TEXT NOT NULL,
 moment_id TEXT NOT NULL, entry_id INTEGER, channel TEXT NOT NULL,
 user_text TEXT NOT NULL, observation TEXT NOT NULL, shot_at TEXT,
 UNIQUE(user_id,moment_id)
);
CREATE INDEX IF NOT EXISTS idx_review_sources_day ON app_review_sources(user_id,day,occurred_at);
CREATE TABLE IF NOT EXISTS app_daily_reviews (
 user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE, day TEXT NOT NULL,
 timezone TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 summary TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '',
 source_ids TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 0,
 fingerprint TEXT NOT NULL DEFAULT '', lease TEXT, lease_until TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, retry_at TEXT, error TEXT, updated_at TEXT,
 PRIMARY KEY(user_id,day)
);
CREATE TABLE IF NOT EXISTS app_review_memories (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
 day TEXT NOT NULL, text TEXT NOT NULL, evidence TEXT NOT NULL,
 source_ids TEXT NOT NULL, edited INTEGER NOT NULL DEFAULT 0,
 forgotten INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_memories_user ON app_review_memories(user_id,forgotten,day);
CREATE TABLE IF NOT EXISTS app_review_exclusions (
 user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL, PRIMARY KEY(user_id,source_id)
);
CREATE TABLE IF NOT EXISTS app_review_answers (
 moment_id TEXT PRIMARY KEY REFERENCES app_moments(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
 day TEXT NOT NULL, question TEXT NOT NULL
);
"""


def initialize(conn):
    conn.executescript(SCHEMA)


def day_string(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    date.fromisoformat(value)
    return value


class DailyReviews:
    def __init__(self, store: AppStore):
        self.store = store

    def timezone(self, user):
        with self.store._lock:
            row = self.store.conn.execute(
                "SELECT timezone FROM app_devices WHERE user_id=? AND active=1 "
                "ORDER BY last_seen_at DESC,id DESC LIMIT 1",
                (user,),
            ).fetchone()
        try:
            return ZoneInfo(row[0] if row else "Asia/Shanghai")
        except (ValueError, KeyError):
            return ZoneInfo("Asia/Shanghai")

    def preferences(self, user):
        with self.store._lock:
            row = self.store.conn.execute(
                "SELECT * FROM app_review_preferences WHERE user_id=?", (user,)
            ).fetchone()
        return dict(row) | {"enabled": bool(row["enabled"])} if row else None

    def configure(self, user, *, enabled=True, local_time="22:30", now=None):
        if (
            type(enabled) is not bool
            or not isinstance(local_time, str)
            or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local_time)
        ):
            raise ValueError("自动整理开关或当地时间无效")
        now = now or datetime.now(UTC)
        tz = self.timezone(user)
        with self.store._tx() as db:
            self.store.require_user_ready(user)
            db.execute(
                "INSERT INTO app_review_preferences(user_id,enabled,local_time,enabled_at,first_day) "
                "VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,"
                "local_time=excluded.local_time,revision=revision+1",
                (
                    user,
                    int(enabled),
                    local_time,
                    _iso(datetime.combine(now.astimezone(tz).date(), time.min, tz)),
                    now.astimezone(tz).date().isoformat(),
                ),
            )
            db.execute(
                "UPDATE app_daily_reviews SET status='queued',lease=NULL,lease_until=NULL WHERE user_id=? AND status='generating'",
                (user,),
            )
        return self.preferences(user)

    def history_view(self, user, memory):
        """Read-only projection: raw transcripts remain intact on disk.

        After a correction/forget, old generated replies have no granular
        provenance, so omit those replies rather than recalling stale claims.
        """
        with self.store._lock:
            excluded = {
                r[0]
                for r in self.store.conn.execute(
                    "SELECT source_id FROM app_review_exclusions WHERE user_id=?", (user,)
                )
            }
            edits = self.store.conn.execute(
                "SELECT text,source_ids FROM app_review_memories WHERE user_id=? AND edited=1 AND forgotten=0",
                (user,),
            ).fetchall()
        source_ids = excluded | {sid for item in edits for sid in json.loads(item["source_ids"])}
        sources = []
        with self.store._lock:
            # Bind JSON as one parameter, avoiding both a full history load and
            # SQLite's variable limit for long-lived accounts.
            if source_ids:
                sources = self.store.conn.execute(
                    "SELECT id,entry_id FROM app_review_sources WHERE user_id=? AND id IN (SELECT value FROM json_each(?))",
                    (user, json.dumps(sorted(source_ids))),
                ).fetchall()
        overrides = {}
        for item in edits:
            for sid in json.loads(item["source_ids"]):
                overrides.setdefault(sid, []).append(item["text"])
        removed = {s["entry_id"] for s in sources if s["id"] in excluded}
        corrected = {
            s["entry_id"]: "用户已纠正：" + "；".join(overrides[s["id"]])
            for s in sources
            if s["id"] in overrides
        }
        return ReviewHistory(memory, removed, corrected, bool(excluded or edits))

    def import_completed(self, memory_path):
        """Repairable outbox scan; only committed, successful moments since activation.

        Each source gets an immutable exchange day/timezone at first import.
        Old photo dates remain metadata and never select the review date.
        """
        with self.store._lock:
            rows = self.store.conn.execute(
                "SELECT m.*,p.first_day,p.enabled_at FROM app_moments m "
                "JOIN app_review_preferences p ON p.user_id=m.user_id "
                "JOIN app_users u ON u.id=m.user_id "
                "WHERE u.active=1 AND u.deleting=0 AND m.source='inbound' "
                "AND m.status='complete' AND m.memory_entry_id IS NOT NULL "
                "AND m.created_at>=p.enabled_at "
                "AND NOT EXISTS(SELECT 1 FROM app_review_sources s WHERE s.moment_id=m.id) "
                "ORDER BY m.created_at LIMIT 500"
            ).fetchall()
        if not rows:
            return
        connection = sqlite3.connect(memory_path)
        connection.row_factory = sqlite3.Row
        try:
            for m in rows:
                user = m["user_id"]
                entry = connection.execute(
                    "SELECT * FROM entries WHERE id=? AND chat_id=? AND delivery_state='committed'",
                    (m["memory_entry_id"], thread_key("app", "direct", user)[0]),
                ).fetchone()
                if not entry:
                    continue
                tz = ZoneInfo(m["review_timezone"]) if m["review_timezone"] else self.timezone(user)
                local_day = (
                    datetime.fromisoformat(m["created_at"]).astimezone(tz).date().isoformat()
                )
                if local_day < m["first_day"]:
                    continue
                # 'scene' is an observation, not a user statement or objective truth.
                note = entry["note"] or ""
                observation = entry["scene"] or "" if entry["has_photo"] else ""
                with self.store._tx() as db:
                    self.store.require_user_ready(user)
                    db.execute(
                        "INSERT OR IGNORE INTO app_review_sources "
                        "(id,user_id,day,timezone,occurred_at,moment_id,entry_id,channel,user_text,observation,shot_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            m["id"],
                            user,
                            local_day,
                            str(tz),
                            m["created_at"],
                            m["id"],
                            entry["id"],
                            "photo_room"
                            if m["intent"] == "photo_reading" or m["context_moment_ids"]
                            else "home",
                            note,
                            observation,
                            entry["shot_at"],
                        ),
                    )
        finally:
            connection.close()

    def sources(self, user, day, *, corrected=True):
        with self.store._lock:
            rows = self.store.conn.execute(
                "SELECT * FROM app_review_sources s WHERE user_id=? AND day=? AND (user_text<>'' OR observation<>'') "
                "AND NOT EXISTS(SELECT 1 FROM app_review_exclusions x WHERE x.user_id=s.user_id AND x.source_id=s.id) "
                "ORDER BY occurred_at,id",
                (user, day),
            ).fetchall()
        result = [dict(r) for r in rows]
        with self.store._lock:
            edits = self.store.conn.execute(
                "SELECT text,source_ids FROM app_review_memories WHERE user_id=? AND edited=1 AND forgotten=0",
                (user,),
            ).fetchall()
        if not corrected:
            return result
        for source in result:
            replacements = [r["text"] for r in edits if source["id"] in json.loads(r["source_ids"])]
            if replacements:
                source["user_text"] = "用户已纠正：" + "；".join(replacements)
                source["observation"] = ""
        return result

    def enqueue_due(self, now=None):
        now = now or datetime.now(UTC)
        with self.store._lock:
            candidates = self.store.conn.execute(
                "SELECT DISTINCT s.user_id,s.day,p.local_time "
                "FROM app_review_sources s JOIN app_review_preferences p ON p.user_id=s.user_id "
                "JOIN app_users u ON u.id=s.user_id WHERE p.enabled=1 AND u.active=1 AND u.deleting=0"
            ).fetchall()
        for row in candidates:
            tz = self.timezone(row["user_id"])
            due = datetime.combine(
                date.fromisoformat(row["day"]), time.fromisoformat(row["local_time"]), tz
            )
            if now >= due:
                self.request(row["user_id"], row["day"], now=now)

    def request(self, user, day, *, now=None, force=False):
        day_string(day)
        now = now or datetime.now(UTC)
        pref = self.preferences(user)
        if not pref or day < pref["first_day"]:
            raise NotFound("review date unavailable")
        sources = self.sources(user, day)
        if not sources:
            return None
        fingerprint = hashlib.sha256(
            json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self.store._tx() as db:
            self.store.require_user_ready(user)
            old = db.execute(
                "SELECT * FROM app_daily_reviews WHERE user_id=? AND day=?", (user, day)
            ).fetchone()
            if old:
                if old["lease_until"] and old["lease_until"] > _iso(now):
                    return dict(old)
                if not force and old["fingerprint"] == fingerprint and old["status"] == "ready":
                    return dict(old)
                if not force and old["retry_at"] and old["retry_at"] > _iso(now):
                    return dict(old)
                db.execute(
                    "UPDATE app_daily_reviews SET status='queued',timezone=?,lease=NULL,lease_until=NULL "
                    "WHERE user_id=? AND day=?",
                    (str(self.timezone(user)), user, day),
                )
            else:
                db.execute(
                    "INSERT INTO app_daily_reviews(user_id,day,timezone) VALUES(?,?,?)",
                    (user, day, str(self.timezone(user))),
                )
        return self.detail(user, day)

    def claim(self, now=None):
        now = now or datetime.now(UTC)
        with self.store._tx() as db:
            row = db.execute(
                "SELECT r.*,p.revision FROM app_daily_reviews r "
                "JOIN app_users u ON u.id=r.user_id JOIN app_review_preferences p ON p.user_id=r.user_id "
                "WHERE u.active=1 AND u.deleting=0 AND p.enabled=1 "
                "AND ((r.status='queued') OR (r.status='generating' AND r.lease_until<=?)) "
                "AND (r.retry_at IS NULL OR r.retry_at<=?) ORDER BY r.day LIMIT 1",
                (_iso(now), _iso(now)),
            ).fetchone()
            if not row:
                return None
            task = dict(row)
            task["lease"] = str(uuid.uuid4())
            db.execute(
                "UPDATE app_daily_reviews SET status='generating',lease=?,lease_until=?,attempts=attempts+1 "
                "WHERE user_id=? AND day=?",
                (task["lease"], _iso(now + timedelta(minutes=10)), task["user_id"], task["day"]),
            )
        task["sources"] = self.sources(task["user_id"], task["day"])
        task["fingerprint"] = hashlib.sha256(
            json.dumps(task["sources"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return task

    def publish(self, task, output, now=None):
        output = validate(output, task["sources"])
        now = now or datetime.now(UTC)
        user = task["user_id"]
        day = task["day"]
        with self.store._tx() as db:
            self.store.require_user_ready(user)
            pref = db.execute(
                "SELECT revision,enabled FROM app_review_preferences WHERE user_id=?", (user,)
            ).fetchone()
            row = db.execute(
                "SELECT * FROM app_daily_reviews WHERE user_id=? AND day=?", (user, day)
            ).fetchone()
            if (
                not row
                or row["lease"] != task["lease"]
                or not pref
                or pref["revision"] != task["revision"]
                or not pref["enabled"]
            ):
                return False
            # Stable identity by evidence sources plus text; edited records survive regeneration.
            existing = db.execute(
                "SELECT * FROM app_review_memories WHERE user_id=? AND day=?", (user, day)
            ).fetchall()
            protected = {
                sid
                for e in existing
                if e["edited"] or e["forgotten"]
                for sid in json.loads(e["source_ids"])
            }
            db.execute(
                "DELETE FROM app_review_memories WHERE user_id=? AND day=? AND edited=0 AND forgotten=0",
                (user, day),
            )
            for item in output["memories"]:
                if protected.intersection(item["source_ids"]):
                    continue
                ident = hashlib.sha256(
                    (user + day + json.dumps(item, sort_keys=True, ensure_ascii=False)).encode()
                ).hexdigest()
                db.execute(
                    "INSERT OR IGNORE INTO app_review_memories(id,user_id,day,text,evidence,source_ids,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        ident,
                        user,
                        day,
                        item["text"],
                        item["evidence"],
                        json.dumps(item["source_ids"]),
                        _iso(now),
                    ),
                )
            db.execute(
                "UPDATE app_daily_reviews SET summary=?,question=CASE WHEN question='' THEN ? ELSE question END,"
                "source_ids=?,version=version+1,fingerprint=?,status='ready',lease=NULL,lease_until=NULL,"
                "retry_at=NULL,error=NULL,updated_at=? WHERE user_id=? AND day=?",
                (
                    output["summary"],
                    output["question"],
                    json.dumps(output["source_ids"]),
                    task["fingerprint"],
                    _iso(now),
                    user,
                    day,
                ),
            )
        return True

    def fail(self, task):
        with self.store._tx() as db:
            db.execute(
                "UPDATE app_daily_reviews SET status='failed',error='generation_failed',lease=NULL,"
                "lease_until=NULL,retry_at=? WHERE user_id=? AND day=? AND lease=?",
                (
                    _iso(
                        datetime.now(UTC)
                        + timedelta(seconds=min(3600, 30 * 2 ** min(task["attempts"], 7)))
                    ),
                    task["user_id"],
                    task["day"],
                    task["lease"],
                ),
            )

    def dates(self, user, before=None, limit=90):
        if before:
            day_string(before)
        with self.store._lock:
            rows = self.store.conn.execute(
                "SELECT day,status,version,updated_at FROM app_daily_reviews "
                "WHERE user_id=? AND (? IS NULL OR day<?) ORDER BY day DESC LIMIT ?",
                (user, before, before, min(90, max(1, limit))),
            ).fetchall()
        return [dict(r) for r in rows]

    def detail(self, user, day):
        day_string(day)
        with self.store._lock:
            row = self.store.conn.execute(
                "SELECT * FROM app_daily_reviews WHERE user_id=? AND day=?", (user, day)
            ).fetchone()
            if not row:
                raise NotFound("review not found")
            items = self.store.conn.execute(
                "SELECT id,text,evidence,source_ids,edited,updated_at FROM app_review_memories "
                "WHERE user_id=? AND day=? AND forgotten=0 ORDER BY id",
                (user, day),
            ).fetchall()
        result = {
            k: row[k]
            for k in (
                "day",
                "timezone",
                "status",
                "summary",
                "question",
                "version",
                "updated_at",
                "error",
            )
        }
        result["memories"] = [
            dict(r) | {"source_ids": json.loads(r["source_ids"]), "edited": bool(r["edited"])}
            for r in items
        ]
        # Only evidence associated with this review is exposed, not a chat transcript endpoint.
        ids = set(json.loads(row["source_ids"])) | {
            s for r in result["memories"] for s in r["source_ids"]
        }
        result["sources"] = [
            {
                k: s[k]
                for k in ("id", "occurred_at", "channel", "user_text", "observation", "shot_at")
            }
            for s in self.sources(user, day, corrected=False)
            if s["id"] in ids
        ]
        return result

    def edit(self, user, ident, text=None, *, forget=False):
        if not forget and (not isinstance(text, str) or not 1 <= len(text.strip()) <= 500):
            raise ValueError("记忆应为1–500字")
        with self.store._tx() as db:
            self.store.require_user_ready(user)
            row = db.execute(
                "SELECT * FROM app_review_memories WHERE user_id=? AND id=?",
                (user, ident),
            ).fetchone()
            if not row:
                raise NotFound("memory not found")
            if row["forgotten"]:
                if forget:
                    return {"id": ident, "forgotten": True, "text": None}
                raise NotFound("memory not found")
            ids = json.loads(row["source_ids"])
            db.execute(
                "UPDATE app_review_preferences SET revision=revision+1 WHERE user_id=?", (user,)
            )
            if forget:
                for sid in ids:
                    db.execute(
                        "INSERT OR IGNORE INTO app_review_exclusions VALUES(?,?)", (user, sid)
                    )
                # Any other item grounded in the same source is derived from withdrawn evidence.
                for item in db.execute(
                    "SELECT id,source_ids FROM app_review_memories WHERE user_id=?", (user,)
                ).fetchall():
                    if set(ids).intersection(json.loads(item["source_ids"])):
                        db.execute(
                            "UPDATE app_review_memories SET forgotten=1,text='',updated_at=? WHERE id=?",
                            (_iso(), item["id"]),
                        )
            else:
                db.execute(
                    "UPDATE app_review_memories SET text=?,edited=1,updated_at=? WHERE id=?",
                    (text.strip(), _iso(), ident),
                )
            # Invalidate every potentially derived summary immediately. A new job reads
            # only permitted evidence; old material is never returned while rebuilding.
            for review in db.execute(
                "SELECT day,source_ids FROM app_daily_reviews WHERE user_id=?", (user,)
            ).fetchall():
                if set(ids).intersection(json.loads(review["source_ids"])):
                    db.execute(
                        "UPDATE app_daily_reviews SET summary='',question=CASE WHEN ? THEN '' ELSE question END,status='queued',fingerprint='',lease=NULL,"
                        "lease_until=NULL,version=version+1,updated_at=? WHERE user_id=? AND day=?",
                        (int(forget), _iso(), user, review["day"]),
                    )
        return {"id": ident, "forgotten": forget, "text": None if forget else text.strip()}

    def recalled(self, user, query, limit=8):
        # Match across the complete archive without loading it into the prompt
        # or limiting recall to the latest days. Chinese uses overlapping pairs.
        terms = set(re.findall(r"[a-z0-9]{2,}", query.lower()))
        for word in re.findall(r"[\u4e00-\u9fff]+", query):
            terms.update(word[i : i + 2] for i in range(len(word) - 1))
        terms = sorted(terms)[:32]
        if not terms:
            return []
        score = "+".join("(instr(lower(text),?)>0)" for _ in terms)
        with self.store._lock:
            rows = self.store.conn.execute(
                "SELECT text,day,evidence,source_ids,edited FROM ("
                f"SELECT *,({score}) AS score FROM app_review_memories WHERE user_id=? AND forgotten=0) "
                "WHERE score>0 ORDER BY score DESC,edited DESC,day DESC,id LIMIT ?",
                (*terms, user, min(8, max(1, limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def context(self, user, query):
        rows = self.recalled(user, query)
        return "\n".join(
            f"- {r['day']} [{r['evidence']}{' / 用户已纠正' if r['edited'] else ''}] {r['text']} (来源:{r['source_ids']})"
            for r in rows
        )


def validate(obj, sources):
    if not isinstance(obj, dict):
        raise ValueError("invalid review object")
    allowed = {s["id"]: s for s in sources}
    for key in ("summary", "question"):
        if (
            not isinstance(obj.get(key), str)
            or not obj[key].strip()
            or len(obj[key]) > (1200 if key == "summary" else 180)
        ):
            raise ValueError("invalid review text")
    if obj["question"].count("？") + obj["question"].count("?") != 1:
        raise ValueError("exactly one question required")

    def refs(value):
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(s, str) or s not in allowed for s in value)
        ):
            raise ValueError("invalid source reference")

    refs(obj.get("source_ids"))
    if not isinstance(obj.get("memories"), list) or len(obj["memories"]) > 20:
        raise ValueError("invalid memories")
    for item in obj["memories"]:
        if not isinstance(item, dict) or item.get("evidence") not in (
            "user_stated",
            "image_observed",
            "uncertain",
        ):
            raise ValueError("invalid evidence")
        if not isinstance(item.get("text"), str) or not 1 <= len(item["text"]) <= 500:
            raise ValueError("invalid memory")
        refs(item.get("source_ids"))
        field = "user_text" if item["evidence"] == "user_stated" else "observation"
        if item["evidence"] != "uncertain" and any(
            not allowed[s][field] for s in item["source_ids"]
        ):
            raise ValueError("unsupported evidence")
    return obj


class ReviewGenerator:
    def __init__(self, cfg):
        self.cfg = cfg

    def __call__(self, task):
        from .dossier import _loads_tolerant
        from .engine import _client

        system = (
            RULES
            + """\n整理一天的交流：summary 默认150–300字，材料少就更短，不强行升华。
question 只提出一个值得继续思考的问题。已有问题时原样保留。
memories 只记有依据的重要事实，不凑条数；用户感受不等于外部事实。
每条标明 evidence: user_stated / image_observed / uncertain，source_ids 只能引用给定id。
图片 observation 可能有误，不能将其中的关系或心理推断当作事实。
只返回 JSON {"summary":"…","question":"…？","source_ids":["…"],"memories":[{"text":"…","evidence":"user_stated","source_ids":["…"]}]}。"""
        )
        payload = {
            "day": task["day"],
            "timezone": task["timezone"],
            "existing_question": task["question"],
            "sources": task["sources"],
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        kwargs = {}
        if self.cfg.json_prefix:
            messages.append({"role": "assistant", "content": "{", "prefix": True})
        else:
            kwargs["response_format"] = {"type": "json_object"}
        response = _client(self.cfg).chat.completions.create(
            model=self.cfg.memory_model or self.cfg.model,
            messages=messages,
            max_tokens=4000,
            **kwargs,
        )
        raw = response.choices[0].message.content or ""
        if self.cfg.json_prefix and raw.lstrip().startswith(('"', "“")):
            raw = "{" + raw
        return _loads_tolerant(raw)


class ReviewHistory:
    def __init__(self, memory, removed, corrected, changed):
        self.memory, self.removed, self.corrected, self.changed = (
            memory,
            removed,
            corrected,
            changed,
        )

    def filter(self, entries):
        result = []
        for entry in entries:
            if entry.id in self.removed:
                continue
            if entry.id in self.corrected:
                entry = replace(
                    entry,
                    note=self.corrected[entry.id],
                    scene=None,
                    said=None,
                    reply=None,
                    music_track=None,
                )
            elif self.changed:
                entry = replace(entry, said=None, reply=None)
                if entry.kind == "out":
                    continue
            result.append(entry)
        return result

    def recent(self, chat_id=0, limit=6):
        return self.filter(self.memory.recent(chat_id, limit))

    def entries_by_ids(self, ids):
        return self.filter(self.memory.entries_by_ids(ids))

    def recent_outbound(self, chat_id, limit=8):
        return [] if self.changed else self.memory.recent_outbound(chat_id, limit)

    def spot_visits(self, chat_id, spot, bucket):
        # Location-count aggregates cannot attribute a forgotten source.
        return 0 if self.changed else self.memory.spot_visits(chat_id, spot, bucket)

    def __getattr__(self, name):
        return getattr(self.memory, name)


class ReviewWorker:
    def __init__(self, store, memory_path, generator):
        self.reviews = DailyReviews(store)
        self.memory_path = memory_path
        self.generator = generator
        self.stop_event = threading.Event()

    def cycle(self, now=None):
        self.reviews.import_completed(self.memory_path)
        self.reviews.enqueue_due(now)
        task = self.reviews.claim(now)
        if not task:
            return False
        try:
            if not task["sources"]:
                # All evidence was forgotten; never regenerate it from raw entries.
                with self.reviews.store._tx() as db:
                    db.execute(
                        "DELETE FROM app_daily_reviews WHERE user_id=? AND day=? AND lease=?",
                        (task["user_id"], task["day"], task["lease"]),
                    )
            else:
                self.reviews.publish(task, self.generator(task), now)
        except Exception as error:
            log.warning("review_task category=%s", type(error).__name__)
            self.reviews.fail(task)
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                busy = self.cycle()
            except Exception as error:
                log.warning("review_scheduler category=%s", type(error).__name__)
                busy = False
            self.stop_event.wait(0.5 if busy else 30)
