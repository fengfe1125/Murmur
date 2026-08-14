"""SQLite 记忆层。

存两种东西：最近发生过什么，以及"同一个地方的同一个时段"你来过多少次。
后者才是让它显得懂你的东西——不是从图里看出来的，是数出来的。

分层记忆的思路参考 smixs/iva (https://github.com/smixs/iva)，那边落成
Obsidian markdown，这里为了按维度聚合用 SQLite。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


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
    has_photo   INTEGER NOT NULL DEFAULT 0
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
"""

# 索引单独放，必须在 _migrate() 之后建。
# v0.1 的 entries 表没有 chat_id 列，而这些索引都建在 chat_id 上——
# 跟建表语句写在一起的话，打开一个真正的老库会直接
# "no such column: chat_id"，整个进程起不来。
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chat_spot_bucket ON entries(chat_id, spot, bucket);
CREATE INDEX IF NOT EXISTS idx_chat_id_desc ON entries(chat_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_roster_platform ON roster(platform);
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


class Memory:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
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

    def _migrate(self) -> None:
        """v0.1 的库里是 place 列、没有 chat_id。补上缺的列，别让旧库炸掉。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(entries)")}
        for name, decl in (("chat_id", "INTEGER NOT NULL DEFAULT 0"), ("spot", "TEXT"),
                           ("reply", "TEXT"), ("thread", "TEXT"),
                           ("kind", "TEXT"), ("intent", "TEXT"),
                           ("has_photo", "INTEGER NOT NULL DEFAULT 0")):
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
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO entries
               (chat_id, thread, logged_at, shot_at, bucket, weekday, spot,
                scene, move, said, note, kind, intent, has_photo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_reply(self, entry_id: int, text: str) -> None:
        """用户在它说完之后回的话。判断"这句说得对不对"最直接的信号。"""
        self.conn.execute(
            "UPDATE entries SET reply = ? WHERE id = ?", (text, entry_id)
        )
        self.conn.commit()

    def recent(self, chat_id: int = 0, limit: int = 6) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in reversed(rows)]

    def spot_visits(self, chat_id: int, spot: str | None, bucket: str | None) -> int:
        """同一个匿名地点、同一个时段，以前来过几次。
        没有定位就返回 0——光凭时段说"你又是这个点"太廉价，容易翻车。"""
        if not spot or not bucket:
            return 0
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE chat_id = ? AND spot = ? AND bucket = ?",
            (chat_id, spot, bucket),
        ).fetchone()
        return int(row["n"])


    def recent_outbound(self, chat_id: int, limit: int = 8) -> list[Entry]:
        """最近它主动说过什么。用来避免 10 条/天全是同一句。"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE chat_id = ? AND kind = 'out' "
            "ORDER BY id DESC LIMIT ?", (chat_id, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in reversed(rows)]

    def last_inbound_at(self, chat_id: int) -> str | None:
        """他最后一次说话是什么时候。正在聊的时候别硬插一条主动消息。"""
        row = self.conn.execute(
            "SELECT logged_at FROM entries WHERE chat_id = ? AND kind != 'out' "
            "ORDER BY id DESC LIMIT 1", (chat_id,),
        ).fetchone()
        return row["logged_at"] if row else None

    def unanswered_outbound(self, chat_id: int) -> int:
        """连续几条主动消息没被回。真人被连着无视也会收敛。"""
        rows = self.conn.execute(
            "SELECT kind, reply FROM entries WHERE chat_id = ? ORDER BY id DESC LIMIT 12",
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
        self.conn.execute(
            "INSERT OR IGNORE INTO greeted(chat_id, thread, at) VALUES (?,?,?)",
            (chat_id, thread,
             datetime.now(UTC).isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def set_optout(self, chat_id: int, thread: str | None, on: bool = True) -> None:
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
        row = self.conn.execute(
            "SELECT 1 FROM roster WHERE platform = ? AND user_id = ?",
            (platform, user_id),
        ).fetchone()
        if row is not None:
            if nick:
                self.conn.execute(
                    "UPDATE roster SET nick = ? WHERE platform = ? AND user_id = ?",
                    (nick, platform, user_id),
                )
                self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO roster(platform, user_id, chat_id, thread, nick, added_at)"
            " VALUES (?,?,?,?,?,?)",
            (platform, user_id, chat_id, thread, nick,
             datetime.now(UTC).isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return True

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
    )
