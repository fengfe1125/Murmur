"""Push delivery and the restrained first-party proactive schedule.

APNs lives here; FCM lives in :mod:`murmur.app_push_fcm`.  The scheduler holds
one provider per platform and never inspects a provider's status codes -- see
:class:`PushResult`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import time
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .app_lock import UserOperationLock
from .app_settings import AppSettings
from .app_store import AccountDeleting, AppStore, MomentInFlight, NotFound
from .config import Config
from .dossier import Dossier
from .engine import initiate
from .initiative import intent_for_material, pick_intent
from .memory import Memory, thread_key
from .moment import Moment
from .proactive_materials import (
    ProactiveMaterial,
    dossier_materials,
    select_material,
)

log = logging.getLogger("murmur.app_push")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class PushResponse(Protocol):
    status_code: int
    text: str

    def json(self) -> dict: ...


class PushTransport(Protocol):
    def post(self, url: str, *, headers: dict[str, str], content: bytes) -> PushResponse: ...


class HttpxPushTransport:
    """Shared by APNs and FCM: both want HTTP/2 and neither needs a session."""

    def __init__(self, timeout: float = 15.0):
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("push delivery requires httpx[http2]>=0.27") from exc
        self.client = httpx.Client(http2=True, timeout=timeout)

    def post(self, url: str, *, headers: dict[str, str], content: bytes):
        return self.client.post(url, headers=headers, content=content)


@dataclass(frozen=True)
class PushResult:
    delivered: bool
    status: int
    reason: str | None = None
    # Whether the token itself is finished, as opposed to the attempt.  Each
    # provider renders this verdict in its own vocabulary -- APNs says 410
    # BadDeviceToken where FCM says 404 UNREGISTERED -- so the scheduler must
    # not try to read status codes it cannot know the dialect of.
    permanent: bool = False


class PushProvider(Protocol):
    """One platform's notification transport.  The scheduler routes on this."""

    platform: str

    def send(self, device_token: str, *, moment_id: str, preview: str) -> PushResult: ...


@dataclass(frozen=True)
class GeneratedProactive:
    bubbles: list[str]
    scene: str
    moment: Moment
    intent: str
    material: ProactiveMaterial | None = None


class OpenLoopFollowupClaimed(RuntimeError):
    """Another generator atomically won the one allowed follow-up."""


