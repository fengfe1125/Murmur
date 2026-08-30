"""Durable worker for App moments.

The HTTP process only validates and queues uploads.  Model work happens here,
so an API restart cannot lose an accepted request and the SSE event log remains
the single source of delivery state.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from PIL import Image

from . import counters
from .affect import apply_message as preview_affect
from .affect import prompt_context
from .app_lock import UserOperationLock
from .app_music import (
    MUSIC_NOT_FOUND_LINE,
    AppMusic,
    load_music_track,
    music_fallback_text,
    music_prompt_line,
    playback_prompt_line,
)
from .app_settings import AppSettings
from .app_store import AccountDeleting, AppStore, Job, NotFound
from .config import Config
from .continuity import refresh_open_loops
from .dossier import Dossier, refresh
from .engine import Reply, read_photo, respond
from .memory import Entry, Memory, thread_key
from .moment import Moment
from .photo import Photo, PhotoTooLarge, save_preview
from .photo import load as load_photo

log = logging.getLogger("murmur.app_worker")


_MEMORY_LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_memory_links (
    moment_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    entry_id   INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""


def _ensure_memory_links(memory: Memory) -> None:
    memory.conn.execute(_MEMORY_LINK_SCHEMA)
    memory.conn.commit()


class InvalidAppImage(ValueError):
    """A permanent, client-actionable image validation failure."""

    def __init__(self, code: str, client_message: str):
        super().__init__(code)
        self.code = code
        self.client_message = client_message


def load_app_photo(path: str | Path, max_image_pixels: int) -> Photo:
    """Decode an App upload, translating decode failures into client errors.

    The bounded-raster decode itself (JPEG ``draft`` + pixel ceiling) lives in
    :func:`murmur.photo.load`; here we only map its exceptions onto the
    permanent, client-actionable :class:`InvalidAppImage` codes.
    """
    try:
        return load_photo(path, max_image_pixels=max_image_pixels)
    except PhotoTooLarge as error:
        raise InvalidAppImage("image_too_large", "图片像素尺寸过大。") from error
    except Image.DecompressionBombError as error:
        raise InvalidAppImage("image_too_large", "图片像素尺寸过大。") from error
    except (OSError, SyntaxError, ValueError) as error:
        raise InvalidAppImage("invalid_image", "图片文件无效。") from error


@dataclass
class ProcessedMoment:
    reply: Reply
    moment: Moment
    photo: Photo | None
    # 当年今日 的读图才有：三个递到他手边的话头，跟着 guess 一起发。
    # 普通 moment 永远是空的——聊天窗口里没有这一行。
    angles: list[str] = field(default_factory=list)
    # 这一轮的歌，最多一首。role 说明它是谁发的：'in' 是他附上的，
    # 'out' 是它被点名之后找出来的。一条 moment 不会两者都有。
    music_track: dict | None = None
    music_track_role: str | None = None

    @property
    def music_card(self) -> dict | None:
        """要作为一张卡片发出去的那首歌——只有它自己挑的那首才发。"""
        return self.music_track if self.music_track_role == "out" else None


class MomentProcessor(Protocol):
    def __call__(
        self, job: Job, memory: Memory, on_bubble: Callable[[str], None]
    ) -> ProcessedMoment: ...


def _provenance(job: Job) -> dict:
    """App 随图声明的事实：拍摄时间、坐标、机上反解出的地名。

    写坏了当没有。这条路径宁可少一句地名，也不该因为一个字段把整条
    moment 弄失败——API 那层已经校验过一遍了，这里只是不信任地读回来。
    """
    # getattr 而不是 job.provenance：同 _archive_context 里的
    # context_moment_ids，Job 的可选字段在测试替身里未必存在。
    raw = getattr(job, "provenance", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _apply_provenance(photo: Photo, provenance: dict) -> Photo:
    """把 App 声明的事实盖到从图里读出来的之上。

    当年今日推上来的是重新编码过的 JPEG，EXIF 已经没了，_read_gps 和
    _read_shot_at 一定读出 None——不盖的话，一张三年前的照片会被当成
    刚拍的。普通发图走原图，EXIF 还在，那时 App 不声明，也就盖不动。
    两条路径共用同一个 Photo。
    """
    shot_at = photo.shot_at
    if raw := provenance.get("shot_at"):
        try:
            shot_at = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            pass
    lat, lon = provenance.get("lat"), provenance.get("lon")
    return replace(
        photo,
        shot_at=shot_at,
        lat=photo.lat if lat is None else float(lat),
        lon=photo.lon if lon is None else float(lon),
    )


class EngineMomentProcessor:
    def __init__(
        self,
        cfg: Config,
        data_root: Path,
        *,
        max_image_pixels: int = 100_000_000,
        music: AppMusic | None = None,
        playback_state: Callable[[str], dict | None] | None = None,
    ):
        self.cfg = cfg
        self.data_root = data_root
        self.max_image_pixels = max_image_pixels
        # None 就是这台部署没开音乐。整条音乐路径靠这一个 None 关掉，
        # 不需要在下面每一处再问一次开关。
        self.music = music
        self.playback_state = playback_state

    def __call__(
        self, job: Job, memory: Memory, on_bubble: Callable[[str], None]
    ) -> ProcessedMoment:
        photo = (
            load_app_photo(job.image_path, self.max_image_pixels)
            if job.image_path else None
        )
        provenance = _provenance(job)
        if photo is not None:
            photo = _apply_provenance(photo, provenance)
        moment = (
            Moment.of(photo, self.cfg.tz, place=provenance.get("place"))
            if photo is not None
            else Moment.text_only(self.cfg.tz)
        )
        chat_id, label = thread_key("app", "direct", job.user_id)
        dossier = Dossier.load(self.data_root / "dossiers", label)
        prompt_parts: list[str] = []
        if not dossier.is_empty:
            prompt_parts.append(dossier.as_prompt())
        if self.cfg.open_loops:
            loops = memory.pending_open_loops(chat_id, limit=4)
            if loops:
                prompt_parts.append(
                    "仍未闭合的近期待办（只有与本轮明显相关时才自然接上，"
                    "不要逐条盘问）：\n" + "\n".join(
                        f"- {loop.title}" for loop in loops
                    )
                )
        if self.cfg.affect:
            state = preview_affect(
                memory.affect_state(chat_id, moment.at), job.note, moment.at
            )
            if tone := prompt_context(state):
                prompt_parts.append(tone)
        prompt_dossier = "\n\n".join(prompt_parts) or None
        # 歌先定下来，模型后说话。定在这里有两个原因：它要进这一轮的
        # prompt（不然它会答一句和卡片无关的话），而且点歌那一轮必须关掉
        # 逐条气泡流——先把曲目确定并落库，再一次性输出文字和卡片。
        shared = self.music.verify_shared(job.music_track) if self.music else None
        choice = (
            self.music.choose_for(job.note)
            if self.music and shared is None
            else None
        )
        chosen = choice.track if choice else None
        context_extra: list[str] = []
        if shared is not None:
            context_extra.append(music_prompt_line(shared, actor="他"))
        if chosen is not None:
            context_extra.append(
                music_prompt_line(chosen, actor="你")
                + "（这首歌会作为卡片和你的话一起发出去，"
                "所以说一句把它递过去的话，不要复述标题和艺人。）"
            )
        elif choice is not None and choice.not_found:
            context_extra.append(
                "（他点了歌，但一首都没找到。别编一首出来，"
                "顺着他的话说下去就行，那句说明会另外发。）"
            )
        if self.playback_state is not None:
            if line := playback_prompt_line(self.playback_state(job.user_id)):
                context_extra.append(line)
        if job.intent == "photo_reading" and photo is not None:
            reading = self._read(
                job, moment, photo, prompt_dossier,
                # 没有坐标就没有「来过几次」可数，也就不必去问库——
                # spot_visits 自己也是这么早退的。
                visits=(
                    memory.spot_visits(chat_id, moment.spot, moment.bucket)
                    if moment.spot else 0
                ),
            )
            if reading is not None:
                return reading
        reply = respond(
            moment, memory, self.cfg, photo=photo, note=job.note, chat_id=chat_id,
            # 点歌那一轮不流式：一条崩溃后重来的 moment 不能换一首歌，也不能
            # 把同一张卡片发两次。曲目落库之后才由调用方一次性发出去。
            on_bubble=None if chosen is not None else on_bubble,
            dossier=prompt_dossier,
            history_entries=self._archive_context(memory, job),
            context_extra=context_extra or None,
        )
        if choice is not None and choice.not_found:
            # 他点了歌却一首都没找到。那句说明是固定的，而且要跟它自己的话
            # 一起进 `say`：这样它会被落库、被崩溃后的重放原样带回来，不必在
            # 发事件那一层再单独记一件事。这一轮也不许沉默。
            reply = Reply(
                scene=reply.scene,
                move="speak",
                say=[*reply.say, MUSIC_NOT_FOUND_LINE],
            )
        track = chosen if chosen is not None else shared
        return ProcessedMoment(
            reply, moment, photo,
            music_track=track,
            music_track_role=("out" if chosen is not None
                              else "in" if shared is not None else None),
        )

    @staticmethod
    def _archive_context(memory: Memory, job: Job) -> list[Entry] | None:
        """Resolve only this user's durable moment links, preserving App order."""
        context_moment_ids = tuple(getattr(job, "context_moment_ids", ()))
        if not context_moment_ids:
            return None
        _ensure_memory_links(memory)
        placeholders = ",".join("?" for _ in context_moment_ids)
        rows = memory.conn.execute(
            "SELECT moment_id,entry_id FROM app_memory_links "
            f"WHERE user_id=? AND moment_id IN ({placeholders})",
            (job.user_id, *context_moment_ids),
        ).fetchall()
        entry_for_moment = {
            str(row["moment_id"]): int(row["entry_id"]) for row in rows
        }
        entry_ids = [
            entry_for_moment[moment_id]
            for moment_id in context_moment_ids
            if moment_id in entry_for_moment
        ]
        entries = memory.entries_by_ids(entry_ids)
        log.info(
            "App archive context requested_count=%d resolved_count=%d",
            len(context_moment_ids),
            len(entries),
        )
        return entries or None

    def _read(
        self, job: Job, moment: Moment, photo: Photo, dossier: str | None,
        *, visits: int = 0,
    ) -> ProcessedMoment | None:
        """当年今日 推过来的那一张：先看图，猜他想说什么，再递三个话头。

        非流式——这一屏是一次调用一次交付，没有可以提前吐的第一条气泡。
        看图失败（模型挂了、超时、输出不合规）返回 None，由调用方退回普通
        回复：这间房宁可少三个话头，也不能开门就是一片空白。
        """
        try:
            reading = read_photo(
                moment, photo, self.cfg, visits=visits, dossier=dossier
            )
        except Exception as error:
            log.warning(
                "App photo reading fell back moment_id=%s error_type=%s",
                job.moment_id, type(error).__name__,
            )
            return None
        return ProcessedMoment(
            Reply(scene=reading.scene, move="speak", say=[reading.guess]),
            moment,
            photo,
            reading.angles,
        )


