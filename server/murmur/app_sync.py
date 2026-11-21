"""Opt-in account history. Generation barriers reject stale offline uploads."""

import io
import json
import re
from datetime import datetime

from .app_store import Conflict, NotFound


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_sync_settings (
            user_id TEXT PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
            enabled INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 0,
            chat_generation INTEGER NOT NULL DEFAULT 0,
            archive_generation INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS app_sync_records (
            user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, id TEXT NOT NULL, generation INTEGER NOT NULL,
            revision INTEGER NOT NULL, payload TEXT NOT NULL,
            preview BLOB,
            PRIMARY KEY(user_id,kind,id)
        );
        CREATE INDEX IF NOT EXISTS app_sync_records_cursor ON app_sync_records(user_id,kind,revision);
    """)


def kind_column(kind):
    if kind not in {"chat", "archive"}:
        raise ValueError("记录类型无效。")
    return kind + "_generation"


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError("记录格式无效。")
    allowed = {
        "id",
        "author",
        "text",
        "sent_at",
        "archive_day",
        "moment_id",
        "reply_id",
        "music_track",
    }
    if set(record) - allowed:
        raise ValueError("记录包含不支持的字段。")
    if (
        not isinstance(record.get("id"), str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,180}", record["id"])
        or ".." in record["id"]
    ):
        raise ValueError("记录 ID 无效。")
    if record.get("author") not in {"you", "murmur"}:
        raise ValueError("记录作者无效。")
    if not isinstance(record.get("text"), str) or len(record["text"]) > 20000:
        raise ValueError("记录文字无效。")
    try:
        stamp = datetime.fromisoformat(record["sent_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("记录日期无效。") from None
    for name in ("archive_day", "moment_id", "reply_id"):
        value = record.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 180):
            raise ValueError("记录关联无效。")
    track = record.get("music_track")
    if track is not None:
        # Preserve only portable card metadata, never provider credentials/URLs.
        if not isinstance(track, dict) or set(track) - {
            "provider",
            "track_id",
            "title",
            "artist",
            "album",
            "canonical_url",
        }:
            raise ValueError("歌曲卡片元数据无效。")
        if any(not isinstance(v, str) or len(v) > 500 for v in track.values()):
            raise ValueError("歌曲卡片元数据无效。")
        if track.get("canonical_url"):
            from urllib.parse import parse_qs, urlsplit

            url = urlsplit(track["canonical_url"])
            if (
                url.scheme != "https"
                or url.hostname not in {"audius.co", "music.163.com"}
                or url.username
                or url.password
                or set(parse_qs(url.query)) - {"id"}
            ):
                raise ValueError("歌曲链接必须是公开曲目链接。")
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class HistorySync:
    def __init__(self, store):
        self.store = store

    def state(self, db, user):
        self.store.require_user_ready(user)
        db.execute("INSERT OR IGNORE INTO app_sync_settings(user_id) VALUES(?)", (user,))
        row = dict(
            db.execute("SELECT * FROM app_sync_settings WHERE user_id=?", (user,)).fetchone()
        )
        row.pop("user_id")
        row["enabled"] = bool(row["enabled"])
        return row

    def settings(self, user, enabled=None):
        with self.store._tx() as db:
            self.state(db, user)
            if enabled is not None:
                if type(enabled) is not bool:
                    raise ValueError("同步开关无效。")
                db.execute(
                    "UPDATE app_sync_settings SET enabled=? WHERE user_id=?", (int(enabled), user)
                )
            return self.state(db, user)

    def ready(self, db, user, kind, generation):
        column = kind_column(kind)
        state = self.state(db, user)
        if not state["enabled"]:
            raise Conflict("同步尚未开启。")
        if type(generation) is not int or state[column] != generation:
            raise Conflict("记录已在其他设备删除，请先拉取最新同步状态。")
        return state

    def upload(self, user, kind, generation, records):
        if not isinstance(records, list) or not 1 <= len(records) <= 100:
            raise ValueError("每批需要 1 至 100 条记录。")
        validated = [(record, validate_record(record)) for record in records]
        with self.store._tx() as db:
            state = self.ready(db, user, kind, generation)
            revision = state["revision"]
            for record, payload in validated:
                existing = db.execute(
                    "SELECT payload FROM app_sync_records WHERE user_id=? AND kind=? AND id=?",
                    (user, kind, record["id"]),
                ).fetchone()
                if existing:
                    prior = json.loads(existing[0])
                    # The same server reply can be observed at different local
                    # times. Its stable ID, moment and contents identify it;
                    # retain the first observation's date without a second row.
                    same_reply = (
                        record.get("author") == "murmur"
                        and record.get("moment_id")
                        and {k: v for k, v in prior.items() if k != "sent_at"}
                        == {k: v for k, v in record.items() if k != "sent_at"}
                    )
                    if existing[0] != payload and not same_reply:
                        raise Conflict("同一记录 ID 的内容不同，请保留本机记录并重试。")
                    continue
                moment = record.get("moment_id")
                if (
                    moment
                    and db.execute(
                        "SELECT 1 FROM app_moments WHERE id=? AND user_id<>?", (moment, user)
                    ).fetchone()
                ):
                    raise NotFound("moment not found")
                revision += 1
                db.execute(
                    "INSERT INTO app_sync_records(user_id,kind,id,generation,revision,payload) VALUES(?,?,?,?,?,?)",
                    (user, kind, record["id"], generation, revision, payload),
                )
            db.execute("UPDATE app_sync_settings SET revision=? WHERE user_id=?", (revision, user))
            return {"revision": revision, "generation": generation}

    def pull(self, user, kind, cursor=0, limit=100):
        column = kind_column(kind)
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("同步游标无效。")
        with self.store._tx() as db:
            state = self.state(db, user)
            if not state["enabled"]:
                raise Conflict("同步尚未开启。")
            rows = db.execute(
                "SELECT id,payload,revision,preview IS NOT NULL AS has_preview FROM app_sync_records WHERE user_id=? AND kind=? AND revision>? ORDER BY revision LIMIT ?",
                (user, kind, cursor, limit + 1),
            ).fetchall()
            page = rows[:limit]
            return {
                "generation": state[column],
                "records": [
                    {
                        "record": json.loads(r["payload"]),
                        "revision": r["revision"],
                        "has_preview": bool(r["has_preview"]),
                    }
                    for r in page
                ],
                "cursor": page[-1]["revision"] if len(rows) > limit else state["revision"],
                "has_more": len(rows) > limit,
            }

    def clear(self, user, kind, generation):
        column = kind_column(kind)
        with self.store._tx() as db:
            state = self.state(db, user)
            # Replaying a lost delete response must not delete newer records.
            if type(generation) is not int or generation < 0:
                raise ValueError("删除版本无效。")
            if generation == state[column]:
                db.execute("DELETE FROM app_sync_records WHERE user_id=? AND kind=?", (user, kind))
                db.execute(
                    f"UPDATE app_sync_settings SET {column}={column}+1,revision=revision+1 WHERE user_id=?",
                    (user,),
                )
            elif generation > state[column]:
                raise Conflict("删除版本无效。")
            return self.state(db, user)

    def upload_preview(self, user, kind, generation, record_id, data):
        from PIL import Image, ImageOps

        if len(data) > 180000:
            raise ValueError("预览图过大。")
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > 4000000:
                    raise ValueError("预览图尺寸过大。")
                preview = ImageOps.exif_transpose(source).convert("RGB")
                preview.thumbnail((1280, 1280))
                output = io.BytesIO()
                preview.save(output, format="JPEG", quality=75)
                encoded = output.getvalue()
        except (OSError, Image.DecompressionBombError):
            raise ValueError("预览图格式无效。") from None
        with self.store._tx() as db:
            state = self.ready(db, user, kind, generation)
            row = db.execute(
                "SELECT preview FROM app_sync_records WHERE user_id=? AND kind=? AND id=?",
                (user, kind, record_id),
            ).fetchone()
            if row is None:
                raise NotFound("record not found")
            if row[0] is not None:
                if bytes(row[0]) != encoded:
                    raise Conflict("记录已有不同的预览图。")
                return {"revision": state["revision"]}
            revision = state["revision"] + 1
            db.execute(
                "UPDATE app_sync_records SET preview=?,revision=? WHERE user_id=? AND kind=? AND id=?",
                (encoded, revision, user, kind, record_id),
            )
            db.execute("UPDATE app_sync_settings SET revision=? WHERE user_id=?", (revision, user))
            return {"revision": revision}

    def preview(self, user, kind, record_id):
        kind_column(kind)
        with self.store._tx() as db:
            state = self.state(db, user)
            if not state["enabled"]:
                raise Conflict("同步尚未开启。")
            row = db.execute(
                "SELECT preview FROM app_sync_records WHERE user_id=? AND kind=? AND id=?",
                (user, kind, record_id),
            ).fetchone()
            if not row or row[0] is None:
                raise NotFound("preview not found")
            return bytes(row[0])
