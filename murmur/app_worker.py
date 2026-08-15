"""Durable worker for App moments.

The HTTP process only validates and queues uploads.  Model work happens here,
so an API restart cannot lose an accepted request and the SSE event log remains
the single source of delivery state.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from PIL import Image, ImageOps

from .app_lock import UserOperationLock
from .app_settings import AppSettings
from .app_store import AccountDeleting, AppStore, Job, NotFound
from .config import Config
from .dossier import Dossier, refresh
from .engine import Reply, respond
from .memory import Memory, thread_key
from .moment import Moment
from .photo import (
    _EXIF_TAGS,
    JPEG_QUALITY,
    Photo,
    _read_gps,
    _read_shot_at,
    _target_edge,
    save_preview,
)

log = logging.getLogger("murmur.app_worker")


_MEMORY_LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_memory_links (
    moment_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    entry_id   INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""


class InvalidAppImage(ValueError):
    """A permanent, client-actionable image validation failure."""

    def __init__(self, code: str, client_message: str):
        super().__init__(code)
        self.code = code
        self.client_message = client_message


def load_app_photo(path: str | Path, max_image_pixels: int) -> Photo:
    """Decode an App upload with a bounded output raster and no EXIF copy.

    JPEG ``draft`` asks libjpeg to subsample during decode, which keeps a 48MP
    phone photo from materialising as several full-size RGB buffers.  Other
    formats still pass the pixel ceiling and are reduced immediately after the
    decoder opens them.
    """
    source_path = Path(path)
    try:
        with Image.open(source_path) as source:
            width, height = source.size
            if width <= 0 or height <= 0:
                raise InvalidAppImage("invalid_image", "图片文件无效。")
            if width * height > max_image_pixels:
                raise InvalidAppImage("image_too_large", "图片像素尺寸过大。")
            exif = source.getexif()
            lat, lon = _read_gps(exif)
            make = exif.get(_EXIF_TAGS["Make"])
            model = exif.get(_EXIF_TAGS["Model"])
            camera = (
                " ".join(str(value).strip() for value in (make, model) if value)
                or None
            )
            shot_at = _read_shot_at(exif)
            edge = _target_edge(width, height)
            if (source.format or "").upper() in {"JPEG", "MPO"}:
                source.draft("RGB", (edge, edge))
            source.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            upright = ImageOps.exif_transpose(source) or source
            if upright.mode != "RGB":
                upright = upright.convert("RGB")
            buffer = io.BytesIO()
            upright.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    except InvalidAppImage:
        raise
    except Image.DecompressionBombError as error:
        raise InvalidAppImage("image_too_large", "图片像素尺寸过大。") from error
    except (OSError, SyntaxError, ValueError) as error:
        raise InvalidAppImage("invalid_image", "图片文件无效。") from error
    return Photo(
        path=source_path,
        shot_at=shot_at,
        lat=lat,
        lon=lon,
        camera=camera,
        image_b64=base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
    )


@dataclass
class ProcessedMoment:
    reply: Reply
    moment: Moment
    photo: Photo | None


class MomentProcessor(Protocol):
    def __call__(
        self, job: Job, memory: Memory, on_bubble: Callable[[str], None]
    ) -> ProcessedMoment: ...


class EngineMomentProcessor:
    def __init__(
        self, cfg: Config, data_root: Path, *, max_image_pixels: int = 100_000_000
    ):
        self.cfg = cfg
        self.data_root = data_root
        self.max_image_pixels = max_image_pixels

    def __call__(
        self, job: Job, memory: Memory, on_bubble: Callable[[str], None]
    ) -> ProcessedMoment:
        photo = (
            load_app_photo(job.image_path, self.max_image_pixels)
            if job.image_path else None
        )
        moment = (
            Moment.of(photo, self.cfg.tz) if photo is not None
            else Moment.text_only(self.cfg.tz)
        )
        chat_id, label = thread_key("app", "direct", job.user_id)
        dossier = Dossier.load(self.data_root / "dossiers", label)
        reply = respond(
            moment, memory, self.cfg, photo=photo, note=job.note, chat_id=chat_id,
            on_bubble=on_bubble,
            dossier=None if dossier.is_empty else dossier.as_prompt(),
        )
        return ProcessedMoment(reply, moment, photo)


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
        memory.conn.execute(_MEMORY_LINK_SCHEMA)
        memory.conn.commit()

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
                    scene,move,said,note,kind,intent,has_photo)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            while not stop.wait(self.heartbeat_interval):
                try:
                    if not self.store.renew_job(
                        job, self.worker_id, lease=self.lease
                    ):
                        lost.set()
                        return
                except Exception as error:
                    log.error(
                        "App worker heartbeat failed moment_id=%s error_type=%s",
                        job.moment_id, type(error).__name__,
                    )
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
                quiet=(linked["move"] == "quiet" or not bubbles),
            )
            if completed:
                # Raw originals have a shorter lifetime than sleep-time dossier
                # work.  Delete before refresh can block or fail.
                self._safe_remove_upload(job.image_path)
                self._refresh_dossier(memory, job)
            return completed

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
                # A fake/non-streaming processor, or a model response salvaged after
                # truncated JSON, can return bubbles that were not emitted live.
                for bubble in result.reply.say:
                    on_bubble(bubble)
                # Account deletion and final Memory/App writes share this lock.
                # A delete can cancel slow model work, but it can never return 204
                # and then have this worker recreate the user's memory afterward.
                with UserOperationLock(self.settings.data_root, job.user_id):
                    self.store.require_user_ready(job.user_id)
                    if lost.is_set() or not self.store.owns_job(job, self.worker_id):
                        return True
                    entry_id = self._record_memory_once(memory, job, result)
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
                        quiet=result.reply.silent,
                    )
                    if cleaned_by_terminal_owner:
                        self._safe_remove_upload(job.image_path)
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
                "App moment failed moment_id=%s error_type=%s",
                job.moment_id, type(error).__name__,
            )
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
        scheduler = None
        apns_configured = all((
            settings.apns_key_path, settings.apns_key_id,
            settings.apns_team_id, settings.apns_topic,
        ))
        if settings.production:
            # The official channel promises proactive delivery.  Starting a
            # production worker without APNs would silently break that promise.
            settings.validate_apns()
        if apns_configured:
            from .app_push import APNsProvider, EngineProactiveGenerator, ProactiveScheduler

            provider = APNsProvider.from_settings(
                settings, on_invalid_token=store.invalidate_push_token
            )
            providers = {provider.platform: provider}
            # Android is opt-in: without FCM configured the iOS half still runs,
            # and any Android delivery stays pending rather than being buried.
            if settings.fcm_configured:
                from .app_push_fcm import FCMProvider

                fcm = FCMProvider.from_settings(
                    settings, on_invalid_token=store.invalidate_push_token
                )
                providers[fcm.platform] = fcm
            else:
                log.info("FCM not configured; Android push delivery is disabled")
            scheduler = ProactiveScheduler(
                store, providers, EngineProactiveGenerator(
                    cfg, data_root=settings.data_root,
                    memory_db_path=settings.memory_db_path,
                ), lock_root=settings.data_root,
            )
        else:
            log.warning("APNs/proactive delivery disabled in explicit development mode")
        AppWorker(store, cfg, settings, scheduler=scheduler).serve_forever()


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
