"""APNs delivery and the restrained first-party proactive schedule."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import time
from collections.abc import Callable
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
from .initiative import pick_intent
from .memory import Memory, thread_key
from .moment import Moment

log = logging.getLogger("murmur.app_push")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class APNsResponse(Protocol):
    status_code: int
    text: str

    def json(self) -> dict: ...


class APNsTransport(Protocol):
    def post(self, url: str, *, headers: dict[str, str], content: bytes) -> APNsResponse: ...


class HttpxAPNsTransport:
    def __init__(self, timeout: float = 15.0):
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("APNs requires httpx[http2]>=0.27") from exc
        self.client = httpx.Client(http2=True, timeout=timeout)

    def post(self, url: str, *, headers: dict[str, str], content: bytes):
        return self.client.post(url, headers=headers, content=content)


@dataclass(frozen=True)
class PushResult:
    delivered: bool
    status: int
    reason: str | None = None


@dataclass(frozen=True)
class GeneratedProactive:
    bubbles: list[str]
    scene: str
    moment: Moment
    intent: str


class APNsProvider:
    """Token-authenticated APNs HTTP/2 provider with a 50-minute JWT cache."""

    def __init__(
        self,
        *,
        key_path: str | Path,
        key_id: str,
        team_id: str,
        topic: str,
        environment: str,
        transport: APNsTransport | None = None,
        on_invalid_token: Callable[[str], None] | None = None,
    ):
        if environment not in {"development", "production"}:
            raise ValueError("invalid APNs environment")
        self.key_id, self.team_id, self.topic = key_id, team_id, topic
        self.environment = environment
        self.transport = transport or HttpxAPNsTransport()
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
        cls, settings: AppSettings, *, transport: APNsTransport | None = None,
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
            if response.status_code == 410 or reason in {"BadDeviceToken", "Unregistered"}:
                if self.on_invalid_token:
                    self.on_invalid_token(device_token)
            return PushResult(False, response.status_code, reason)
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

    def __call__(self, user_id: str) -> GeneratedProactive:
        chat_id, label = thread_key("app", "direct", user_id)
        with Memory(self.memory_db_path) as memory:
            intent = pick_intent([e.intent for e in memory.recent_outbound(chat_id, limit=8)])
            dossier = Dossier.load(self.data_root / "dossiers", label)
            moment = Moment.text_only(self.cfg.tz)
            reply = initiate(
                moment, memory, self.cfg, intent, chat_id=chat_id,
                dossier=None if dossier.is_empty else dossier.as_prompt(),
            )
            # Model work is deliberately outside the per-user deletion lock.
            # Memory is committed later, together with the App moment check.
            return GeneratedProactive(reply.say, reply.scene, moment, intent.key)

    def record(self, user_id: str, generated: GeneratedProactive) -> int | None:
        if not generated.bubbles:
            return None
        chat_id, label = thread_key("app", "direct", user_id)
        with Memory(self.memory_db_path) as memory:
            return memory.record(
                chat_id=chat_id, thread=label, shot_at=None,
                bucket=generated.moment.bucket, weekday=generated.moment.weekday,
                spot=None, scene=generated.scene, move="speak",
                said=" ⏎ ".join(generated.bubbles), note=None, kind="out",
                intent=generated.intent, has_photo=False,
            )


class ProactiveScheduler:
    def __init__(
        self,
        store: AppStore,
        provider: APNsProvider,
        generator: Callable[[str], tuple[list[str], str] | tuple[list[str], str, int | None]],
        *,
        lock_root: Path | None = None,
    ):
        self.store, self.provider, self.generator = store, provider, generator
        self.lock_root = lock_root

    def ensure_schedules(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for user in self.store.proactive_users():
            try:
                timezone = ZoneInfo(user["timezone"])
            except ZoneInfoNotFoundError:
                timezone = ZoneInfo("Asia/Shanghai")
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

    def deliver_pending(self, now: datetime | None = None) -> int:
        """Replay durable per-device APNs work, including after a restart."""
        now = now or datetime.now(UTC)
        delivered = 0
        for candidate in self.store.due_push_deliveries(now):
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
                    try:
                        result = self.provider.send(
                            item["apns_token"], moment_id=item["moment_id"],
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
                    elif result.status in {400, 410} or result.reason in {
                        "BadDeviceToken", "Unregistered",
                    }:
                        self.store.mark_push_dead(
                            item["moment_id"], item["device_id"],
                            status=result.status,
                        )
                    else:
                        self.store.retry_push(
                            item["moment_id"], item["device_id"],
                            status=result.status, now=now,
                        )
                        log.warning("APNs delivery deferred status=%s", result.status)
            except (AccountDeleting, NotFound):
                continue
        return delivered

    def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        self.store.expire_stale_proactive()
        self.deliver_pending(now)
        self.ensure_schedules(now)
        created = 0
        for slot in self.store.due_slots(now):
            user_id = slot["user_id"]
            # Advisory check prevents needless model calls in the common case.
            if self.store.current_proactive(user_id):
                self.store.mark_slot_delivered(user_id, slot["slot_at"])
                continue
            try:
                generated = self.generator(user_id)
                if isinstance(generated, GeneratedProactive):
                    bubbles, scene = generated.bubbles, generated.scene
                else:
                    bubbles, scene = generated[:2]
                with self._user_lock(user_id):
                    self.store.require_user_ready(user_id)
                    # A second worker may have won while the model was running.
                    if self.store.current_proactive(user_id):
                        self.store.mark_slot_delivered(user_id, slot["slot_at"])
                        continue
                    if not self.store.push_devices(user_id):
                        self.store.mark_slot_delivered(user_id, slot["slot_at"])
                        continue
                    if not bubbles:
                        self.store.mark_slot_delivered(user_id, slot["slot_at"])
                        continue
                    if isinstance(generated, GeneratedProactive):
                        entry_id = self.generator.record(user_id, generated)
                    else:
                        entry_id = generated[2] if len(generated) > 2 else None
                    self.store.create_proactive(
                        user_id, bubbles, scene=scene, memory_entry_id=entry_id, now=now
                    )
                    self.store.mark_slot_delivered(user_id, slot["slot_at"])
                    created += 1
            except (AccountDeleting, NotFound):
                # Account erasure owns the per-user lock and cascades its slots.
                continue
            except MomentInFlight:
                self.store.mark_slot_delivered(user_id, slot["slot_at"])
                continue
            except Exception as error:
                log.error(
                    "could not create proactive moment user_id=%s error_type=%s",
                    user_id, type(error).__name__,
                )
                continue
        self.deliver_pending(now)
        return created