def _open_loop_id(material_id: str) -> int | None:
    if not material_id.startswith("open_loop:"):
        return None
    try:
        return int(material_id.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        log.error("invalid open-loop material id")
        return None


class APNsProvider:
    """Token-authenticated APNs HTTP/2 provider with a 50-minute JWT cache."""

    platform = "ios"

    def __init__(
        self,
        *,
        key_path: str | Path,
        key_id: str,
        team_id: str,
        topic: str,
        environment: str,
        transport: PushTransport | None = None,
        on_invalid_token: Callable[[str], None] | None = None,
    ):
        if environment not in {"development", "production"}:
            raise ValueError("invalid APNs environment")
        self.key_id, self.team_id, self.topic = key_id, team_id, topic
        self.environment = environment
        self.transport = transport or HttpxPushTransport()
        self.on_invalid_token = on_invalid_token
        try:
            key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
        except (OSError, ValueError) as exc:
            raise RuntimeError("could not load APNs signing key") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise RuntimeError("APNs signing key must be P-256")
        self._key = key
        self._cached_token: str | None = None
        self._token_issued_at = 0

    @classmethod
    def from_settings(
        cls, settings: AppSettings, *, transport: PushTransport | None = None,
        on_invalid_token: Callable[[str], None] | None = None,
    ) -> APNsProvider:
        settings.validate_apns()
        return cls(
            key_path=settings.apns_key_path,
            key_id=settings.apns_key_id or "",
            team_id=settings.apns_team_id or "",
            topic=settings.apns_topic,
            environment=settings.apns_environment,
            transport=transport,
            on_invalid_token=on_invalid_token,
        )

    def _provider_token(self, now: int | None = None) -> str:
        now = int(time.time()) if now is None else int(now)
        if self._cached_token and now - self._token_issued_at < 50 * 60:
            return self._cached_token
        # ExpiredProviderToken can arrive at a second boundary.  A refreshed JWT
        # must have a newer iat instead of reproducing the rejected token.
        now = max(now, self._token_issued_at + 1)
        header = _b64url(json.dumps(
            {"alg": "ES256", "kid": self.key_id}, separators=(",", ":")
        ).encode())
        claims = _b64url(json.dumps(
            {"iss": self.team_id, "iat": now}, separators=(",", ":")
        ).encode())
        signing_input = f"{header}.{claims}".encode("ascii")
        der = self._key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        self._cached_token = f"{header}.{claims}.{signature}"
        self._token_issued_at = now
        return self._cached_token

    @staticmethod
    def payload(moment_id: str, preview: str) -> bytes:
        # Do not put the full conversation in a push.  The app fetches it through
        # an asserted request; this text is merely the notification preview.
        text = preview.strip()[:180]
        while True:
            body = json.dumps({
                "aps": {"alert": {"body": text}, "sound": "default"},
                "moment_id": moment_id,
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) <= 4096:
                return body
            text = text[:-8]

    def send(self, device_token: str, *, moment_id: str, preview: str) -> PushResult:
        host = (
            "https://api.push.apple.com" if self.environment == "production"
            else "https://api.sandbox.push.apple.com"
        )
        for attempt in range(2):
            response = self.transport.post(
                f"{host}/3/device/{device_token}",
                headers={
                    "authorization": f"bearer {self._provider_token()}",
                    "apns-topic": self.topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-collapse-id": moment_id[:64],
                },
                content=self.payload(moment_id, preview),
            )
            if response.status_code == 200:
                return PushResult(True, 200)
            try:
                reason = str(response.json().get("reason") or "APNsError")
            except Exception:
                reason = "APNsError"
            if reason == "ExpiredProviderToken" and attempt == 0:
                self._cached_token = None
                continue
            dead = response.status_code in {400, 410} or reason in {
                "BadDeviceToken", "Unregistered",
            }
            if response.status_code == 410 or reason in {"BadDeviceToken", "Unregistered"}:
                if self.on_invalid_token:
                    self.on_invalid_token(device_token)
            return PushResult(False, response.status_code, reason, permanent=dead)
        return PushResult(False, 403, "ExpiredProviderToken")


def _parse_clock(value: str) -> tuple[int, int]:
    hour, minute = (int(x) for x in value.split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("invalid clock")
    return hour, minute


def plan_proactive_day(
    user_id: str,
    local_day: date,
    timezone: ZoneInfo,
    count: int = 3,
    start: str = "08:30",
    end: str = "22:30",
) -> list[datetime]:
    """Evenly cover the allowed window with deterministic per-day jitter."""
    if count not in {0, 2, 3, 4}:
        raise ValueError("count must be 0, 2, 3, or 4")
    if count == 0:
        return []
    sh, sm = _parse_clock(start)
    eh, em = _parse_clock(end)
    begin = datetime.combine(local_day, day_time(sh, sm), timezone)
    finish = datetime.combine(local_day, day_time(eh, em), timezone)
    if finish <= begin:
        raise ValueError("proactive window must not cross midnight")
    span = (finish - begin).total_seconds()
    segment = span / count
    seed = int.from_bytes(hashlib.sha256(
        f"{user_id}:{local_day.isoformat()}".encode()
    ).digest()[:8], "big")
    rng = random.Random(seed)
    return [
        begin + timedelta(seconds=segment * i + segment * rng.uniform(0.25, 0.75))
        for i in range(count)
    ]


class EngineProactiveGenerator:
    def __init__(
        self,
        cfg: Config,
        *,
        data_root: Path | None = None,
        memory_db_path: Path | None = None,
    ):
        self.cfg = cfg
        self.data_root = data_root or cfg.db_path.parent
        self.memory_db_path = memory_db_path or cfg.db_path

    def __call__(self, user_id: str, tz: ZoneInfo | None = None) -> GeneratedProactive:
        chat_id, label = thread_key("app", "direct", user_id)
        with Memory(self.memory_db_path) as memory:
            dossier = Dossier.load(self.data_root / "dossiers", label)
            # 此刻要按用户设备的时区算：slot 是按它的本地时间排的。
            moment = Moment.text_only(tz or self.cfg.tz)
            recent_intents = [
                entry.intent for entry in memory.recent_outbound(chat_id, limit=8)
            ]
            material: ProactiveMaterial | None = None
            open_loop_id: int | None = None
            if self.cfg.open_loops:
                memory.expire_open_loops(chat_id, moment.at)
                if due := memory.due_open_loops(chat_id, moment.at, limit=1):
                    open_loop_id = due[0].id
                    material = ProactiveMaterial(
                        f"open_loop:{due[0].id}", "open_loop", due[0].title
                    )
                elif memory.has_active_open_loop_claim(chat_id, moment.at):
                    # A worker may have died after reserving this follow-up.
                    # Keep the slot retryable until its lease can be recovered.
                    raise OpenLoopFollowupClaimed("active lease")
            if material is None and self.cfg.proactive_materials:
                candidates = dossier_materials(dossier.blocks)
                for candidate in candidates:
                    memory.upsert_proactive_material(
                        chat_id,
                        candidate.material_id,
                        candidate.category,
                        candidate.source_ref,
                        moment.at,
                    )
                material = select_material(
                    candidates,
                    memory.material_cooldowns(
                        chat_id, [candidate.material_id for candidate in candidates]
                    ),
                    moment.at,
                )
            intent = (
                intent_for_material(material.category, material.source_ref)
                if material is not None
                else pick_intent(recent_intents, material_available=False)
            )
            reply = initiate(
                moment, memory, self.cfg, intent, chat_id=chat_id,
                dossier=None if dossier.is_empty else dossier.as_prompt(),
            )
            # Claim only after a usable reply exists: model failures remain
            # retryable, while concurrent workers cannot both deliver the loop.
            if (
                open_loop_id is not None
                and reply.say
                and not memory.claim_open_loop_followup(open_loop_id, moment.at)
            ):
                raise OpenLoopFollowupClaimed(open_loop_id)
            # Model work is deliberately outside the per-user deletion lock.
            # Memory is committed later, together with the App moment check.
            return GeneratedProactive(
                reply.say,
                reply.scene,
                moment,
                intent.key,
                material,
            )

    def record(self, user_id: str, generated: GeneratedProactive) -> int | None:
        if not generated.bubbles:
            return None
        chat_id, label = thread_key("app", "direct", user_id)
        material = generated.material
        with Memory(self.memory_db_path) as memory:
            return memory.record(
                chat_id=chat_id, thread=label, shot_at=None,
                bucket=generated.moment.bucket, weekday=generated.moment.weekday,
                spot=None, scene=generated.scene, move="speak",
                said=" ⏎ ".join(generated.bubbles), note=None, kind="out",
                intent=generated.intent, has_photo=False,
                material_id=material.material_id if material else None,
                delivery_state="pending",
            )

    def finalize(
        self, user_id: str, generated: GeneratedProactive, entry_id: int | None
    ) -> None:
        """Commit cooldown bookkeeping after the App moment is durable."""
        chat_id, _ = thread_key("app", "direct", user_id)
        with Memory(self.memory_db_path) as memory:
            if entry_id is None or not memory.commit_outbound_entry(entry_id, chat_id):
                raise RuntimeError("proactive memory entry missing")
            material = generated.material
            if material is None:
                return
            if material.category == "open_loop":
                loop_id = _open_loop_id(material.material_id)
                if loop_id is not None:
                    memory.commit_open_loop_followup(loop_id)
            memory.upsert_proactive_material(
                chat_id,
                material.material_id,
                material.category,
                material.source_ref,
                generated.moment.at,
            )
            memory.mark_proactive_material_used(
                chat_id, material.material_id, generated.moment.at
            )

    def rollback(
        self,
        user_id: str,
        generated: GeneratedProactive,
        entry_id: int | None = None,
    ) -> None:
        """Make a failed pre-delivery attempt eligible for a clean retry."""
        chat_id, _ = thread_key("app", "direct", user_id)
        material = generated.material
        with Memory(self.memory_db_path) as memory:
            if entry_id is not None:
                memory.discard_outbound_entry(entry_id, chat_id)
            if material is None or material.category != "open_loop":
                return
            if (loop_id := _open_loop_id(material.material_id)) is not None:
                memory.release_open_loop_followup(loop_id, generated.moment.at)

    def sweep_orphans(self, user_id: str, keep_entry_ids: set[int]) -> int:
        """Discard pending rows that no durable App moment references.

        ProactiveScheduler calls this while holding the same per-user lock as
        record -> create_proactive, closing the otherwise unavoidable crash
        window between the two SQLite databases.
        """
        chat_id, _ = thread_key("app", "direct", user_id)
        with Memory(self.memory_db_path) as memory:
            return memory.discard_unlinked_outbound_entries(
                chat_id, keep_entry_ids
            )

    def reconcile(self, user_id: str, entry_id: int) -> None:
        """Finish bookkeeping after a crash that left an App moment durable."""
        chat_id, _ = thread_key("app", "direct", user_id)
        with Memory(self.memory_db_path) as memory:
            delivery = memory.outbound_entry_delivery(entry_id, chat_id)
            if delivery is None:
                raise RuntimeError("proactive memory entry missing")
            material_id, _delivery_state = delivery
            if not memory.commit_outbound_entry(entry_id, chat_id):
                raise RuntimeError("proactive memory entry missing")
            if material_id is None:
                return
            if (loop_id := _open_loop_id(material_id)) is not None:
                memory.commit_open_loop_followup(loop_id)
            else:
                memory.mark_proactive_material_used(
                    chat_id, material_id, datetime.now(UTC)
                )


class ProactiveScheduler:
    # 生成失败时的按用户指数退避：模型网关故障时，不能让每个到期 slot
    # 每 30 秒重打一次完整的模型调用直到当天结束。
    GEN_RETRY_BASE_SECONDS = 60.0
    GEN_RETRY_MAX_SECONDS = 30 * 60.0

    def __init__(
        self,
        store: AppStore,
        providers: Mapping[str, PushProvider] | PushProvider,
        generator: Callable[[str, ZoneInfo], tuple[list[str], str] | tuple[list[str], str, int | None]],
        *,
        lock_root: Path | None = None,
        delivery_budget_seconds: float = 30.0,
    ):
        if not isinstance(providers, Mapping):
            providers = {getattr(providers, "platform", "ios"): providers}
        self.store, self.providers, self.generator = store, dict(providers), generator
        self.lock_root = lock_root
        self.delivery_budget_seconds = delivery_budget_seconds
        self._gen_failures: dict[str, int] = {}
        self._gen_retry_after: dict[str, float] = {}

    @staticmethod
    def _timezone(name: str | None) -> ZoneInfo:
        try:
            return ZoneInfo(name or "Asia/Shanghai")
        except ZoneInfoNotFoundError:
            return ZoneInfo("Asia/Shanghai")

    def ensure_schedules(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for user in self.store.proactive_users():
            timezone = self._timezone(user["timezone"])
            local_day = now.astimezone(timezone).date()
            if self.store.has_slots(user["id"], local_day):
                continue
            slots = plan_proactive_day(
                user["id"], local_day, timezone, int(user["daily_frequency"]),
                # Public preferences describe the quiet interval, which crosses
                # midnight: 22:30 -> 08:30.  Scheduling uses its complement.
                user["quiet_end"], user["quiet_start"],
            )
            self.store.replace_slots(user["id"], local_day, slots)

    def _user_lock(self, user_id: str):
        return (UserOperationLock(self.lock_root, user_id)
                if self.lock_root is not None else nullcontext())

    def _rollback_generated(
        self, user_id: str, generated, entry_id: int | None = None
    ) -> None:
        if not isinstance(generated, GeneratedProactive):
            return
        rollback = getattr(self.generator, "rollback", None)
        if not callable(rollback):
            return
        try:
            rollback(user_id, generated, entry_id)
        except Exception as error:
            log.error(
                "could not roll back proactive generation user_id=%s error_type=%s",
                user_id, type(error).__name__,
            )

    def _finalize_generated(
        self, user_id: str, generated, entry_id: int | None
    ) -> None:
        if not isinstance(generated, GeneratedProactive):
            return
        finalize = getattr(self.generator, "finalize", None)
        if not callable(finalize):
            return
        finalize(user_id, generated, entry_id)

    def _recover_pending_memory(self, user_id: str | None = None) -> None:
        try:
            pending = self.store.pending_proactive_memory_finalizations(user_id)
        except Exception as error:
            log.error(
                "could not scan proactive memory outbox error_type=%s",
                type(error).__name__,
            )
            return
        reconcile = getattr(self.generator, "reconcile", None)
        for item in pending:
            try:
                if callable(reconcile):
                    reconcile(item["user_id"], int(item["memory_entry_id"]))
                self.store.finish_proactive_memory_finalization(
                    item["moment_id"], item["user_id"]
                )
            except (AccountDeleting, NotFound):
                continue
            except Exception as error:
                log.error(
                    "could not reconcile proactive memory user_id=%s error_type=%s",
                    item["user_id"], type(error).__name__,
                )

    def _sweep_orphan_memory(self) -> None:
        """Clean pre-App crash rows using AppStore as the durable authority."""
        sweep = getattr(self.generator, "sweep_orphans", None)
        if not callable(sweep):
            return
        try:
            users = self.store.active_users()
        except Exception as error:
            log.error(
                "could not scan proactive memory users error_type=%s",
                type(error).__name__,
            )
            return
        for user in users:
            user_id = str(user["id"])
            try:
                with self._user_lock(user_id):
                    self.store.require_user_ready(user_id)
                    pending = self.store.pending_proactive_memory_finalizations(
                        user_id
                    )
                    keep = {int(item["memory_entry_id"]) for item in pending}
                    sweep(user_id, keep)
            except (AccountDeleting, NotFound):
                continue
            except Exception as error:
                log.error(
                    "could not sweep proactive memory user_id=%s error_type=%s",
                    user_id, type(error).__name__,
                )

    def deliver_pending(self, now: datetime | None = None) -> int:
        """Replay durable per-device APNs work, including after a restart."""
        now = now or datetime.now(UTC)
        if not self.providers:
            # Without any push provider (development installs) proactive
            # moments are picked up by in-app polling; pending rows age out
            # through expire_stale_proactive instead of retrying forever.
            return 0
        delivered = 0
        # A sick APNs/FCM must not starve the worker's moment processing: one
        # round spends at most delivery_budget_seconds on sends.  Whatever is
        # left keeps its past-due next_attempt_at, so due_push_deliveries
        # picks it up again on the next round.
        deadline = time.monotonic() + self.delivery_budget_seconds
        candidates = self.store.due_push_deliveries(now)
        for index, candidate in enumerate(candidates):
            if index and time.monotonic() >= deadline:
                log.warning(
                    "push delivery budget exhausted; %d deliveries wait for next round",
                    len(candidates) - index,
                )
                break
            user_id = candidate["user_id"]
            try:
                # Re-check under the same lock as account erasure.  Network work
                # is bounded by the APNs timeout; slow model generation is not in
                # this critical section.
                with self._user_lock(user_id):
                    self.store.require_user_ready(user_id)
                    item = self.store.pending_push_delivery(
                        candidate["moment_id"], candidate["device_id"], now
                    )
                    if item is None:
                        continue
                    provider = self.providers.get(item["platform"])
                    if provider is None:
                        # A platform with no configured provider is an operator
                        # problem, not a dead device: back off and let the 24h
                        # proactive expiry retire it if the gap is never closed.
                        log.warning(
                            "no push provider configured platform=%s", item["platform"]
                        )
                        self.store.retry_push(
                            item["moment_id"], item["device_id"], now=now
                        )
                        continue
                    try:
                        result = provider.send(
                            item["push_token"], moment_id=item["moment_id"],
                            preview=item["push_preview"] or "Murmur",
                        )
                    except Exception as error:
                        # HTTP exceptions may contain the token-bearing request URL.
                        log.error(
                            "APNs transport failed error_type=%s",
                            type(error).__name__,
                        )
                        self.store.retry_push(
                            item["moment_id"], item["device_id"], now=now
                        )
                        continue
                    if result.delivered:
                        self.store.mark_push_sent(
                            item["moment_id"], item["device_id"]
                        )
                        delivered += 1
                    elif result.permanent:
                        self.store.mark_push_dead(
                            item["moment_id"], item["device_id"],
                            status=result.status,
                        )
                    else:
                        self.store.retry_push(
                            item["moment_id"], item["device_id"],
                            status=result.status, now=now,
                        )
                        log.warning(
                            "push delivery deferred platform=%s status=%s",
                            item["platform"], result.status,
                        )
            except (AccountDeleting, NotFound):
                continue
        return delivered

    def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        # Every other step of run_once takes the injected clock; expiry reading
        # the wall clock instead made fixed-date tests rot after 24 hours.
        self.store.expire_stale_proactive(now=now)
        self.deliver_pending(now)
        # This durable outbox is independent of ACK, 24h expiry, preferences,
        # and slots (which a stop preference may delete altogether).
        self._recover_pending_memory()
        self._sweep_orphan_memory()
        self.ensure_schedules(now)
        created = 0
        timezones = {u["id"]: u["timezone"] for u in self.store.proactive_users()}
        for slot in self.store.due_slots(now):
            user_id = slot["user_id"]
            generated = None
            entry_id: int | None = None
            proactive_created = False
            if time.monotonic() < self._gen_retry_after.get(user_id, 0.0):
                continue
            # Advisory check prevents needless model calls in the common case.
            if self.store.current_proactive(user_id):
                self._recover_pending_memory(user_id)
                self.store.mark_slot_delivered(user_id, slot["slot_at"])
                continue
            try:
                generated = self.generator(user_id, self._timezone(timezones.get(user_id)))
                if isinstance(generated, GeneratedProactive):
                    bubbles, scene = generated.bubbles, generated.scene
                else:
                    bubbles, scene = generated[:2]
                with self._user_lock(user_id):
                    self.store.require_user_ready(user_id)
                    # A second worker may have won while the model was running.
                    if self.store.current_proactive(user_id):
                        self._rollback_generated(user_id, generated)
                        self._recover_pending_memory(user_id)
                        self.store.mark_slot_delivered(user_id, slot["slot_at"])
                        continue
                    if not self.store.push_devices(user_id):
                        self._rollback_generated(user_id, generated)
                        self.store.mark_slot_delivered(user_id, slot["slot_at"])
                        continue
                    if not bubbles:
                        self._rollback_generated(user_id, generated)
                        self.store.mark_slot_delivered(user_id, slot["slot_at"])
                        continue
                    if isinstance(generated, GeneratedProactive):
                        entry_id = self.generator.record(user_id, generated)
                    else:
                        entry_id = generated[2] if len(generated) > 2 else None
                    proactive_id = self.store.create_proactive(
                        user_id, bubbles, scene=scene, memory_entry_id=entry_id, now=now
                    )
                    proactive_created = True
                    self._finalize_generated(user_id, generated, entry_id)
                    self.store.finish_proactive_memory_finalization(
                        proactive_id, user_id
                    )
                    self._gen_failures.pop(user_id, None)
                    self._gen_retry_after.pop(user_id, None)
                    created += 1
            except (AccountDeleting, NotFound):
                # Account erasure owns the per-user lock and cascades its slots.
                continue
            except MomentInFlight:
                self._rollback_generated(user_id, generated, entry_id)
                self._recover_pending_memory(user_id)
                self.store.mark_slot_delivered(user_id, slot["slot_at"])
                continue
            except Exception as error:
                if not proactive_created:
                    self._rollback_generated(user_id, generated, entry_id)
                failures = self._gen_failures.get(user_id, 0) + 1
                self._gen_failures[user_id] = failures
                delay = min(
                    self.GEN_RETRY_BASE_SECONDS * 2 ** (failures - 1),
                    self.GEN_RETRY_MAX_SECONDS,
                )
                self._gen_retry_after[user_id] = time.monotonic() + delay
                log.error(
                    "could not create proactive moment user_id=%s error_type=%s "
                    "(failures=%d, retry in %.0fs)",
                    user_id, type(error).__name__, failures, delay,
                )
                continue
        self.deliver_pending(now)
        return created