class AppWorker:
    def __init__(
        self,
        store: AppStore,
        cfg: Config,
        settings: AppSettings,
        processor: MomentProcessor | None = None,
        scheduler=None,
        *,
        worker_id: str | None = None,
        heartbeat_interval: float = 30.0,
        lease_seconds: float = 180.0,
    ):
        self.store = store
        self.cfg = cfg
        self.settings = settings
        self.processor = processor or EngineMomentProcessor(
            cfg, settings.data_root, max_image_pixels=settings.max_image_pixels
        )
        self.scheduler = scheduler
        self.worker_id = worker_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        if heartbeat_interval <= 0 or lease_seconds <= heartbeat_interval * 2:
            raise ValueError("worker lease must exceed two heartbeat intervals")
        self.heartbeat_interval = heartbeat_interval
        self.lease = timedelta(seconds=lease_seconds)
        self._next_proactive_check = 0.0
        self._next_cleanup = 0.0

    def _safe_remove_upload(self, raw_path: str | None) -> None:
        if not raw_path:
            return
        path = Path(raw_path)
        try:
            resolved = path.resolve(strict=False)
            root = self.settings.upload_dir.resolve(strict=False)
            if not resolved.is_relative_to(root):
                log.error("refusing to delete upload outside configured temp root")
                return
            resolved.unlink(missing_ok=True)
        except OSError as exc:
            log.warning(
                "could not remove temporary upload name=%s error_type=%s",
                path.name, type(exc).__name__,
            )

    @staticmethod
    def _ensure_memory_links(memory: Memory) -> None:
        _ensure_memory_links(memory)

    def _linked_memory_entry(self, memory: Memory, job: Job):
        """Find a response already durably recorded before an earlier crash."""
        self._ensure_memory_links(memory)
        return memory.conn.execute(
            "SELECT e.* FROM app_memory_links l JOIN entries e ON e.id=l.entry_id "
            "WHERE l.moment_id=? AND l.user_id=?",
            (job.moment_id, job.user_id),
        ).fetchone()

    def _record_memory_once(
        self, memory: Memory, job: Job, result: ProcessedMoment
    ) -> int:
        """Atomically record Memory and its moment link exactly once.

        The app queue and Memory may use separate SQLite files, so a single
        cross-database transaction is impossible.  The link lives beside the
        Memory entry instead: after a crash, a reclaimed job detects the link
        and finishes the App transaction without calling the model again.
        """
        self._ensure_memory_links(memory)
        chat_id, label = thread_key("app", "direct", job.user_id)
        memory.conn.execute("BEGIN IMMEDIATE")
        try:
            linked = memory.conn.execute(
                "SELECT e.id FROM app_memory_links l JOIN entries e ON e.id=l.entry_id "
                "WHERE l.moment_id=? AND l.user_id=?",
                (job.moment_id, job.user_id),
            ).fetchone()
            if linked:
                memory.conn.commit()
                return int(linked["id"])
            # Repair only an orphaned link.  Account deletion removes links and
            # entries together while holding the same per-user file lock.
            memory.conn.execute(
                "DELETE FROM app_memory_links WHERE moment_id=?", (job.moment_id,)
            )
            cur = memory.conn.execute(
                """INSERT INTO entries
                   (chat_id,thread,logged_at,shot_at,bucket,weekday,spot,
                    scene,move,said,note,kind,intent,has_photo,
                    music_track,music_track_role)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chat_id,
                    label,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    (result.photo.shot_at.isoformat(timespec="seconds")
                     if result.photo and result.photo.shot_at else None),
                    result.moment.bucket,
                    result.moment.weekday,
                    result.moment.spot,
                    result.reply.scene,
                    result.reply.move,
                    result.reply.joined,
                    job.note,
                    "in",
                    None,
                    1 if result.photo is not None else 0,
                    # 落的是服务端核验过的那一份，不是客户端提交的快照。
                    (json.dumps(
                        result.music_track, ensure_ascii=False,
                        separators=(",", ":"), sort_keys=True,
                    ) if result.music_track else None),
                    result.music_track_role,
                ),
            )
            entry_id = int(cur.lastrowid)
            memory.conn.execute(
                "INSERT INTO app_memory_links(moment_id,user_id,entry_id,created_at) "
                "VALUES(?,?,?,?)",
                (job.moment_id, job.user_id, entry_id,
                 datetime.now(UTC).isoformat(timespec="seconds")),
            )
            memory.conn.commit()
            return entry_id
        except Exception:
            memory.conn.rollback()
            raise

    def _start_heartbeat(
        self, job: Job
    ) -> tuple[threading.Event, threading.Event, threading.Thread]:
        stop = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            failures = 0
            while not stop.wait(self.heartbeat_interval):
                try:
                    if not self.store.renew_job(
                        job, self.worker_id, lease=self.lease
                    ):
                        lost.set()
                        return
                    failures = 0
                except Exception as error:
                    # 一次瞬时失败（比如 SQLite busy）不等于丢了租约。
                    # 连续三次才置 lost：间隔 30s、租约 180s 时仍留有余量。
                    failures += 1
                    log.log(
                        logging.ERROR if failures >= 3 else logging.WARNING,
                        "App worker heartbeat failed moment_id=%s error_type=%s",
                        job.moment_id, type(error).__name__,
                    )
                    if failures >= 3:
                        lost.set()
                        return

        thread = threading.Thread(
            target=heartbeat,
            name=f"murmur-heartbeat-{job.id[:8]}",
            daemon=True,
        )
        thread.start()
        return stop, lost, thread

    def _complete_linked(
        self,
        memory: Memory,
        job: Job,
        linked,
        emitted: set[str],
        lost: threading.Event,
    ) -> bool:
        """Finish a job whose Memory commit survived a prior worker crash."""
        with UserOperationLock(self.settings.data_root, job.user_id):
            self.store.require_user_ready(job.user_id)
            if lost.is_set() or not self.store.owns_job(job, self.worker_id):
                return False
            bubbles = [part.strip() for part in (linked["said"] or "").split(" ⏎ ")
                       if part.strip()]
            for bubble in bubbles:
                if bubble in emitted:
                    continue
                if not self.store.append_job_event(
                    job, self.worker_id, "bubble", {"text": bubble}
                ):
                    lost.set()
                    return False
                emitted.add(bubble)
            # 上一次崩溃前已经选好并落库的那首歌。从库里读回来重发，绝不
            # 重新调模型、也绝不重新选一首——那样他会收到两首不同的歌。
            keys = linked.keys()
            card = (
                load_music_track(linked["music_track"])
                if "music_track" in keys and "music_track_role" in keys
                and linked["music_track_role"] == "out"
                else None
            )
            if card is not None:
                self._append_music_card(job, card, emitted, lost)
                if lost.is_set():
                    return False
            preview_path: str | None = None
            if linked["has_photo"] and job.image_path and Path(job.image_path).is_file():
                photo = load_app_photo(job.image_path, self.settings.max_image_pixels)
                preview_path = str(save_preview(
                    self.settings.data_root, int(linked["id"]), photo
                ))
                del photo
            completed = self.store.complete_owned_job(
                job,
                self.worker_id,
                scene=linked["scene"] or "",
                move=linked["move"] or "quiet",
                memory_entry_id=int(linked["id"]),
                preview_path=preview_path,
                quiet=((linked["move"] == "quiet" or not bubbles)
                       and card is None),
            )
            if completed:
                # Raw originals have a shorter lifetime than sleep-time dossier
                # work.  Delete before refresh can block or fail.
                self._safe_remove_upload(job.image_path)
                self._refresh_continuity(memory, job, int(linked["id"]))
                self._refresh_dossier(memory, job)
            return completed

    def _append_music_card(
        self, job: Job, track: dict, emitted: set[str], lost: threading.Event
    ) -> None:
        """把这一轮的歌作为一条普通气泡发出去。

        故意仍然是 `bubble`，而不是一种新事件：旧客户端会忽略多出来的字段，
        只显示文字兜底那一行，音乐不必自带一条事件流。发在文字之后、done
        之前，而且和文字气泡走同一套去重——崩溃重来时不能发出第二张卡片。
        """
        text = music_fallback_text(track)
        if lost.is_set() or text in emitted:
            return
        if self.store.append_job_event(
            job, self.worker_id, "bubble", {"text": text, "music_track": track}
        ):
            emitted.add(text)
        else:
            lost.set()

    def _append_angles(self, job: Job, result: ProcessedMoment,
                       lost: threading.Event) -> None:
        """把读图带回来的三个话头发出去，跟在 guess 后面、done 之前。

        只有 当年今日 的读图会带话头，聊天窗口的普通 moment 永远走空路径。
        崩溃重试时 job 会重新处理，已发过的话头不能重复发。
        """
        if not result.angles or lost.is_set():
            return
        try:
            if any(
                event["event"] == "angles"
                for event in self.store.events_after(
                    job.moment_id, job.user_id, verify=False
                )
            ):
                return
        except Exception as error:
            log.warning(
                "App angles skipped moment_id=%s error_type=%s",
                job.moment_id, type(error).__name__,
            )
            return
        if not self.store.append_job_event(
            job, self.worker_id, "angles", {"angles": result.angles}
        ):
            lost.set()

    def _refresh_dossier(self, memory: Memory, job: Job) -> None:
        """Refresh private long-term memory after SSE is already terminal."""
        chat_id, label = thread_key("app", "direct", job.user_id)
        try:
            refreshed = refresh(
                self.cfg,
                memory,
                chat_id,
                label,
                self.settings.data_root / "dossiers",
            )
            dossier_path = refreshed.path if refreshed is not None else Dossier.load(
                self.settings.data_root / "dossiers", label
            ).path
            if dossier_path.is_file():
                dossier_path.chmod(0o600)
        except Exception as error:
            # Memory compaction is sleep-time work.  Never turn a delivered reply
            # into a retry, and never log model output that may contain user text.
            log.warning(
                "App dossier refresh failed user_id=%s error_type=%s",
                job.user_id, type(error).__name__,
            )

    def _refresh_continuity(
        self, memory: Memory, job: Job, entry_id: int
    ) -> None:
        """Update continuity state only after the reply is already terminal.

        These are enrichment jobs, never delivery prerequisites.  A model or
        SQLite failure here must not turn a reply the user has seen into a
        retryable moment.
        """
        chat_id, _ = thread_key("app", "direct", job.user_id)
        now = datetime.now(self.cfg.tz)
        if self.cfg.affect:
            try:
                # Reserve the even sequence for the inbound note; the odd one
                # is used if the user later acknowledges this reply in App API.
                memory.apply_affect_message(chat_id, entry_id * 2, job.note, now)
            except Exception as error:
                log.warning(
                    "App affect update failed user_id=%s error_type=%s",
                    job.user_id, type(error).__name__,
                )
        if self.cfg.open_loops:
            try:
                refresh_open_loops(
                    self.cfg,
                    memory,
                    chat_id,
                    entry_id,
                    job.note,
                    now=now,
                )
                memory.expire_open_loops(chat_id, now)
            except Exception as error:
                log.warning(
                    "App open-loop refresh failed user_id=%s error_type=%s",
                    job.user_id, type(error).__name__,
                )

    def process_one(self) -> bool:
        job = self.store.claim_job(self.worker_id, lease=self.lease)
        if job is None:
            return False
        stop, lost, heartbeat = self._start_heartbeat(job)
        emitted = set(self.store.event_bubbles(job.moment_id))
        cleaned_by_terminal_owner = False

        def on_bubble(text: str) -> None:
            text = str(text).strip()
            if not text or text in emitted or lost.is_set():
                return
            if self.store.append_job_event(
                job, self.worker_id, "bubble", {"text": text}
            ):
                emitted.add(text)
            else:
                lost.set()

        try:
            with Memory(self.settings.memory_db_path) as memory:
                linked = self._linked_memory_entry(memory, job)
                if linked is not None:
                    cleaned_by_terminal_owner = self._complete_linked(
                        memory, job, linked, emitted, lost
                    )
                    return True
                result = self.processor(job, memory, on_bubble)
                card = result.music_card
                # A fake/non-streaming processor, or a model response salvaged after
                # truncated JSON, can return bubbles that were not emitted live.
                #
                # 点歌那一轮一条都不在这里发：曲目要先跟这条 entry 一起落库，
                # 否则中途崩掉之后重来的那一次可能换一首歌，或者把同一张卡片
                # 发第二次。下面拿到锁、写完 Memory 之后再一次性发。
                if card is None:
                    for bubble in result.reply.say:
                        on_bubble(bubble)
                # 三个话头在 done 之前发：读图已经把它们一起带回来了，
                # 这里只是把它们放上事件流，普通 moment 到这里什么都不做。
                self._append_angles(job, result, lost)
                # Account deletion and final Memory/App writes share this lock.
                # A delete can cancel slow model work, but it can never return 204
                # and then have this worker recreate the user's memory afterward.
                with UserOperationLock(self.settings.data_root, job.user_id):
                    self.store.require_user_ready(job.user_id)
                    if lost.is_set() or not self.store.owns_job(job, self.worker_id):
                        return True
                    entry_id = self._record_memory_once(memory, job, result)
                    if card is not None:
                        # 歌已经在库里了，现在才发。重来的那一次会从
                        # `_complete_linked` 读回同一首，不会重新选。
                        for bubble in result.reply.say:
                            on_bubble(bubble)
                        self._append_music_card(job, card, emitted, lost)
                    preview_path: str | None = None
                    if result.photo is not None:
                        preview_path = str(save_preview(
                            self.settings.data_root, entry_id, result.photo
                        ))
                    cleaned_by_terminal_owner = self.store.complete_owned_job(
                        job,
                        self.worker_id,
                        scene=result.reply.scene,
                        move=result.reply.move,
                        memory_entry_id=entry_id,
                        preview_path=preview_path,
                        # 发出过一张卡片就不算这一轮没说话，哪怕模型自己选了
                        # quiet：屏幕上确实多了一条东西。
                        quiet=result.reply.silent and card is None,
                    )
                    if cleaned_by_terminal_owner:
                        self._safe_remove_upload(job.image_path)
                        self._refresh_continuity(memory, job, entry_id)
                        # Release the downsampled base64 before dossier refresh can
                        # spend up to the model timeout doing sleep-time work.
                        del result
                        self._refresh_dossier(memory, job)
            return True
        except InvalidAppImage as error:
            log.warning(
                "App image rejected moment_id=%s error_type=%s",
                job.moment_id, type(error).__name__,
            )
            try:
                cleaned_by_terminal_owner = self.store.fail_job(
                    job, code=error.code, message=error.client_message,
                    retryable=False, worker_id=self.worker_id,
                )
            except Exception as terminal_error:
                log.error(
                    "App image terminal update failed moment_id=%s error_type=%s",
                    job.moment_id, type(terminal_error).__name__,
                )
            return True
        except (AccountDeleting, NotFound):
            # Account deletion owns cleanup and has already cancelled the job.
            return True
        except Exception as error:
            log.error(
                "app_moment category=retryable_failure moment_id=%s error_type=%s",
                job.moment_id, type(error).__name__,
            )
            counters.bump(self.cfg, "app_moment.retryable_failure")
            # Do not leak gateway messages or user content into the API response.
            try:
                cleaned_by_terminal_owner = self.store.fail_job(
                    job, code="processing_failed",
                    message="Murmur 暂时没有接住，请稍后重试。",
                    retryable=True, worker_id=self.worker_id,
                )
            except Exception as terminal_error:
                log.error(
                    "App moment terminal update failed moment_id=%s error_type=%s",
                    job.moment_id, type(terminal_error).__name__,
                )
            return True
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_interval * 2))
            # Success, model failure, Pillow failure and cancellation all converge
            # here.  A worker that lost its lease must leave the raw upload for
            # the new owner; terminal success/failure owns its final deletion.
            if cleaned_by_terminal_owner:
                self._safe_remove_upload(job.image_path)

    def cleanup(self) -> None:
        from datetime import timedelta

        self.store.cleanup_events(timedelta(hours=self.settings.event_ttl_hours))
        # 听歌状态只是这一阵子的上下文。读的时候会顺手清掉过期的那一行，但一个
        # 不再打开 App 的人不会再被读到——所以这里也扫一遍。
        self.store.cleanup_playback_states()
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 24 * 60 * 60
        for path in self.settings.upload_dir.glob("murmur-upload-*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                log.warning("could not clean stale upload %s", path.name)

    def run_cycle(self, *, monotonic_now: float | None = None) -> bool:
        """Process one inbound job and, at most every 30s, proactive work."""
        did_work = self.process_one()
        clock = time.monotonic() if monotonic_now is None else monotonic_now
        if clock >= self._next_cleanup:
            self.cleanup()
            self._next_cleanup = clock + 60 * 60
        if self.scheduler is not None and clock >= self._next_proactive_check:
            try:
                delivered = self.scheduler.run_once(datetime.now(UTC))
                did_work = bool(delivered) or did_work
            except Exception as error:
                log.error("proactive scheduler cycle failed error_type=%s",
                          type(error).__name__)
            self._next_proactive_check = clock + 30.0
        return did_work

    def serve_forever(self, poll_interval: float = 0.75) -> None:
        log.info("App worker %s started", self.worker_id)
        while True:
            if not self.run_cycle():
                time.sleep(poll_interval)


def run() -> None:
    """CLI entry point for ``murmur app-worker``."""
    cfg = Config.load()
    settings = AppSettings.from_env(cfg)
    settings.validate()
    validate_model_config(cfg, settings)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings.upload_dir.chmod(0o700)
    except OSError:
        pass
    with AppStore(settings.db_path) as store:
        _start_balance_poller(cfg)
        apns_configured = all((
            settings.apns_key_path, settings.apns_key_id,
            settings.apns_team_id, settings.apns_topic,
        ))
        if settings.production:
            # The official channel promises proactive delivery.  Starting a
            # production worker without APNs would silently break that promise.
            settings.validate_apns()
        from .app_push import EngineProactiveGenerator, ProactiveScheduler

        providers = {}
        if apns_configured:
            from .app_push import APNsProvider

            provider = APNsProvider.from_settings(
                settings, on_invalid_token=store.invalidate_push_token
            )
            providers[provider.platform] = provider
        # Android is opt-in: without FCM configured the iOS half still runs,
        # and any Android delivery stays pending rather than being buried.
        if settings.fcm_configured:
            from .app_push_fcm import FCMProvider

            fcm = FCMProvider.from_settings(
                settings, on_invalid_token=store.invalidate_push_token
            )
            providers[fcm.platform] = fcm
        if not providers:
            # Push providers are optional outside production: proactive
            # moments are still generated and wait for in-app polling on
            # /v1/proactive/current instead of never existing at all.
            log.warning(
                "no push providers configured; proactive moments rely on in-app polling"
            )
        scheduler = ProactiveScheduler(
            store, providers, EngineProactiveGenerator(
                cfg, data_root=settings.data_root,
                memory_db_path=settings.memory_db_path,
            ), lock_root=settings.data_root,
            delivery_budget_seconds=settings.push_delivery_budget_seconds,
        )
        AppWorker(
            store, cfg, settings, processor=build_processor(cfg, settings, store),
            scheduler=scheduler,
        ).serve_forever()


def build_processor(
    cfg: Config, settings: AppSettings, store: AppStore
) -> EngineMomentProcessor:
    """The processor this deployment actually runs, music included or not.

    Music is off unless the switch is on and a catalog key exists, so a worker
    on a machine without one behaves exactly as it did before the feature.
    """
    music = None
    if settings.music_enabled and settings.audius_api_key:
        from .app_music import AppMusicPlanner, AudiusCatalogClient

        music = AppMusic(
            AudiusCatalogClient(
                settings.audius_api_key, base_url=settings.audius_api_base_url
            ),
            AppMusicPlanner(cfg),
        )
    return EngineMomentProcessor(
        cfg, settings.data_root,
        max_image_pixels=settings.max_image_pixels,
        music=music,
        # 播放状态只是这一轮的上下文，过期就没有了。取不到不算错。
        playback_state=(store.current_playback if music is not None else None),
    )


def _start_balance_poller(cfg: Config) -> None:
    """余额采集挂在常驻的 worker 上，而不是只活在看板里。

    历史原因：采集最早写在看板进程里，但生产 VPS 上没人天天开面板，
    曲线全是大段空白。worker 是 Always-On 的那个进程，放这里。
    daemon 线程随进程退出；看板自己单跑时还会再起一个同样的 poller，
    balance.snapshot 的同值去重保证两边同时跑也不会写重复行。
    """
    from .balance import poller

    threading.Thread(
        target=poller, args=(cfg, threading.Event()),
        name="murmur-balance", daemon=True,
    ).start()


def main() -> None:
    run()


def validate_model_config(cfg: Config, settings: AppSettings) -> None:
    """Fail closed before a production worker accepts private content."""
    if not settings.production:
        return
    if not cfg.api_key or not cfg.api_key.strip():
        raise RuntimeError("production App worker requires a model API key")
    parsed = urlparse(cfg.base_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("production App worker model base URL must use HTTPS")
