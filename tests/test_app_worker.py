"""Durable App worker, streaming and upload cleanup tests."""

from __future__ import annotations

import base64
import io
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import make_config  # noqa: E402
from PIL import Image  # noqa: E402

from murmur.app_api import erase_account  # noqa: E402
from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore  # noqa: E402
from murmur.app_worker import (  # noqa: E402
    AppWorker,
    ProcessedMoment,
    validate_model_config,
)
from murmur.dossier import Dossier  # noqa: E402
from murmur.engine import Reply  # noqa: E402
from murmur.memory import Memory, thread_key  # noqa: E402
from murmur.moment import Moment  # noqa: E402
from murmur.photo import Photo  # noqa: E402


def app_settings(root: Path) -> AppSettings:
    return AppSettings(
        db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
        data_root=root, upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766",
        app_id="", team_id="", attest_mode="development", attest_root_path=None,
        allow_development=True, development_token="d" * 32,
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        timezone=ZoneInfo("Asia/Shanghai"),
    )


def jpeg_b64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "coral").save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


def oversized_png_header(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class FakeScheduler:
    def __init__(self):
        self.calls = 0

    def run_once(self, _now):
        self.calls += 1
        return 1


class AppWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = app_settings(self.root)
        self.settings.upload_dir.mkdir()
        self.cfg = make_config(self.settings.memory_db_path)
        self.store = AppStore(self.settings.db_path)
        code = self.store.create_invite()
        self.enrollment = self.store.redeem_invite(
            code=code, key_id="dev-worker", public_key=None, receipt=None,
            counter=0, environment="development",
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def queue(self, image: bool = False):
        path = None
        if image:
            path = self.settings.upload_dir / "murmur-upload-test"
            path.write_bytes(b"original-private-photo")
        result = self.store.create_moment(
            user_id=self.enrollment.user_id, note="hello",
            image_path=str(path) if path else None,
            idempotency_key=f"worker-{image}", request_digest=f"digest-{image}",
        )
        return result, path

    def test_streams_bubbles_records_hidden_memory_and_finishes(self):
        result, _ = self.queue()

        def processor(job, _memory, on_bubble):
            on_bubble("first")
            return ProcessedMoment(
                Reply("night desk", "speak", ["first", "second"]),
                Moment.text_only(self.cfg.tz), None,
            )

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        self.assertTrue(worker.process_one())
        events = self.store.events_after(result.moment_id, self.enrollment.user_id)
        self.assertEqual([event["event"] for event in events],
                         ["accepted", "bubble", "bubble", "done"])
        self.assertEqual([event["data"].get("text") for event in events[1:3]],
                         ["first", "second"])
        chat_id, _ = thread_key("app", "direct", self.enrollment.user_id)
        with Memory(self.settings.memory_db_path) as memory:
            entries = memory.recent(chat_id)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].said, "first ⏎ second")

    def test_photo_preview_has_no_exif_and_original_is_always_removed(self):
        result, original = self.queue(image=True)
        photo = Photo(None, None, 31.12345, 121.54321, "test", jpeg_b64())

        def processor(job, _memory, _on_bubble):
            return ProcessedMoment(
                Reply("a room", "quiet", []), Moment.of(photo, self.cfg.tz), photo,
            )

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        worker.process_one()
        self.assertFalse(original.exists())
        row = self.store.moment_for_user(result.moment_id, self.enrollment.user_id)
        preview = Path(row["preview_path"])
        self.assertTrue(preview.is_file())
        if os.name != "nt":  # Windows has no POSIX mode bits; 0600 is enforced where it exists
            self.assertEqual(preview.stat().st_mode & 0o777, 0o600)
        with Image.open(preview) as image:
            self.assertFalse(image.getexif())
        events = self.store.events_after(result.moment_id, self.enrollment.user_id)
        self.assertEqual([event["event"] for event in events],
                         ["accepted", "quiet", "done"])

    def test_failure_is_terminal_and_removes_original(self):
        result, original = self.queue(image=True)

        def processor(*_args):
            raise RuntimeError("secret gateway detail")

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        with self.assertLogs("murmur.app_worker", level="ERROR") as captured:
            worker.process_one()
        self.assertFalse(original.exists())
        events = self.store.events_after(result.moment_id, self.enrollment.user_id)
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["data"]["code"], "processing_failed")
        self.assertTrue(events[-1]["data"]["retryable"])
        self.assertNotIn("secret", events[-1]["data"]["message"])
        self.assertNotIn("secret gateway detail", "\n".join(captured.output))

    def test_heartbeat_prevents_a_slow_job_from_being_reclaimed(self):
        result, _ = self.queue()
        started = threading.Event()
        release = threading.Event()

        def processor(_job, _memory, _on_bubble):
            started.set()
            self.assertTrue(release.wait(2))
            return ProcessedMoment(
                Reply("desk", "speak", ["done"]),
                Moment.text_only(self.cfg.tz), None,
            )

        worker = AppWorker(
            self.store, self.cfg, self.settings, processor=processor,
            heartbeat_interval=0.01, lease_seconds=0.05,
        )
        thread = threading.Thread(target=worker.process_one)
        thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.12)
        with AppStore(self.settings.db_path) as competitor:
            self.assertIsNone(competitor.claim_job(
                "other-worker", lease=timedelta(seconds=0.05)
            ))
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        events = self.store.events_after(result.moment_id, self.enrollment.user_id)
        self.assertEqual(events[-1]["event"], "done")

    def test_reclaimed_job_uses_atomic_memory_link_without_model_replay(self):
        result, _ = self.queue()
        crashed_job = self.store.claim_job("crashed-worker")
        crashed = AppWorker(
            self.store, self.cfg, self.settings, worker_id="crashed-worker",
        )
        processed = ProcessedMoment(
            Reply("desk", "speak", ["once"]), Moment.text_only(self.cfg.tz), None,
        )
        with Memory(self.settings.memory_db_path) as memory:
            crashed._record_memory_once(memory, crashed_job, processed)
        self.store.append_job_event(
            crashed_job, "crashed-worker", "bubble", {"text": "once"}
        )
        self.store.conn.execute(
            "UPDATE app_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (crashed_job.id,),
        )
        self.store.conn.commit()

        def must_not_run(*_args):
            raise AssertionError("model was replayed")

        recovered = AppWorker(
            self.store, self.cfg, self.settings, processor=must_not_run,
            worker_id="recovery-worker",
        )
        self.assertTrue(recovered.process_one())
        events = self.store.events_after(result.moment_id, self.enrollment.user_id)
        self.assertEqual([event["event"] for event in events],
                         ["accepted", "bubble", "done"])
        chat_id, _ = thread_key("app", "direct", self.enrollment.user_id)
        with Memory(self.settings.memory_db_path) as memory:
            self.assertEqual(len(memory.recent(chat_id)), 1)

    def test_account_delete_racing_slow_worker_cannot_recreate_memory(self):
        _, original = self.queue(image=True)
        started = threading.Event()
        release = threading.Event()
        worker_errors: list[BaseException] = []

        def processor(_job, _memory, _on_bubble):
            started.set()
            self.assertTrue(release.wait(2))
            return ProcessedMoment(
                Reply("desk", "speak", ["too late"]),
                Moment.text_only(self.cfg.tz), None,
            )

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)

        def run_worker():
            try:
                worker.process_one()
            except BaseException as exc:  # asserted below
                worker_errors.append(exc)

        thread = threading.Thread(target=run_worker)
        thread.start()
        self.assertTrue(started.wait(1))
        with AppStore(self.settings.db_path) as api_store:
            erase_account(api_store, self.settings, self.enrollment.user_id)
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertFalse(original.exists())
        chat_id, _ = thread_key("app", "direct", self.enrollment.user_id)
        with Memory(self.settings.memory_db_path) as memory:
            self.assertEqual(memory.recent(chat_id), [])
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) FROM app_users"
        ).fetchone()[0], 0)

    def test_raw_upload_is_deleted_before_dossier_refresh_can_fail(self):
        result, original = self.queue(image=True)

        def processor(_job, _memory, _on_bubble):
            return ProcessedMoment(
                Reply("desk", "speak", ["done"]),
                Moment.text_only(self.cfg.tz), None,
            )

        def refresh_after_terminal(*_args, **_kwargs):
            self.assertFalse(original.exists())
            raise RuntimeError("private dossier model output")

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        with patch("murmur.app_worker.refresh", side_effect=refresh_after_terminal):
            with self.assertLogs("murmur.app_worker", level="WARNING") as captured:
                self.assertTrue(worker.process_one())
        self.assertEqual(
            self.store.events_after(result.moment_id, self.enrollment.user_id)[-1]["event"],
            "done",
        )
        self.assertNotIn("private dossier model output", "\n".join(captured.output))

    def test_app_dossier_file_is_forced_to_mode_0600(self):
        self.queue()

        def processor(_job, _memory, _on_bubble):
            return ProcessedMoment(
                Reply("desk", "speak", ["done"]),
                Moment.text_only(self.cfg.tz), None,
            )

        def make_world_readable(_cfg, _memory, _chat_id, label, root):
            dossier = Dossier.load(root, label)
            dossier.save()
            dossier.path.chmod(0o644)
            return dossier

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        with patch("murmur.app_worker.refresh", side_effect=make_world_readable):
            worker.process_one()
        _, label = thread_key("app", "direct", self.enrollment.user_id)
        path = Dossier.load(self.root / "dossiers", label).path
        if os.name != "nt":  # Windows has no POSIX mode bits; 0600 is enforced where it exists
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_small_compressed_image_with_excessive_dimensions_is_rejected_predecode(self):
        raw = self.settings.upload_dir / "murmur-upload-pixel-bomb"
        raw.write_bytes(oversized_png_header(8_000, 7_000))
        result = self.store.create_moment(
            user_id=self.enrollment.user_id, note="image", image_path=str(raw),
            idempotency_key="pixel-bomb", request_digest="pixel-bomb",
        )
        limited = replace(self.settings, max_image_pixels=48_000_000)
        AppWorker(self.store, self.cfg, limited).process_one()
        self.assertFalse(raw.exists())
        events = self.store.events_after(result.moment_id, self.enrollment.user_id)
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["data"]["code"], "image_too_large")
        self.assertFalse(events[-1]["data"]["retryable"])
        self.assertEqual(
            self.store.moment_for_user(result.moment_id, self.enrollment.user_id)[
                "failure_retryable"
            ],
            0,
        )

    def test_production_worker_rejects_missing_key_and_plaintext_model_gateway(self):
        production = replace(
            self.settings,
            attest_mode="production",
            allow_development=False,
            development_token=None,
            app_id="TEAM.com.sakura.Murmur",
            team_id="TEAM",
            public_base_url="https://murmur.example",
            apns_environment="production",
        )
        with self.assertRaises(RuntimeError):
            validate_model_config(
                make_config(self.settings.memory_db_path, api_key=None), production
            )
        with self.assertRaises(RuntimeError):
            validate_model_config(
                make_config(
                    self.settings.memory_db_path,
                    api_key="private-key",
                    base_url="http://model.example/v1",
                ),
                production,
            )

    def test_scheduler_is_integrated_and_throttled_to_one_cycle_per_30_seconds(self):
        scheduler = FakeScheduler()
        worker = AppWorker(
            self.store, self.cfg, self.settings,
            processor=lambda *_: None, scheduler=scheduler,
        )
        self.assertTrue(worker.run_cycle(monotonic_now=100))
        worker.run_cycle(monotonic_now=110)
        worker.run_cycle(monotonic_now=130)
        self.assertEqual(scheduler.calls, 2)

    def test_event_ttl_cleanup_runs_hourly_in_long_lived_worker(self):
        result, _ = self.queue()
        self.store.conn.execute(
            "UPDATE app_events SET created_at='2000-01-01T00:00:00+00:00' WHERE moment_id=?",
            (result.moment_id,),
        )
        self.store.conn.commit()
        worker = AppWorker(
            self.store, self.cfg, self.settings,
            processor=lambda *_: (_ for _ in ()).throw(RuntimeError("not used")),
        )
        # Claiming the queued job would append a fresh terminal event, so mark it
        # terminal first; this test isolates periodic retention cleanup.
        self.store.conn.execute("UPDATE app_jobs SET status='failed'")
        self.store.conn.execute("UPDATE app_moments SET status='failed'")
        self.store.conn.commit()
        worker.run_cycle(monotonic_now=1)
        self.assertEqual(self.store.events_after(result.moment_id, self.enrollment.user_id), [])
        # A second cycle before the hour does not run retention again.
        worker.run_cycle(monotonic_now=60)
        self.assertEqual(worker._next_cleanup, 3601)


if __name__ == "__main__":
    unittest.main(verbosity=2)
