"""SQLite 记忆层。

存两种东西：最近发生过什么，以及"同一个地方的同一个时段"你来过多少次。
后者才是让它显得懂你的东西——不是从图里看出来的，是数出来的。

分层记忆的思路参考 smixs/iva (https://github.com/smixs/iva)，那边落成
Obsidian markdown，这里为了按维度聚合用 SQLite。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .affect import AffectState, apply_message, decay
from .open_loops import expires_at, iso_utc, normalize_title, resolution_status

OPEN_LOOP_CLAIM_LEASE = timedelta(minutes=15)


def _claim_cutoff(now_iso: str) -> str:
    return (
        datetime.fromisoformat(now_iso) - OPEN_LOOP_CLAIM_LEASE
    ).isoformat(timespec="seconds")


def thread_key(platform: str, conversation: str, sender: str) -> tuple[int, str]:
    """一个人 = 一条上下文。

    按 (平台, 会话, 发送人) 三者隔离，而不是只按会话——
    只按会话的话，群里所有人共用一条记忆，A 的心事会串到 B 的回复里。

    返回 (整数键, 可读串)。整数键给数据库索引用，可读串存下来方便排查。
    """
    label = f"{platform}:{conversation}:{sender}"
    key = int(hashlib.sha1(label.encode()).hexdigest()[:12], 16)
    return key, label

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY,
    chat_id     INTEGER NOT NULL DEFAULT 0,  -- thread_key() 算出的整数键
    thread      TEXT,   -- 可读的 平台:会话:发送人，排查用
    logged_at   TEXT NOT NULL,
    shot_at     TEXT,
    bucket      TEXT,
    weekday     TEXT,
    spot        TEXT,   -- 匿名地点指纹，不是地名
    scene       TEXT,   -- 模型看到的画面
    move        TEXT,   -- speak | brief | quiet
    said        TEXT,   -- 它说了什么
    note        TEXT,   -- 用户随图附的话
    reply       TEXT,   -- 用户之后的回应
    kind        TEXT,   -- 'in' 他发起的 | 'out' 它主动发起的
    intent      TEXT,   -- 主动消息的意图，防止 10 条全是"在干嘛"
    has_photo   INTEGER NOT NULL DEFAULT 0,
    material_id TEXT,   -- 这条主动消息使用的具体素材，防止跨来源串线
    music_track TEXT,   -- TrackV1 JSON；不含 token、流地址或播放状态
    music_track_role TEXT CHECK(music_track_role IS NULL OR music_track_role IN ('in','out')),
    delivery_state TEXT NOT NULL DEFAULT 'committed'
                  CHECK (delivery_state IN ('pending', 'committed'))
);
-- 谁说过"别发了"。不在 .env 里改，因为这是运行时的事，
-- 而且对方的意愿不该需要我改配置文件才生效。
-- 谁已经收到过自我介绍。每个新 id 只打一次招呼。
CREATE TABLE IF NOT EXISTS greeted (
    chat_id  INTEGER PRIMARY KEY,
    thread   TEXT,
    at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optouts (
    chat_id  INTEGER PRIMARY KEY,
    thread   TEXT,
    since    TEXT NOT NULL
);

-- 名册：认识的人。新人第一次说话就自动入册，不用改 .env 也不用重启。
--
-- 为什么不直接靠 .env 的白名单：白名单是"谁被允许"，是静态的；
-- 名册是"认识了谁"，是长出来的。主动消息要按人发，得有个活的列表——
-- 靠改配置文件的话，新人只能等下次重启才收得到消息。
--
-- user_id 存的是**发主动消息时要用的那个 id**（钉钉 staffId / 微信 openId /
-- Telegram chat id），chat_id 是 thread_key 算出来的键，两者不是一回事。
CREATE TABLE IF NOT EXISTS roster (
    platform TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    chat_id  INTEGER NOT NULL,
    thread   TEXT,
    nick     TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS open_loops (
    id                INTEGER PRIMARY KEY,
    chat_id           INTEGER NOT NULL,
    title             TEXT NOT NULL,
    kind              TEXT NOT NULL,
    due_at            TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'resolved', 'cancelled', 'expired')),
    source_entry_id   INTEGER,
    followup_count    INTEGER NOT NULL DEFAULT 0,
    last_followup_at  TEXT,
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);

CREATE TABLE IF NOT EXISTS proactive_materials (
    chat_id         INTEGER NOT NULL,
    material_id     TEXT NOT NULL,
    category        TEXT NOT NULL,
    source_ref      TEXT NOT NULL,
    cooldown_until  TEXT,
    last_used_at    TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (chat_id, material_id)
);

CREATE TABLE IF NOT EXISTS affect_states (
    chat_id       INTEGER PRIMARY KEY,
    valence       REAL NOT NULL,
    arousal       REAL NOT NULL,
    updated_at    TEXT NOT NULL,
    last_entry_id INTEGER
);
"""

# 索引单独放，必须在 _migrate() 之后建。
# v0.1 的 entries 表没有 chat_id 列，而这些索引都建在 chat_id 上——
# 跟建表语句写在一起的话，打开一个真正的老库会直接
# "no such column: chat_id"，整个进程起不来。
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chat_spot_bucket ON entries(chat_id, spot, bucket);
CREATE INDEX IF NOT EXISTS idx_chat_id_desc ON entries(chat_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_roster_platform ON roster(platform);
CREATE UNIQUE INDEX IF NOT EXISTS idx_open_loops_identity
    ON open_loops(chat_id, title, COALESCE(due_at, ''));
CREATE INDEX IF NOT EXISTS idx_open_loops_pending
    ON open_loops(chat_id, status, due_at, created_at);
CREATE INDEX IF NOT EXISTS idx_proactive_materials_cooldown
    ON proactive_materials(chat_id, cooldown_until);
"""


@dataclass
class Entry:
    id: int
    logged_at: str
    shot_at: str | None
    bucket: str | None
    weekday: str | None
    scene: str | None
    move: str | None
    said: str | None
    note: str | None
    reply: str | None
    kind: str | None = None
    intent: str | None = None
    material_id: str | None = None
    music_track: str | None = None
    music_track_role: str | None = None


@dataclass(frozen=True)
class OpenLoop:
    id: int
    chat_id: int
    title: str
    kind: str
    due_at: str | None
    status: str
    source_entry_id: int | None
    followup_count: int
    last_followup_at: str | None
    created_at: str
    resolved_at: str | None


class Memory:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        # 连接是跨线程共享的（wechat 线程池、qq to_thread、bot 回调都踩同一个
        # Memory）。写方法必须整段持锁，否则一个线程的 execute(DML) 和 commit()
        # 之间会插进另一个线程的写，撞上 "cannot start a transaction within
        # a transaction"。
        self._lock = threading.RLock()
        # Telegram 和钉钉是两个独立进程，写的是同一个库。默认的 rollback journal
        # 会让写操作互相独占，撞上就是 "database is locked"。
        # WAL 允许一写多读，busy_timeout 让偶发冲突自己等而不是直接抛。
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.executescript(INDEXES)   # 补完列才能建索引，顺序不能换
        self.conn.commit()
        # 聊天原文库，跟 AppStore 一样只许本人读写。
        try:
            self.path.chmod(0o600)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    sidecar.chmod(0o600)
        except OSError:
            pass

    def _migrate(self) -> None:
        """v0.1 的库里是 place 列、没有 chat_id。补上缺的列，别让旧库炸掉。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(entries)")}
        for name, decl in (("chat_id", "INTEGER NOT NULL DEFAULT 0"), ("spot", "TEXT"),
                           ("reply", "TEXT"), ("thread", "TEXT"),
                           ("kind", "TEXT"), ("intent", "TEXT"),
                           ("has_photo", "INTEGER NOT NULL DEFAULT 0"),
                           ("material_id", "TEXT"),
                           ("music_track", "TEXT"),
                           ("music_track_role", "TEXT"),
                           ("delivery_state", "TEXT NOT NULL DEFAULT 'committed'")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE entries ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Memory:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def record(
        self,
        *,
        chat_id: int = 0,
        thread: str | None = None,
        shot_at: datetime | None,
        bucket: str | None,
        weekday: str | None,
        spot: str | None,
        scene: str | None,
        move: str | None,
        said: str | None,
        note: str | None,
        kind: str = "in",
        intent: str | None = None,
        has_photo: bool = False,
        material_id: str | None = None,
        music_track: str | None = None,
        music_track_role: str | None = None,
        delivery_state: str = "committed",
    ) -> int:
        if delivery_state not in {"pending", "committed"}:
            raise ValueError("delivery_state must be pending or committed")
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO entries
                   (chat_id, thread, logged_at, shot_at, bucket, weekday, spot,
                    scene, move, said, note, kind, intent, has_photo, material_id,
                    music_track, music_track_role, delivery_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chat_id,
                    thread,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    shot_at.isoformat(timespec="seconds") if shot_at else None,
                    bucket,
                    weekday,
                    spot,
                    scene,
                    move,
                    said,
                    note,
                    kind,
                    intent,
                    1 if has_photo else 0,
                    material_id,
                    music_track,
                    music_track_role,
                    delivery_state,
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def add_reply(self, entry_id: int, text: str) -> None:
        """用户在它说完之后回的话。判断"这句说得对不对"最直接的信号。"""
        with self._lock:
            self.conn.execute(
                "UPDATE entries SET reply = ? WHERE id = ?", (text, entry_id)
            )
            self.conn.commit()

    def discard_outbound_entry(self, entry_id: int, chat_id: int) -> bool:
        """Compensate a proactive entry that never became user-visible."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM entries WHERE id=? AND chat_id=? AND kind='out' "
                "AND delivery_state='pending'",
                (entry_id, chat_id),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def outbound_entry_delivery(
        self, entry_id: int, chat_id: int
    ) -> tuple[str | None, str] | None:
        """Return material/state for one outbound entry, distinguishing absence.

        A generic proactive message legitimately has no material id.  Returning
        only that nullable column made a missing row indistinguishable from a
        valid generic message, so the durable App outbox could be acknowledged
        even though there was no Memory row to reconcile.
        """
        row = self.conn.execute(
            "SELECT material_id, delivery_state FROM entries "
            "WHERE id = ? AND chat_id = ? AND kind = 'out'",
            (entry_id, chat_id),
        ).fetchone()
        if row is None:
            return None
        material_id = str(row["material_id"]) if row["material_id"] else None
        return material_id, str(row["delivery_state"])

    def discard_unlinked_outbound_entries(
        self, chat_id: int, keep_entry_ids: set[int]
    ) -> int:
        """Remove pre-App crash orphans while preserving durable outbox links.

        The caller must obtain the per-user App operation lock, then derive
        ``keep_entry_ids`` from AppStore's durable outbox under that lock.  A
        Memory-only cleanup cannot tell an unsent row from a user-visible App
        moment whose cross-database finalization is still pending.
        """
        sql = (
            "DELETE FROM entries WHERE chat_id=? AND kind='out' "
            "AND delivery_state='pending'"
        )
        params: list[object] = [chat_id]
        if keep_entry_ids:
            placeholders = ",".join("?" for _ in keep_entry_ids)
            sql += f" AND id NOT IN ({placeholders})"
            params.extend(sorted(keep_entry_ids))
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.rowcount

    def commit_outbound_entry(self, entry_id: int, chat_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE entries SET delivery_state='committed' "
                "WHERE id=? AND chat_id=? AND kind='out' "
                "AND delivery_state='pending'",
                (entry_id, chat_id),
            )
            exists = cur.rowcount == 1 or self.conn.execute(
                "SELECT 1 FROM entries WHERE id=? AND chat_id=? AND kind='out' "
                "AND delivery_state='committed'",
                (entry_id, chat_id),
            ).fetchone() is not None
            self.conn.commit()
            return exists

    def recent(self, chat_id: int = 0, limit: int = 6) -> list[Entry]:
        rows = self.conn.execute(
            """SELECT e.* FROM entries e WHERE e.chat_id = ?
               AND e.delivery_state='committed'
               ORDER BY e.id DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in reversed(rows)]

    def entries_by_ids(self, entry_ids: list[int]) -> list[Entry]:
        """Return committed Memory entries in the caller's requested order."""
        if not entry_ids:
            return []
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self.conn.execute(
            f"SELECT * FROM entries WHERE id IN ({placeholders}) "
            "AND delivery_state='committed'",
            entry_ids,
        ).fetchall()
        by_id = {int(row["id"]): _row_to_entry(row) for row in rows}
        return [by_id[entry_id] for entry_id in entry_ids if entry_id in by_id]

    def spot_visits(self, chat_id: int, spot: str | None, bucket: str | None) -> int:
        """同一个匿名地点、同一个时段，以前来过几次。
        没有定位就返回 0——光凭时段说"你又是这个点"太廉价，容易翻车。"""
        if not spot or not bucket:
            return 0
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE chat_id=? AND spot=? AND bucket=? "
            "AND delivery_state='committed'",
            (chat_id, spot, bucket),
        ).fetchone()
        return int(row["n"])


    def recent_outbound(self, chat_id: int, limit: int = 8) -> list[Entry]:
        """最近它主动说过什么。用来避免 10 条/天全是同一句。"""
        rows = self.conn.execute(
            """SELECT e.* FROM entries e
               WHERE e.chat_id = ? AND e.kind = 'out'
                 AND e.delivery_state='committed'
               ORDER BY e.id DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in reversed(rows)]

    def last_inbound_at(self, chat_id: int) -> str | None:
        """他最后一次说话是什么时候。正在聊的时候别硬插一条主动消息。"""
        row = self.conn.execute(
            "SELECT logged_at FROM entries WHERE chat_id = ? AND kind != 'out' "
            "AND delivery_state='committed' "
            "ORDER BY id DESC LIMIT 1", (chat_id,),
        ).fetchone()
        return row["logged_at"] if row else None

    def unanswered_outbound(self, chat_id: int) -> int:
        """连续几条主动消息没被回。真人被连着无视也会收敛。"""
        rows = self.conn.execute(
            """SELECT e.kind,e.reply FROM entries e WHERE e.chat_id = ?
               AND e.delivery_state='committed'
               ORDER BY e.id DESC LIMIT 12""",
            (chat_id,),
        ).fetchall()
        n = 0
        for r in rows:
            if r["kind"] != "out":
                break
            if r["reply"]:
                break
            n += 1
        return n


    def has_greeted(self, chat_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM greeted WHERE chat_id = ?", (chat_id,)
        ).fetchone() is not None

    def mark_greeted(self, chat_id: int, thread: str | None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO greeted(chat_id, thread, at) VALUES (?,?,?)",
                (chat_id, thread,
                 datetime.now(UTC).isoformat(timespec="seconds")),
            )
            self.conn.commit()

    def set_optout(self, chat_id: int, thread: str | None, on: bool = True) -> None:
        with self._lock:
            if on:
                self.conn.execute(
                    "INSERT OR REPLACE INTO optouts(chat_id, thread, since) VALUES (?,?,?)",
                    (chat_id, thread,
                     datetime.now(UTC).isoformat(timespec="seconds")),
                )
            else:
                self.conn.execute("DELETE FROM optouts WHERE chat_id = ?", (chat_id,))
            self.conn.commit()

    def is_opted_out(self, chat_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM optouts WHERE chat_id = ?", (chat_id,)
        ).fetchone() is not None

    # ---- 开环记忆 ----

    def upsert_open_loop(
        self,
        chat_id: int,
        title: str,
        kind: str,
        due_at: datetime | str | None = None,
        source_entry_id: int | None = None,
        now: datetime | str | None = None,
    ) -> OpenLoop:
        """按人、规范化标题和截止时间幂等写入一个待办开环。"""
        normalized_title = normalize_title(title)
        normalized_due = iso_utc(due_at, name="due_at") if due_at is not None else None
        created_at = iso_utc(now or datetime.now(UTC), name="now")
        with self._lock:
            self.conn.execute(
                """INSERT INTO open_loops
                   (chat_id, title, kind, due_at, status, source_entry_id, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)
                   ON CONFLICT DO UPDATE SET
                       kind = excluded.kind,
                       source_entry_id = COALESCE(
                           open_loops.source_entry_id, excluded.source_entry_id
                       )""",
                (chat_id, normalized_title, kind, normalized_due, source_entry_id, created_at),
            )
            row = self.conn.execute(
                """SELECT * FROM open_loops
                   WHERE chat_id = ? AND title = ? AND COALESCE(due_at, '') = COALESCE(?, '')""",
                (chat_id, normalized_title, normalized_due),
            ).fetchone()
            self.conn.commit()
        return _row_to_open_loop(row)

    def pending_open_loops(self, chat_id: int, limit: int = 4) -> list[OpenLoop]:
        rows = self.conn.execute(
            """SELECT * FROM open_loops
               WHERE chat_id = ? AND status = 'pending'
               ORDER BY due_at IS NULL, due_at, created_at, id
               LIMIT ?""",
            (chat_id, max(0, min(limit, 4))),
        ).fetchall()
        return [_row_to_open_loop(row) for row in rows]

    def due_open_loops(
        self, chat_id: int, now: datetime | str, limit: int = 4
    ) -> list[OpenLoop]:
        """Return due loops, including reservations left stale by a crash."""
        now_iso = iso_utc(now, name="now")
        stale_before = _claim_cutoff(now_iso)
        rows = self.conn.execute(
            """SELECT * FROM open_loops
               WHERE chat_id = ? AND status = 'pending'
                 AND followup_count = 0 AND due_at IS NOT NULL AND due_at <= ?
                 AND (last_followup_at IS NULL OR last_followup_at <= ?)
               ORDER BY due_at, created_at, id
               LIMIT ?""",
            (chat_id, now_iso, stale_before, max(0, min(limit, 4))),
        ).fetchall()
        return [_row_to_open_loop(row) for row in rows]

    def has_active_open_loop_claim(
        self, chat_id: int, now: datetime | str
    ) -> bool:
        """Whether a due loop is inside another worker's reservation lease."""
        now_iso = iso_utc(now, name="now")
        stale_before = _claim_cutoff(now_iso)
        row = self.conn.execute(
            """SELECT 1 FROM open_loops
               WHERE chat_id = ? AND status = 'pending' AND followup_count = 0
                 AND due_at IS NOT NULL AND due_at <= ?
                 AND last_followup_at IS NOT NULL AND last_followup_at > ?
               LIMIT 1""",
            (chat_id, now_iso, stale_before),
        ).fetchone()
        return row is not None

    def claim_open_loop_followup(
        self, loop_id: int, now: datetime | str
    ) -> bool:
        """Atomically reserve the follow-up; a crash lease expires after 15m."""
        now_iso = iso_utc(now, name="now")
        stale_before = _claim_cutoff(now_iso)
        with self._lock:
            cur = self.conn.execute(
                """UPDATE open_loops
                   SET last_followup_at = ?
                   WHERE id = ? AND status = 'pending' AND followup_count = 0
                     AND due_at IS NOT NULL AND due_at <= ?
                     AND (last_followup_at IS NULL OR last_followup_at <= ?)""",
                (now_iso, loop_id, now_iso, stale_before),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def commit_open_loop_followup(
        self, loop_id: int, claimed_at: datetime | str | None = None
    ) -> bool:
        """Turn a reservation into the final, user-visible follow-up count."""
        params: list[object] = [loop_id]
        exact = ""
        if claimed_at is not None:
            exact = " AND last_followup_at = ?"
            params.append(iso_utc(claimed_at, name="claimed_at"))
        with self._lock:
            cur = self.conn.execute(
                """UPDATE open_loops SET followup_count = 1
                   WHERE id = ? AND status = 'pending' AND followup_count = 0
                     AND last_followup_at IS NOT NULL""" + exact,
                params,
            )
            self.conn.commit()
            return cur.rowcount == 1

    def mark_open_loop_followed_up(
        self, loop_id: int, now: datetime | str
    ) -> bool:
        """原子占用一次追问机会；未到期、已关闭或已追问都返回 False。"""
        now_iso = iso_utc(now, name="now")
        stale_before = _claim_cutoff(now_iso)
        with self._lock:
            cur = self.conn.execute(
                """UPDATE open_loops
                   SET followup_count = 1, last_followup_at = ?
                   WHERE id = ? AND status = 'pending' AND followup_count = 0
                     AND due_at IS NOT NULL AND due_at <= ?
                     AND (last_followup_at IS NULL OR last_followup_at <= ?)""",
                (now_iso, loop_id, now_iso, stale_before),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def release_open_loop_followup(
        self, loop_id: int, claimed_at: datetime | str
    ) -> bool:
        """Release our exact claim when no proactive message was persisted.

        The timestamp comparison is a compare-and-swap guard: a stale failure
        handler cannot undo a later worker's successful follow-up.
        """
        claimed_iso = iso_utc(claimed_at, name="claimed_at")
        with self._lock:
            cur = self.conn.execute(
                """UPDATE open_loops
                   SET last_followup_at = NULL
                   WHERE id = ? AND status = 'pending' AND followup_count = 0
                     AND last_followup_at = ?""",
                (loop_id, claimed_iso),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def resolve_open_loops_from_text(
        self, chat_id: int, text: str, now: datetime | str
    ) -> list[OpenLoop]:
        """从明确的完成/取消表述关闭同一个人的匹配开环。"""
        resolved_at = iso_utc(now, name="now")
        with self._lock:
            candidates = self.conn.execute(
                """SELECT * FROM open_loops
                   WHERE chat_id = ? AND status = 'pending'
                   ORDER BY created_at, id""",
                (chat_id,),
            ).fetchall()
            closed_ids: list[int] = []
            for row in candidates:
                if status := resolution_status(str(row["title"]), text):
                    self.conn.execute(
                        """UPDATE open_loops SET status = ?, resolved_at = ?
                           WHERE id = ? AND status = 'pending'""",
                        (status, resolved_at, row["id"]),
                    )
                    closed_ids.append(int(row["id"]))
            self.conn.commit()
            if not closed_ids:
                return []
            placeholders = ",".join("?" for _ in closed_ids)
            rows = self.conn.execute(
                f"SELECT * FROM open_loops WHERE id IN ({placeholders}) ORDER BY id",
                closed_ids,
            ).fetchall()
        return [_row_to_open_loop(row) for row in rows]

    def expire_open_loops(
        self, chat_id: int, now: datetime | str
    ) -> list[OpenLoop]:
        """关闭超过类型 backstop 的 pending 开环并返回本次关闭项。"""
        now_dt = datetime.fromisoformat(iso_utc(now, name="now"))
        resolved_at = now_dt.isoformat(timespec="seconds")
        with self._lock:
            candidates = self.conn.execute(
                """SELECT * FROM open_loops
                   WHERE chat_id = ? AND status = 'pending'
                   ORDER BY created_at, id""",
                (chat_id,),
            ).fetchall()
            expired_ids = [
                int(row["id"])
                for row in candidates
                if expires_at(row["kind"], row["due_at"], row["created_at"]) <= now_dt
            ]
            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                self.conn.execute(
                    f"""UPDATE open_loops SET status = 'expired', resolved_at = ?
                        WHERE id IN ({placeholders}) AND status = 'pending'""",
                    [resolved_at, *expired_ids],
                )
            self.conn.commit()
            if not expired_ids:
                return []
            rows = self.conn.execute(
                f"SELECT * FROM open_loops WHERE id IN ({placeholders}) ORDER BY id",
                expired_ids,
            ).fetchall()
        return [_row_to_open_loop(row) for row in rows]

    # ---- 主动消息素材 ----

    def upsert_proactive_material(
        self,
        chat_id: int,
        material_id: str,
        category: str,
        source_ref: str,
        now: datetime | str | None = None,
    ) -> None:
        created_at = iso_utc(now or datetime.now(UTC), name="now")
        with self._lock:
            self.conn.execute(
                """INSERT INTO proactive_materials
                   (chat_id, material_id, category, source_ref, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, material_id) DO UPDATE SET
                       category = excluded.category,
                       source_ref = excluded.source_ref""",
                (chat_id, material_id, category, source_ref, created_at),
            )
            self.conn.commit()

    def material_cooldowns(
        self, chat_id: int, ids: list[str] | None = None
    ) -> dict[str, datetime]:
        if ids is not None and not ids:
            return {}
        sql = (
            "SELECT material_id, cooldown_until FROM proactive_materials "
            "WHERE chat_id = ? AND cooldown_until IS NOT NULL"
        )
        params: list[object] = [chat_id]
        if ids is not None:
            placeholders = ",".join("?" for _ in ids)
            sql += f" AND material_id IN ({placeholders})"
            params.extend(ids)
        rows = self.conn.execute(sql, params).fetchall()
        return {
            str(row["material_id"]): datetime.fromisoformat(row["cooldown_until"])
            for row in rows
        }

    def mark_proactive_material_used(
        self,
        chat_id: int,
        material_id: str,
        now: datetime | str,
        cooldown_days: int = 14,
    ) -> bool:
        if cooldown_days < 0:
            raise ValueError("cooldown_days 不能小于 0")
        used_at = datetime.fromisoformat(iso_utc(now, name="now"))
        cooldown_until = (used_at + timedelta(days=cooldown_days)).isoformat(
            timespec="seconds"
        )
        with self._lock:
            cur = self.conn.execute(
                """UPDATE proactive_materials
                   SET last_used_at = ?, cooldown_until = ?
                   WHERE chat_id = ? AND material_id = ?""",
                (used_at.isoformat(timespec="seconds"), cooldown_until,
                 chat_id, material_id),
            )
            self.conn.commit()
            return cur.rowcount == 1

    # ---- 关系内情绪 ----

    def affect_state(self, chat_id: int, now: datetime | str) -> AffectState:
        current = datetime.fromisoformat(iso_utc(now, name="now"))
        row = self.conn.execute(
            "SELECT valence, arousal, updated_at FROM affect_states WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row is None:
            return AffectState(0.0, 0.0, current)
        stored = AffectState(
            float(row["valence"]),
            float(row["arousal"]),
            datetime.fromisoformat(row["updated_at"]),
        )
        return decay(stored, current)

    def save_affect_state(self, chat_id: int, state: AffectState) -> None:
        updated_at = iso_utc(state.updated_at, name="state.updated_at")
        with self._lock:
            self.conn.execute(
                """INSERT INTO affect_states(chat_id, valence, arousal, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       valence = excluded.valence,
                       arousal = excluded.arousal,
                       updated_at = excluded.updated_at""",
                (chat_id, state.valence, state.arousal, updated_at),
            )
            self.conn.commit()

    def apply_affect_message(
        self,
        chat_id: int,
        entry_id: int,
        text: str | None,
        now: datetime | str,
    ) -> AffectState:
        """幂等施加一条消息；worker 重放同一 entry 不会重复改变情绪。"""
        current = datetime.fromisoformat(iso_utc(now, name="now"))
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM affect_states WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            if row is None:
                stored = AffectState(0.0, 0.0, current)
                last_entry_id = -1
            else:
                stored = AffectState(
                    float(row["valence"]),
                    float(row["arousal"]),
                    datetime.fromisoformat(row["updated_at"]),
                )
                last_entry_id = (
                    int(row["last_entry_id"])
                    if row["last_entry_id"] is not None else -1
                )
            if entry_id <= last_entry_id:
                return decay(stored, current)

            updated = apply_message(stored, text, current)
            self.conn.execute(
                """INSERT INTO affect_states
                   (chat_id, valence, arousal, updated_at, last_entry_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       valence = excluded.valence,
                       arousal = excluded.arousal,
                       updated_at = excluded.updated_at,
                       last_entry_id = excluded.last_entry_id""",
                (
                    chat_id, updated.valence, updated.arousal,
                    updated.updated_at.isoformat(timespec="seconds"), entry_id,
                ),
            )
            self.conn.commit()
            return updated

    # ---- 名册 ----

    def enroll(
        self,
        platform: str,
        user_id: str,
        chat_id: int,
        thread: str | None = None,
        nick: str | None = None,
    ) -> bool:
        """把一个人记进名册。返回 True 表示这是新人（之前没见过）。

        返回值是有用的：调用方据此决定要不要打日志、要不要发自我介绍。
        已经在册的人重复调用是安全的，昵称会顺手更新（对方改名了也跟得上）。
        """
        with self._lock:
            is_new = self.conn.execute(
                "SELECT 1 FROM roster WHERE platform = ? AND user_id = ?",
                (platform, user_id),
            ).fetchone() is None
            # 先 SELECT 后 INSERT 在跨进程同时入册时会撞主键吃 IntegrityError
            # （调用方没捕获的话整条消息都丢了）。ON CONFLICT 把"见过就顺手
            # 更新昵称"压成一条语句，谁先来都安全。
            self.conn.execute(
                "INSERT INTO roster(platform, user_id, chat_id, thread, nick, added_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(platform, user_id) DO UPDATE SET"
                " nick = COALESCE(excluded.nick, roster.nick)",
                (platform, user_id, chat_id, thread, nick,
                 datetime.now(UTC).isoformat(timespec="seconds")),
            )
            self.conn.commit()
            return is_new

    def roster(self, platform: str) -> list[dict]:
        """在册的人。主动消息的收件人列表就是从这里来的。

        已经说过"别发了"的人直接排除——退出的意愿应该在最上游生效，
        免得每个调用方都得记着自己过滤一遍。
        """
        rows = self.conn.execute(
            "SELECT r.* FROM roster r"
            " LEFT JOIN optouts o ON o.chat_id = r.chat_id"
            " WHERE r.platform = ? AND o.chat_id IS NULL"
            " ORDER BY r.added_at",
            (platform,),
        ).fetchall()
        return [dict(r) for r in rows]


def _row_to_entry(r: sqlite3.Row) -> Entry:
    return Entry(
        id=r["id"],
        logged_at=r["logged_at"],
        shot_at=r["shot_at"],
        bucket=r["bucket"],
        weekday=r["weekday"],
        scene=r["scene"],
        move=r["move"],
        said=r["said"],
        note=r["note"],
        reply=r["reply"] if "reply" in r.keys() else None,
        kind=r["kind"] if "kind" in r.keys() else None,
        intent=r["intent"] if "intent" in r.keys() else None,
        material_id=r["material_id"] if "material_id" in r.keys() else None,
        music_track=r["music_track"] if "music_track" in r.keys() else None,
        music_track_role=(
            r["music_track_role"] if "music_track_role" in r.keys() else None
        ),
    )


def _row_to_open_loop(row: sqlite3.Row) -> OpenLoop:
    return OpenLoop(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        title=str(row["title"]),
        kind=str(row["kind"]),
        due_at=row["due_at"],
        status=str(row["status"]),
        source_entry_id=row["source_entry_id"],
        followup_count=int(row["followup_count"]),
        last_followup_at=row["last_followup_at"],
        created_at=str(row["created_at"]),
        resolved_at=row["resolved_at"],
    )
