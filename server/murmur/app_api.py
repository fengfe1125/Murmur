"""Shared HTTPS/SSE boundary for the first-party Murmur mobile clients.

FastAPI and multipart imports are intentionally delayed so the existing bot
commands remain importable independently. Authentication, uploads and event
contracts stay here; journal drafting and client-side history are not this API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .app_auth import (
    AppAttestUnsupported,
    AppAuthenticator,
    AppAuthError,
    AttestationRequired,
    InvalidAttestation,
)
from .app_listen_together import (
    ROOM_COMMANDS,
    ListenTogetherError,
    RoomIPCClient,
)
from .app_lock import UserOperationLock
from .app_music import (
    MAX_MUSIC_SEARCH_CANDIDATES,
    MAX_MUSIC_SEARCH_RESULTS,
    MusicCatalog,
    MusicCatalogUnavailable,
    MusicError,
    MusicTrackInvalid,
    MusicTrackUnavailable,
    NeteaseCatalogAdapter,
    normalize_music_track,
    normalize_playback_track,
    parse_music_track,
    rank_music_search_results,
)
from .app_music_links import MAX_SHARED_TEXT_BYTES, MusicLink, MusicLinkRejected
from .app_settings import AppSettings, music_user_allowed
from .app_store import (
    MOMENT_INTENTS,
    AccountDeleting,
    AppStore,
    AppStoreError,
    ChallengeExpired,
    ChallengeInvalid,
    Conflict,
    DeviceLimit,
    IdempotencyConflict,
    InviteInvalid,
    LastDevice,
    MomentInFlight,
    NotFound,
)
from .config import Config
from .dossier import _safe
from .initiative import wants_stop
from .memory import Memory, thread_key

log = logging.getLogger("murmur.app_api")

PLATFORMS = ("ios", "android")

# APNs hands out a hex device token; FCM hands out a much longer opaque string
# built from the URL-safe base64 alphabet with a ':' separating its two halves.
PUSH_TOKEN_RULES = {
    "ios": (re.compile(r"[0-9A-Fa-f]+"), 32, 256),
    "android": (re.compile(r"[A-Za-z0-9_:.\-]+"), 64, 512),
}


def _valid_push_token(token, platform: str) -> bool:
    rule = PUSH_TOKEN_RULES.get(platform)
    if rule is None or not isinstance(token, str):
        return False
    pattern, low, high = rule
    return low <= len(token) <= high and pattern.fullmatch(token) is not None


class APIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable = retryable


class RateLimiter:
    """Small in-process guard; the reverse proxy remains the outer rate limit."""

    def __init__(self, limit: int, window_seconds: int = 60, max_keys: int = 4096):
        self.limit, self.window, self.max_keys = limit, window_seconds, max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    for stale_key in list(self._hits):
                        stale_hits = self._hits[stale_key]
                        while stale_hits and stale_hits[0] <= now - self.window:
                            stale_hits.popleft()
                        if not stale_hits:
                            del self._hits[stale_key]
                while len(self._hits) >= self.max_keys:
                    self._hits.popitem(last=False)
                hits = deque()
                self._hits[key] = hits
            else:
                self._hits.move_to_end(key)
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise APIError(429, "rate_limited", "请求太频繁，请稍后再试。", retryable=True)
            hits.append(now)

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._hits)


def _error_payload(error: APIError, request_id: str | None = None) -> dict:
    body = {
        "code": error.code,
        "message": error.message,
        "retryable": bool(error.retryable),
    }
    if request_id:
        body["request_id"] = request_id
    return {"error": body}


def _translate_error(exc: Exception) -> APIError:
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, (ChallengeExpired,)):
        return APIError(401, "challenge_expired", "验证挑战已过期，请重新获取。")
    if isinstance(exc, (ChallengeInvalid,)):
        return APIError(401, "invalid_challenge", "验证挑战无效或已使用。")
    if isinstance(exc, AppAttestUnsupported):
        return APIError(403, exc.code, "这台设备不支持安全注册。")
    if isinstance(exc, AttestationRequired):
        return APIError(401, exc.code, "需要有效的 App Attest 验证。")
    if isinstance(exc, (AppAuthError, InvalidAttestation)):
        return APIError(401, getattr(exc, "code", "invalid_assertion"), "App 验证失败。")
    if isinstance(exc, InviteInvalid):
        return APIError(403, exc.code, "邀请码无效、过期或已经使用。")
    if isinstance(exc, DeviceLimit):
        return APIError(409, exc.code, "最多只能绑定三台设备。")
    if isinstance(exc, LastDevice):
        return APIError(409, exc.code, "不能移除最后一台设备；请改为删除账号。")
    if isinstance(exc, MusicTrackInvalid):
        return APIError(400, exc.code, "这首歌的信息无效。")
    if isinstance(exc, MusicError):
        # Catalog lookups and planning are best-effort dependencies; the client
        # is told to try again rather than being handed a 500.
        return APIError(
            503 if exc.retryable else 400, exc.code,
            "音乐服务暂时不可用。" if exc.retryable else "这首歌暂时不可用。",
            retryable=exc.retryable,
        )
    if isinstance(exc, IdempotencyConflict):
        return APIError(409, exc.code, "这个幂等键已经用于其他内容。")
    if isinstance(exc, MomentInFlight):
        return APIError(409, exc.code, "上一条还在处理中。", retryable=True)
    if isinstance(exc, AccountDeleting):
        return APIError(409, exc.code, "账号正在删除，请稍后重试删除操作。", retryable=True)
    if isinstance(exc, Conflict):
        return APIError(409, exc.code, "请求与当前状态冲突。")
    if isinstance(exc, NotFound):
        return APIError(404, exc.code, "没有找到对应内容。")
    if isinstance(exc, ValueError):
        return APIError(400, "validation_error", str(exc))
    if isinstance(exc, AppStoreError):
        return APIError(400, exc.code, "请求无法完成。")
    return APIError(500, "internal_error", "Murmur 暂时没有接住。", retryable=True)


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise APIError(400, "validation_error", "请求必须是 JSON。") from exc
    if not isinstance(value, dict):
        raise APIError(400, "validation_error", "JSON 顶层必须是对象。")
    return value


def _required_text(data: dict, name: str, max_length: int = 500) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise APIError(400, "validation_error", f"{name} 无效。")
    return value.strip()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        or len(data) >= 12 and data[4:8] == b"ftyp"  # HEIC/HEIF container
    )


# 地名在这里只做长度上的兜底，内容不动：它是 App 反解出来的一段人读的文字，
# 服务端没有第二个来源可以拿来校验它。
MAX_PLACE_CHARS = 120
_PROVENANCE_KEYS = {"shot_at", "lat", "lon", "place"}


def _parse_provenance(value) -> str | None:
    """照片自己带的事实：拍摄时间、坐标、机上反解出的地名。

    重新编码过的 JPEG 没有 EXIF，所以这些不从图里读，由 App 显式声明。
    存回去的是规范化后的 JSON——幂等摘要要算它，同一份内容必须得到同一串字节。

    四个键全是可选的：老照片可能关了定位，反解可能超时，都不是错误。
    """
    if value in (None, ""):
        return None
    try:
        raw = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise APIError(400, "validation_error", "provenance 无效。") from exc
    if not isinstance(raw, dict) or not _PROVENANCE_KEYS.issuperset(raw):
        raise APIError(400, "validation_error", "provenance 无效。")

    clean: dict[str, object] = {}

    shot_at = raw.get("shot_at")
    if shot_at is not None:
        if not isinstance(shot_at, str):
            raise APIError(400, "validation_error", "provenance.shot_at 无效。")
        try:
            parsed = datetime.fromisoformat(shot_at)
        except ValueError as exc:
            raise APIError(
                400, "validation_error", "provenance.shot_at 无效。"
            ) from exc
        clean["shot_at"] = parsed.isoformat()

    lat, lon = raw.get("lat"), raw.get("lon")
    if (lat is None) != (lon is None):
        # 半个坐标折不出地点指纹，也没法解释它是什么意思。
        raise APIError(400, "validation_error", "provenance 的经纬度要成对提交。")
    if lat is not None:
        # bool 是 int 的子类，挡掉，否则 True 会被当成 1.0 度。
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise APIError(400, "validation_error", "provenance 的经纬度无效。")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            raise APIError(400, "validation_error", "provenance 的经纬度无效。")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise APIError(400, "validation_error", "provenance 的经纬度无效。")
        clean["lat"], clean["lon"] = float(lat), float(lon)

    place = raw.get("place")
    if place is not None:
        if not isinstance(place, str):
            raise APIError(400, "validation_error", "provenance.place 无效。")
        place = " ".join(place.split())
        if len(place) > MAX_PLACE_CHARS:
            raise APIError(400, "validation_error", "provenance.place 过长。")
        if place:
            clean["place"] = place

    if not clean:
        return None
    return json.dumps(
        {key: clean[key] for key in sorted(clean)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def erase_account(store: AppStore, settings: AppSettings, user_id: str) -> None:
    """Erase app identity, hidden memory, dossier and every retained preview."""
    with UserOperationLock(settings.data_root, user_id):
        chat_id, label = thread_key("app", "direct", user_id)
        artefacts = store.begin_user_erasure(user_id)
        ids = artefacts["memory_entry_ids"]
        with Memory(settings.memory_db_path) as memory:
            # Include records made by proactive generation even if an early version
            # did not link their entry id back to app_moments.
            rows = memory.conn.execute(
                "SELECT id FROM entries WHERE chat_id=?", (chat_id,)
            ).fetchall()
            ids = sorted(set(ids) | {int(row["id"]) for row in rows})
            memory.conn.execute("DELETE FROM entries WHERE chat_id=?", (chat_id,))
            try:
                memory.conn.execute(
                    "DELETE FROM app_memory_links WHERE user_id=?", (user_id,)
                )
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
            memory.conn.execute(
                "DELETE FROM roster WHERE platform='app' AND user_id=?", (user_id,)
            )
            memory.conn.execute("DELETE FROM greeted WHERE chat_id=?", (chat_id,))
            memory.conn.execute("DELETE FROM optouts WHERE chat_id=?", (chat_id,))
            memory.conn.execute("DELETE FROM open_loops WHERE chat_id=?", (chat_id,))
            memory.conn.execute(
                "DELETE FROM proactive_materials WHERE chat_id=?", (chat_id,)
            )
            memory.conn.execute("DELETE FROM affect_states WHERE chat_id=?", (chat_id,))
            memory.conn.commit()
        paths = [Path(value) for value in artefacts["paths"]]
        paths.extend(settings.data_root / "photos" / f"{entry_id}.jpg" for entry_id in ids)
        paths.append(settings.data_root / "dossiers" / f"{_safe(label)}.md")
        allowed_roots = {
            settings.data_root.resolve(strict=False), settings.upload_dir.resolve(strict=False)
        }
        for path in paths:
            resolved = path.resolve(strict=False)
            if not any(resolved.is_relative_to(root) for root in allowed_roots):
                raise RuntimeError("account artefact escaped the configured data root")
            resolved.unlink(missing_ok=True)
        # Authentication is destroyed only after all retryable external cleanup
        # has succeeded.
        store.complete_user_erasure(user_id)


def apply_stop_preference(store: AppStore, user_id: str, text: str | None) -> bool:
    """Honor an explicit stop command before any model or queue work starts."""
    if not text or not wants_stop(text):
        return False
    prefs = store.preferences(user_id)
    store.update_preferences(
        user_id, daily_frequency=0,
        quiet_start=prefs["quiet_start"], quiet_end=prefs["quiet_end"],
    )
    return True


# One table for both local and worker-reported room failures: the IPC client
# re-raises a typed error carrying the same code, so the API never has to know
# which side of the socket a room refusal came from.
ROOM_ERROR_STATUS = {
    "invalid_room_request": 400,
    "room_not_found": 404,
    "room_conflict": 409,
    "room_idempotency_conflict": 409,
    "room_experiment_disabled": 403,
    "room_protocol_unsupported": 501,
    "room_out_of_order": 503,
    "room_transport_unavailable": 503,
    "room_authentication_failed": 503,
    "room_unreachable": 503,
    "room_ipc_unavailable": 503,
    "room_confirmation_timeout": 504,
}
# Fixed copy per code.  Room errors carry protocol detail that must never reach
# a client, so the message is chosen here rather than taken from the exception.
ROOM_ERROR_MESSAGE = {
    "invalid_room_request": "这个一起听请求不对。",
    "room_not_found": "现在没有正在进行的一起听。",
    "room_conflict": "已经有一个一起听在进行了。",
    "room_idempotency_conflict": "这个请求和之前那个不一样。",
    "room_experiment_disabled": "一起听功能未开启。",
    "room_protocol_unsupported": "一起听功能还没接通。",
    "room_confirmation_timeout": "没等到网易云确认，先别当成已经同步了。",
}
ROOM_ERROR_FALLBACK = "一起听暂时不可用，稍后再试。"


def room_api_error(exc: ListenTogetherError) -> APIError:
    code = getattr(exc, "code", "listen_together_error")
    return APIError(
        ROOM_ERROR_STATUS.get(code, 503),
        code,
        ROOM_ERROR_MESSAGE.get(code, ROOM_ERROR_FALLBACK),
        retryable=bool(getattr(exc, "retryable", False)),
    )


def _music_user_allowed(settings: AppSettings, user_id: str) -> bool:
    return music_user_allowed(settings.music_user_allowlist, user_id)


def audius_music_enabled_for(settings: AppSettings, user_id: str) -> bool:
    """Whether this account may use the existing Audius vertical slice."""
    return settings.music_enabled and _music_user_allowed(settings, user_id)


def netease_catalog_enabled_for(settings: AppSettings, user_id: str) -> bool:
    """Whether this account may search, resolve and share NetEase metadata."""
    return settings.netease_catalog_enabled and _music_user_allowed(settings, user_id)


def netease_room_enabled_for(settings: AppSettings, user_id: str) -> bool:
    """The room experiment is always an explicit, non-empty allowlist."""
    return (
        settings.netease_room_experiment_enabled
        and user_id in settings.netease_room_user_allowlist
    )


def music_provider_enabled_for(
    settings: AppSettings, user_id: str, provider: str
) -> bool:
    """Whether this exact provider is switched on for this account."""
    if provider == "audius":
        return audius_music_enabled_for(settings, user_id)
    if provider == "netease":
        return netease_catalog_enabled_for(settings, user_id)
    return False


def music_enabled_for(settings: AppSettings, user_id: str) -> bool:
    """Whether this account may use any provider on the music wire.

    Two independent gates: the deployment switch, then the rollout allowlist.
    An empty allowlist means "everyone the switch already allows" rather than
    "nobody", so a full rollout does not require enumerating every account.
    """
    return (
        audius_music_enabled_for(settings, user_id)
        or netease_catalog_enabled_for(settings, user_id)
    )


def record_memory_reply(
    settings: AppSettings, cfg: Config, entry_id: int, reply: str
) -> None:
    """Attach an acknowledgement and update optional continuity state."""
    with Memory(settings.memory_db_path) as memory:
        row = memory.conn.execute(
            "SELECT chat_id FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        memory.add_reply(entry_id, reply)
        if row is None:
            return
        chat_id = int(row["chat_id"])
        now = datetime.now(cfg.tz)
        try:
            if cfg.open_loops:
                memory.resolve_open_loops_from_text(chat_id, reply, now)
            if cfg.affect:
                memory.apply_affect_message(
                    chat_id, entry_id * 2 + 1, reply, now
                )
        except Exception as error:
            # The acknowledgement is already durable.  Optional state must not
            # make the client retry an otherwise successful ACK.
            log.warning(
                "App acknowledgement continuity update failed error_type=%s",
                type(error).__name__,
            )


def create_app(
    settings: AppSettings | None = None,
    *,
    cfg: Config | None = None,
    store: AppStore | None = None,
    authenticator: AppAuthenticator | None = None,
    music_link: MusicLink | None = None,
    music_catalog: MusicCatalog | None = None,
    room_client: RoomIPCClient | None = None,
):
    try:
        import python_multipart  # noqa: F401  # required by Request.form()
        from fastapi import FastAPI, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse, Response, StreamingResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException
    except ImportError as exc:
        raise RuntimeError(
            "App API requires fastapi>=0.115, uvicorn>=0.30 and python-multipart>=0.0.9"
        ) from exc

    cfg = cfg or Config.load()
    settings = settings or AppSettings.from_env(cfg)
    settings.validate()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings.upload_dir.chmod(0o700)
    except OSError:
        pass
    owns_store = store is None
    store = store or AppStore(settings.db_path)
    authenticator = authenticator or AppAuthenticator(settings, store)
    limiter = RateLimiter(
        settings.requests_per_minute, max_keys=settings.rate_limit_max_keys
    )
    body_gate = asyncio.Semaphore(settings.max_concurrent_uploads)
    # Both stay None while their switch is off, so a disabled deployment holds
    # no NetEase client and opens no socket.  The catalog here is NetEase-only:
    # resolving a shared link must never fall through to another provider.
    owns_music_link = music_link is None
    # Owned separately from the link: a caller may inject only the catalog (the
    # search route's tests do), and closing something we were handed is not
    # ours to do.
    owns_music_catalog = music_catalog is None
    if music_link is None and settings.netease_catalog_enabled:
        if music_catalog is None:
            music_catalog = MusicCatalog(
                [NeteaseCatalogAdapter(base_url=settings.netease_catalog_base_url)],
                default_provider="netease",
            )
        music_link = MusicLink(music_catalog)
    if room_client is None and settings.netease_room_experiment_enabled:
        room_client = RoomIPCClient(settings.netease_room_socket_path)
    # With postponed annotations the locally imported Request name must also be
    # visible to FastAPI's type-hint resolver; otherwise it becomes a query field.
    globals()["Request"] = Request
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if owns_music_link and music_link is not None:
                music_link.close()
            if owns_music_catalog and music_catalog is not None:
                music_catalog.close()
            if owns_store:
                store.close()

    app = FastAPI(
        title="Murmur App API", version="1", docs_url=None, redoc_url=None,
        openapi_url=None, lifespan=lifespan,
    )
    app.state.store = store
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        ip = request.client.host if request.client else "unknown"
        try:
            # Never trust an unauthenticated key header as the sole rate-limit
            # identity: an attacker could rotate random values indefinitely.
            limiter.check(f"ip:{ip}")
            response = await call_next(request)
        except Exception as exc:
            error = _translate_error(exc)
            if error.status >= 500:
                log.error(
                    "App API request failed request_id=%s path=%s error_type=%s",
                    request.state.request_id, request.url.path, type(exc).__name__,
                )
            response = JSONResponse(
                _error_payload(error, request.state.request_id), status_code=error.status
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def handle_exception(request: Request, exc: Exception):
        error = _translate_error(exc)
        if error.status >= 500:
            log.error("App API error request_id=%s error_type=%s",
                      request.state.request_id, type(exc).__name__)
        return JSONResponse(
            _error_payload(error, request.state.request_id), status_code=error.status
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        error = APIError(exc.status_code, code, "没有找到对应接口。")
        return JSONResponse(
            _error_payload(error, request.state.request_id), status_code=error.status
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, _exc: RequestValidationError):
        error = APIError(400, "validation_error", "请求参数无效。")
        return JSONResponse(
            _error_payload(error, request.state.request_id), status_code=400
        )

    def declared_body_length(request: Request, maximum: int) -> None:
        declared = request.headers.get("content-length")
        if declared:
            try:
                length = int(declared)
            except ValueError as exc:
                raise APIError(400, "validation_error", "Content-Length 无效。") from exc
            if length < 0:
                raise APIError(400, "validation_error", "Content-Length 无效。")
            if length > maximum:
                raise APIError(413, "body_too_large", "请求内容过大。")

    @asynccontextmanager
    async def body_read_slot():
        try:
            await asyncio.wait_for(
                body_gate.acquire(), timeout=settings.body_gate_timeout_seconds
            )
        except TimeoutError as exc:
            raise APIError(
                503, "upload_busy", "当前上传较多，请稍后重试。", retryable=True
            ) from exc
        try:
            yield
        finally:
            body_gate.release()

    async def guarded_body_chunks(request: Request):
        """Yield request chunks with total, idle and slowloris deadlines."""
        iterator = request.stream().__aiter__()
        started = time.monotonic()
        received = 0
        while True:
            elapsed = time.monotonic() - started
            remaining = settings.body_read_timeout_seconds - elapsed
            if remaining <= 0:
                raise TimeoutError
            try:
                chunk = await asyncio.wait_for(
                    anext(iterator),
                    timeout=min(settings.body_idle_timeout_seconds, remaining),
                )
            except StopAsyncIteration:
                return
            received += len(chunk)
            elapsed = time.monotonic() - started
            if (elapsed >= settings.body_speed_grace_seconds
                    and received / max(elapsed, 0.001)
                    < settings.min_body_bytes_per_second):
                raise APIError(
                    408, "body_too_slow", "请求上传速度过慢，请重试。", retryable=True
                )
            yield chunk

    async def limited_json_body(request: Request) -> bytes:
        """Read small JSON bodies behind the same global concurrency gate."""
        declared_body_length(request, settings.max_json_body_bytes)
        data = bytearray()
        async with body_read_slot():
            try:
                async for chunk in guarded_body_chunks(request):
                    data.extend(chunk)
                    if len(data) > settings.max_json_body_bytes:
                        raise APIError(413, "body_too_large", "请求内容过大。")
            except TimeoutError as exc:
                raise APIError(
                    408, "body_timeout", "请求上传超时，请重试。", retryable=True
                ) from exc
        return bytes(data)

    @asynccontextmanager
    async def limited_multipart_body(request: Request):
        """Spool and hash multipart bytes without a 26MiB in-memory copy.

        App Attest signs the SHA-256 of the exact wire body, so authentication
        cannot happen until every byte arrived.  We retain one bounded spool,
        then replay it into Starlette's maintained multipart parser.
        """
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type.lower():
            raise APIError(400, "validation_error", "moment 必须使用 multipart/form-data。")
        declared_body_length(request, settings.max_body_bytes)
        async with body_read_slot():
            spool = tempfile.SpooledTemporaryFile(
                max_size=512 * 1024, mode="w+b", dir=settings.upload_dir
            )
            try:
                digest = hashlib.sha256()
                total = 0
                try:
                    async for chunk in guarded_body_chunks(request):
                        total += len(chunk)
                        if total > settings.max_body_bytes:
                            raise APIError(
                                413, "body_too_large", "请求内容过大。"
                            )
                        digest.update(chunk)
                        spool.write(chunk)
                except TimeoutError as exc:
                    raise APIError(
                        408, "body_timeout", "请求上传超时，请重试。", retryable=True
                    ) from exc
                spool.seek(0)

                async def replay_receive():
                    chunk = spool.read(64 * 1024)
                    return {
                        "type": "http.request",
                        "body": chunk,
                        "more_body": bool(chunk),
                    }

                # The original ASGI stream is consumed.  Replay the single spool
                # to Request.form(); this is still Starlette/python-multipart,
                # never a handwritten boundary parser.
                request._receive = replay_receive
                request._stream_consumed = False
                yield digest.digest()
            finally:
                spool.close()

    def _authenticate(
        request: Request,
        raw: bytes | None = b"",
        *,
        body_digest: bytes | None = None,
        allow_deleting: bool = False,
    ):
        key_header = request.headers.get("x-murmur-key-id")
        if key_header is not None and not 1 <= len(key_header) <= 180:
            raise APIError(400, "validation_error", "设备安全密钥格式无效。")
        context = authenticator.authenticate(
            method=request.method,
            path=request.url.path,
            body=raw,
            body_digest=body_digest,
            key_id=key_header,
            challenge_id=request.headers.get("x-murmur-challenge-id"),
            assertion_b64=request.headers.get("x-murmur-assertion"),
            development_token=request.headers.get("x-murmur-development-token"),
        )
        limiter.check(f"user:{context.user_id}")
        if not allow_deleting:
            store.require_user_ready(context.user_id)
        return context

    async def authenticate(
        request: Request,
        raw: bytes | None = b"",
        *,
        body_digest: bytes | None = None,
        allow_deleting: bool = False,
    ):
        # consume_challenge and advance_counter are synchronous write
        # transactions that may wait out the SQLite busy_timeout while the
        # worker holds the write lock; keep them off the event loop, as
        # delete_account already does for account erasure.
        return await asyncio.to_thread(
            _authenticate, request, raw,
            body_digest=body_digest, allow_deleting=allow_deleting,
        )

    @app.post("/v1/auth/challenges")
    async def issue_challenge(request: Request):
        raw = await limited_json_body(request)
        data = _json(raw)
        purpose = data.get("purpose")
        if purpose not in {"enrollment", "request"}:
            raise APIError(400, "validation_error", "purpose 无效。")
        key_id = data.get("key_id")
        if purpose == "request":
            if not isinstance(key_id, str) or not 1 <= len(key_id) <= 180:
                raise APIError(400, "validation_error", "request challenge 需要 key_id。")
            if not settings.production:
                authenticator._check_development_token(
                    request.headers.get("x-murmur-development-token")
                )
        try:
            challenge_id, challenge, expires_at = await asyncio.to_thread(
                store.issue_challenge,
                purpose, key_id=key_id if purpose == "request" else None,
            )
        except NotFound as exc:
            if purpose == "request":
                raise APIError(
                    404, "attestation_key_unknown", "设备安全密钥尚未注册。"
                ) from exc
            raise
        return {"challenge_id": challenge_id, "challenge": _b64url(challenge),
                "expires_at": expires_at}

    @app.post("/v1/enrollments", status_code=201)
    async def enroll(request: Request):
        raw = await limited_json_body(request)
        data = _json(raw)
        challenge_id = _required_text(data, "challenge_id")
        invite_code = _required_text(data, "invite_code")
        key_id = _required_text(data, "key_id", 180)
        environment = data.get("environment")
        if environment not in {"development", "production"}:
            raise APIError(400, "validation_error", "environment 无效。")
        # Absent means iOS: the shipped client predates a second platform and
        # must keep enrolling without being rebuilt.
        platform = data.get("platform", "ios")
        if platform not in PLATFORMS:
            raise APIError(400, "validation_error", "platform 无效。")
        # Android enrolment also pulls Google's revocation list over the
        # network inside verify_attestation; the thread keeps that fetch and
        # the SQLite writes below off the event loop.
        result = await asyncio.to_thread(
            authenticator.enroll,
            challenge_id=challenge_id,
            key_id=key_id,
            attestation_b64=data.get("attestation"),
            development_token=request.headers.get("x-murmur-development-token"),
            platform=platform,
        )
        if environment != result.environment:
            raise APIError(401, "invalid_attestation", "验证环境不匹配。")
        enrollment = await asyncio.to_thread(
            store.redeem_invite,
            code=invite_code,
            key_id=key_id,
            public_key=result.public_key or None,
            receipt=result.receipt,
            counter=result.counter,
            environment=result.environment,
            platform=result.platform,
            device_name=(str(data.get("device_name"))[:120]
                         if data.get("device_name") else None),
        )
        return {"user_id": enrollment.user_id, "device_id": enrollment.device_id,
                "key_id": enrollment.key_id}

    @app.post("/v1/enrollments/recover")
    async def recover_enrollment(request: Request):
        """Recover identity after a committed 201 response was lost in transit.

        App Attest keys cannot safely be attested a second time with a fresh
        nonce.  The already-enrolled key instead proves possession with the
        normal per-request assertion and receives the same identity tuple.
        """
        raw = await limited_json_body(request)
        _json(raw)
        auth = await authenticate(request, raw)
        enrollment = await asyncio.to_thread(store.enrollment_for_key, auth.key_id)
        return {
            "user_id": enrollment.user_id,
            "device_id": enrollment.device_id,
            "key_id": enrollment.key_id,
        }

    @app.post("/v1/moments", status_code=202)
    async def create_moment(request: Request):
        async with limited_multipart_body(request) as wire_digest:
            auth = await authenticate(request, None, body_digest=wire_digest)
            try:
                form = await request.form(
                    max_files=1, max_fields=7, max_part_size=64 * 1024
                )
            except Exception as exc:
                raise APIError(400, "validation_error", "multipart 内容无效。") from exc
            temp_path: Path | None = None
            try:
                note_value = form.get("note")
                note = str(note_value).strip() if note_value is not None else ""
                if len(note) > settings.max_note_chars:
                    raise APIError(400, "validation_error", "文字最多 2000 字。")
                idempotency_key = str(form.get("idempotency_key") or "").strip()
                if not 8 <= len(idempotency_key) <= 200:
                    raise APIError(400, "validation_error", "idempotency_key 无效。")
                # 当年今日 opens its room by uploading one photo and nothing
                # else; the reply to it is a reading of the image rather than
                # an ordinary line, so the intent travels with the upload.
                intent = str(form.get("intent") or "").strip() or None
                if intent is not None and intent not in MOMENT_INTENTS:
                    raise APIError(400, "validation_error", "intent 无效。")
                context_value = form.get("context_moment_ids")
                context_moment_ids: list[str] | None = None
                if context_value not in (None, ""):
                    try:
                        decoded_context = json.loads(str(context_value))
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise APIError(
                            400, "validation_error", "context_moment_ids 无效。"
                        ) from exc
                    if not isinstance(decoded_context, list) or len(decoded_context) > 8:
                        raise APIError(
                            400, "validation_error", "context_moment_ids 无效。"
                        )
                    context_moment_ids = []
                    seen_context: set[str] = set()
                    for value in decoded_context:
                        if not isinstance(value, str):
                            raise APIError(
                                400, "validation_error", "context_moment_ids 无效。"
                            )
                        moment = value.strip()
                        if not 1 <= len(moment) <= 200:
                            raise APIError(
                                400, "validation_error", "context_moment_ids 无效。"
                            )
                        if moment not in seen_context:
                            seen_context.add(moment)
                            context_moment_ids.append(moment)
                # 当年今日推上来的是一张旧照片，App 顺手把这张照片自己的
                # 事实一起送来：拍摄时间、坐标，以及机上反解好的地名。
                # 这些不从图里猜——重新编码过的 JPEG 早就没有 EXIF 了。
                provenance = _parse_provenance(form.get("provenance"))
                # One song, as its own turn.  The client also sends a text
                # fallback in `note`, so an older server — and an older client
                # reading a newer server — still has something to show.
                music_value = form.get("music_track")
                if music_value is not None and hasattr(music_value, "read"):
                    raise APIError(
                        400, "validation_error", "music_track 必须是文本字段。"
                    )
                if music_value not in (None, "") and not music_enabled_for(
                    settings, auth.user_id
                ):
                    raise APIError(403, "music_unavailable", "音乐功能未开启。")
                music_track, music_canonical = parse_music_track(music_value)
                # The wire now understands two providers, so "music is on" is
                # not the same question as "this provider is on".  Without this
                # a client could attach a card for a switched-off provider and
                # have the snapshot stored with nothing able to verify it.
                if music_track is not None and not music_provider_enabled_for(
                    settings, auth.user_id, music_track["provider"]
                ):
                    raise APIError(403, "music_unavailable", "音乐功能未开启。")
                upload = form.get("image")
                image_hash = hashlib.sha256()
                image_size = 0
                image_prefix = bytearray()
                if upload is not None:
                    if not hasattr(upload, "read"):
                        raise APIError(
                            400, "validation_error", "image 必须是上传文件。"
                        )
                    media_type = str(getattr(upload, "content_type", "") or "")
                    if not media_type.startswith("image/"):
                        raise APIError(
                            400, "validation_error", "上传内容不是支持的图片。"
                        )
                    fd, name = tempfile.mkstemp(
                        prefix="murmur-upload-", dir=settings.upload_dir
                    )
                    temp_path = Path(name)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            while chunk := await upload.read(64 * 1024):
                                image_size += len(chunk)
                                if image_size > settings.max_image_bytes:
                                    raise APIError(
                                        413, "image_too_large", "图片最大为 25MB。"
                                    )
                                if len(image_prefix) < 16:
                                    image_prefix.extend(chunk[:16 - len(image_prefix)])
                                image_hash.update(chunk)
                                handle.write(chunk)
                    finally:
                        await upload.close()
                    if image_size == 0 or not _looks_like_image(bytes(image_prefix)):
                        raise APIError(
                            400, "validation_error", "上传内容不是支持的图片。"
                        )
                if not note and not temp_path and not music_track:
                    raise APIError(
                        400, "validation_error", "文字、图片和歌曲至少要有一个。"
                    )
                # A song is a turn of its own in this version: pairing it with a
                # photo would make one moment mean two different things.
                if music_track and temp_path:
                    raise APIError(
                        400, "invalid_music_attachment", "歌曲不能和照片一起发送。"
                    )
                if provenance is not None and not temp_path:
                    raise APIError(
                        400, "validation_error", "provenance 只能随图片一起提交。"
                    )
                if intent == "photo_reading" and (not temp_path or note):
                    raise APIError(
                        400, "validation_error", "读图只接受一张不带文字的照片。"
                    )

                await asyncio.to_thread(
                    apply_stop_preference, store, auth.user_id, note
                )
                digest_input = (
                    note.encode("utf-8") + b"\x00" + image_hash.digest()
                    + b"\x00" + (intent or "").encode("utf-8")
                )
                if context_moment_ids is not None:
                    digest_input += b"\x00" + json.dumps(
                        context_moment_ids,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                if provenance is not None:
                    digest_input += b"\x00" + provenance.encode("utf-8")
                # The song is part of what the key promises.  Reusing one key
                # for a different track has to be a conflict, not a silent
                # no-op that returns the first song's receipt.
                if music_canonical is not None:
                    digest_input += b"\x00" + music_canonical.encode("utf-8")
                digest = hashlib.sha256(digest_input).hexdigest()
                result = await asyncio.to_thread(
                    store.create_moment,
                    user_id=auth.user_id,
                    note=note or None,
                    image_path=str(temp_path) if temp_path else None,
                    idempotency_key=idempotency_key,
                    request_digest=digest,
                    intent=intent,
                    context_moment_ids=context_moment_ids,
                    provenance=provenance,
                    music_track=music_canonical,
                )
                if not result.created and temp_path:
                    temp_path.unlink(missing_ok=True)
                    temp_path = None
                return {"moment_id": result.moment_id, "status": result.status}
            except Exception:
                if temp_path:
                    temp_path.unlink(missing_ok=True)
                raise
            finally:
                await form.close()

    @app.get("/v1/moments/{moment_id}/events")
    async def moment_events(moment_id: str, request: Request):
        raw = b""
        auth = await authenticate(request, raw)
        await asyncio.to_thread(store.moment_for_user, moment_id, auth.user_id)
        last_header = request.headers.get("last-event-id", "0") or "0"
        try:
            last_sequence = max(0, int(last_header))
        except ValueError as exc:
            raise APIError(400, "validation_error", "Last-Event-ID 必须是整数。") from exc

        async def stream():
            sequence = last_sequence
            last_keepalive = time.monotonic()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    events = await asyncio.to_thread(
                        # Ownership was verified above; skip the per-poll
                        # re-check inside events_after.
                        store.events_after, moment_id, auth.user_id, sequence, False
                    )
                except NotFound:
                    # Account erasure cancels in-flight moments; the stream
                    # ends quietly instead of dropping the connection with a
                    # server error.
                    return
                for event in events:
                    sequence = event["sequence"]
                    data = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
                    yield (f"id: {sequence}\nevent: {event['event']}\ndata: {data}\n\n"
                           .encode())
                    if event["event"] in {"done", "error"}:
                        return
                if await request.is_disconnected():
                    return
                if time.monotonic() - last_keepalive >= 15:
                    yield b": keep-alive\n\n"
                    last_keepalive = time.monotonic()
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                     "Connection": "keep-alive"},
        )

    @app.get("/v1/proactive/current")
    async def proactive_current(request: Request):
        auth = await authenticate(request, b"")
        current = await asyncio.to_thread(store.current_proactive, auth.user_id)
        if current is None:
            return Response(status_code=204)
        return current

    @app.post("/v1/moments/{moment_id}/ack")
    async def acknowledge(moment_id: str, request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        data = _json(raw)
        reply = data.get("reply")
        if reply is not None and (not isinstance(reply, str)
                                  or len(reply.strip()) > settings.max_note_chars):
            raise APIError(400, "validation_error", "reply 无效。")
        moment = await asyncio.to_thread(store.acknowledge, moment_id, auth.user_id)
        reply = reply.strip() if isinstance(reply, str) else ""
        if reply and moment.get("memory_entry_id"):
            await asyncio.to_thread(
                record_memory_reply, settings, cfg,
                int(moment["memory_entry_id"]), reply,
            )
        await asyncio.to_thread(apply_stop_preference, store, auth.user_id, reply)
        return {"acknowledged": True}

    @app.put("/v1/device")
    async def update_device(request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        data = _json(raw)
        environment = data.get("environment")
        if environment not in {"development", "production"}:
            raise APIError(400, "validation_error", "environment 无效。")
        key = await asyncio.to_thread(store.auth_key, auth.key_id)
        if environment != key.environment:
            raise APIError(400, "validation_error", "设备环境与注册环境不匹配。")
        # `apns_token` is the name the shipped iOS client sends; `push_token` is
        # the platform-neutral spelling.  The value is graded against the
        # platform recorded at enrolment, never against one the caller supplies.
        token = data.get("push_token")
        if token is None:
            token = data.get("apns_token")
        if token is not None and not _valid_push_token(token, key.platform):
            raise APIError(400, "validation_error", "push token 无效。")
        timezone = str(data.get("timezone") or "Asia/Shanghai")
        if len(timezone) > 80:
            raise APIError(400, "validation_error", "timezone 无效。")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise APIError(400, "validation_error", "timezone 无效。") from exc
        device = await asyncio.to_thread(
            store.update_device,
            auth.key_id,
            push_token=token,
            environment=environment,
            timezone=timezone,
            device_name=(str(data.get("device_name"))[:120]
                         if data.get("device_name") else None),
        )
        return device

    @app.get("/v1/devices")
    async def list_devices(request: Request):
        auth = await authenticate(request, b"")
        return {"devices": await asyncio.to_thread(store.devices, auth.user_id)}

    @app.delete("/v1/devices/{device_id}", status_code=204)
    async def revoke_device(device_id: str, request: Request):
        auth = await authenticate(request, b"")
        if not await asyncio.to_thread(store.revoke_device, auth.user_id, device_id):
            raise APIError(404, "not_found", "没有找到对应设备。")
        return Response(status_code=204)

    @app.get("/v1/preferences")
    async def get_preferences(request: Request):
        auth = await authenticate(request, b"")
        prefs = await asyncio.to_thread(store.preferences, auth.user_id)
        return {
            "daily_frequency": prefs["daily_frequency"],
            "quiet_start": prefs["quiet_start"],
            "quiet_end": prefs["quiet_end"],
        }

    @app.patch("/v1/preferences")
    async def update_preferences(request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        data = _json(raw)
        current = await asyncio.to_thread(store.preferences, auth.user_id)
        frequency = data.get("daily_frequency", current["daily_frequency"])
        if not isinstance(frequency, int) or isinstance(frequency, bool):
            raise APIError(400, "validation_error", "daily_frequency 无效。")
        try:
            return await asyncio.to_thread(
                store.update_preferences,
                auth.user_id,
                daily_frequency=frequency,
                quiet_start=str(data.get("quiet_start", current["quiet_start"])),
                quiet_end=str(data.get("quiet_end", current["quiet_end"])),
            )
        except ValueError as exc:
            raise APIError(400, "validation_error", str(exc)) from exc

    @app.get("/v1/music/config")
    async def music_config(request: Request):
        auth = await authenticate(request, b"")
        audius_enabled = audius_music_enabled_for(settings, auth.user_id)
        netease_enabled = netease_catalog_enabled_for(settings, auth.user_id)
        room_enabled = netease_room_enabled_for(settings, auth.user_id)
        result = {
            "enabled": audius_enabled or netease_enabled,
            # The singular field is the compatibility view read by shipped
            # clients.  Prefer Audius while it remains available because only
            # that provider has first-party in-App playback.
            "provider": "audius" if audius_enabled or not netease_enabled else "netease",
            "playback_reporting": (
                audius_enabled and settings.music_playback_reporting
            ),
        }
        if netease_enabled:
            providers = []
            if audius_enabled:
                providers.append({
                    "id": "audius",
                    "capabilities": ["cards", "native_playback"],
                })
            netease_capabilities = ["cards", "external_open", "resolve_shared", "search"]
            if room_enabled:
                netease_capabilities.append("listen_together")
            providers.append({
                "id": "netease", "capabilities": netease_capabilities,
            })
            result["providers"] = providers
            result["listen_together"] = {
                "enabled": room_enabled,
                "provider": "netease",
                "commands": [
                    "play", "pause", "resume", "previous", "next", "play_track",
                ],
            }
        return result

    @app.post("/v1/music/resolve-shared")
    async def resolve_shared_music(request: Request):
        """Turn one shared link into catalog-authoritative song metadata.

        Nothing here is sent to the model or written to the transcript: the
        person still has to look at the result and send it themselves.  Titles,
        artists and covers in the shared prose are never read — only the song
        id inside a link on an allowed host, re-resolved against the catalog.
        """
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        if music_link is None or not netease_catalog_enabled_for(settings, auth.user_id):
            raise APIError(403, "music_unavailable", "音乐功能未开启。")
        data = _json(raw)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise APIError(400, "validation_error", "分享内容无效。")
        if len(text.encode("utf-8")) > MAX_SHARED_TEXT_BYTES:
            raise APIError(413, "body_too_large", "分享内容过大。")
        # Shape-checked and then dropped.  Resolving is a read: the key exists
        # so the client can de-duplicate its own draft, not to make the server
        # remember a share it was never asked to keep.
        key = str(data.get("idempotency_key") or "").strip()
        if not 8 <= len(key) <= 200:
            raise APIError(400, "validation_error", "idempotency_key 无效。")
        try:
            track = await asyncio.to_thread(music_link.parse_shared_text, text)
        except MusicLinkRejected as exc:
            raise APIError(
                400, "music_link_rejected", "这个链接打不开成一首歌。"
            ) from exc
        except MusicTrackUnavailable as exc:
            raise APIError(
                404, "music_track_unavailable", "这首歌现在拿不到。"
            ) from exc
        except MusicCatalogUnavailable as exc:
            raise APIError(
                503, "music_catalog_unavailable", "音乐服务暂时不可用，稍后再试。",
                retryable=True,
            ) from exc
        except MusicError as exc:
            # The catalog answered with something this server will not sign off
            # on.  Retrying cannot help, and a 500 would read as our own bug.
            raise APIError(
                502, "music_catalog_unavailable", "音乐服务返回了看不懂的内容。"
            ) from exc
        if track is None:
            raise APIError(422, "music_link_absent", "这段文字里没有网易云歌曲链接。")
        return {"track": track}

    @app.post("/v1/music/search")
    async def search_music(request: Request):
        """Search the NetEase catalog for songs the person can then send.

        POST rather than GET, and all three reasons carry weight: App Attest
        signs the exact body bytes, so a query string would sit outside the
        assertion; the query is text somebody typed and does not belong in a
        URL or an access log; and `limited_json_body` is the body gate every
        other music write already goes through.

        Gated on the catalog, not on the room: searching is a catalog
        capability, and the composer's own picker wants it too.
        """
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        if music_catalog is None or not netease_catalog_enabled_for(settings, auth.user_id):
            raise APIError(403, "music_unavailable", "音乐功能未开启。")
        data = _json(raw)
        purpose = data.get("purpose", "share")
        if not isinstance(purpose, str) or purpose not in {"share", "listen_together"}:
            raise APIError(400, "validation_error", "purpose 无效。")
        playability_transport = (
            room_transport(auth.user_id) if purpose == "listen_together" else None
        )
        query = data.get("query")
        if not isinstance(query, str):
            raise APIError(400, "validation_error", "搜索词无效。")
        if len(query.encode("utf-8")) > MAX_SHARED_TEXT_BYTES:
            raise APIError(413, "body_too_large", "搜索词过大。")
        # Bounded here as well as in the adapter.  `_bounded_text` raises
        # `MusicTrackInvalid`, which is not in the ladder below and would fall
        # through to a 502 — and "your query was 300 characters" is not a
        # bad-gateway.
        clean = " ".join(query.split())
        if not 1 <= len(clean) <= 120:
            raise APIError(400, "validation_error", "搜索词无效。")
        limit = data.get("limit", MAX_MUSIC_SEARCH_RESULTS)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise APIError(400, "validation_error", "limit 无效。")
        # Rejected rather than silently clamped.  The catalog caps at five; a
        # client that asks for twenty, is handed five and is told nothing
        # builds a pager that never terminates.
        if not 1 <= limit <= MAX_MUSIC_SEARCH_RESULTS:
            raise APIError(400, "validation_error", "limit 无效。")
        started = time.monotonic()
        catalog_started = started
        try:
            tracks = await asyncio.to_thread(
                music_catalog.search,
                clean,
                MAX_MUSIC_SEARCH_CANDIDATES,
                provider="netease",
                complete=True,
            )
        except MusicTrackInvalid as exc:
            raise APIError(400, "validation_error", "搜索词无效。") from exc
        except MusicCatalogUnavailable as exc:
            raise APIError(
                503, "music_catalog_unavailable", "音乐服务暂时不可用，稍后再试。",
                retryable=True,
            ) from exc
        except MusicError as exc:
            raise APIError(
                502, "music_catalog_unavailable", "音乐服务返回了看不懂的内容。"
            ) from exc
        catalog_ms = round((time.monotonic() - catalog_started) * 1000, 1)
        # Keep the complete bounded candidate window until room playability is
        # known. Truncating to the public page first can hide a playable exact
        # version behind five unavailable exact versions.
        tracks = rank_music_search_results(
            clean, tracks, limit=MAX_MUSIC_SEARCH_CANDIDATES
        )
        empty_reason = None
        rights_ms = 0.0
        if purpose == "listen_together" and tracks:
            assert playability_transport is not None
            rights_started = time.monotonic()
            try:
                playable = set(await asyncio.to_thread(
                    playability_transport.playable_song_ids,
                    user_id=auth.user_id,
                    song_ids=[track["track_id"] for track in tracks],
                ))
            except ListenTogetherError as exc:
                raise room_api_error(exc) from exc
            rights_ms = round((time.monotonic() - rights_started) * 1000, 1)
            tracks = [track for track in tracks if track["track_id"] in playable]
            if not tracks:
                empty_reason = "no_common_playable_track"
        tracks = tracks[:limit]
        log.info(
            "music_search_timing purpose=%s catalog_ms=%s rights_ms=%s total_ms=%s result_count=%s",
            purpose,
            catalog_ms,
            rights_ms,
            round((time.monotonic() - started) * 1000, 1),
            len(tracks),
        )
        # Finding nothing is an answer, not a failure.
        result = {"tracks": tracks}
        if empty_reason is not None:
            result["empty_reason"] = empty_reason
        return result

    @app.put("/v1/music/playback-state")
    async def update_playback_state(request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        if not audius_music_enabled_for(settings, auth.user_id):
            raise APIError(403, "music_unavailable", "音乐功能未开启。")
        data = _json(raw)
        if data.get("version") != 1:
            raise APIError(400, "validation_error", "playback-state 版本不支持。")
        session_id = str(data.get("session_id") or "").strip()
        if not 8 <= len(session_id) <= 64:
            raise APIError(400, "validation_error", "session_id 无效。")
        sequence = data.get("sequence")
        if (not isinstance(sequence, int) or isinstance(sequence, bool)
                or not 0 <= sequence <= 1_000_000):
            raise APIError(400, "validation_error", "sequence 无效。")
        state = data.get("state")
        if state not in {"started", "paused", "resumed", "completed", "stopped"}:
            raise APIError(400, "validation_error", "state 无效。")
        # Validated for shape and then dropped: TTL is measured on the server's
        # own clock, so a client timestamp can never extend how long a listening
        # state stays visible to the model.
        occurred_at = data.get("occurred_at")
        if not isinstance(occurred_at, str) or not 1 <= len(occurred_at) <= 64:
            raise APIError(400, "validation_error", "occurred_at 无效。")
        try:
            datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise APIError(400, "validation_error", "occurred_at 无效。") from exc
        track = normalize_playback_track(data.get("track"))
        # Reporting can be turned off on its own while the rest of music stays
        # on.  Accept and drop, so a client that has not refetched its config
        # is not stuck retrying a request it is told is retryable.
        if not settings.music_playback_reporting:
            return {"accepted": False, "active": False, "expires_at": None}
        return await asyncio.to_thread(
            store.update_playback_state,
            user_id=auth.user_id,
            device_id=auth.device_id,
            session_id=session_id,
            sequence=sequence,
            state=state,
            track_json=json.dumps(
                track, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
            duration_seconds=track.get("duration_seconds"),
        )

    def room_transport(user_id: str) -> RoomIPCClient:
        """Both gates, then the socket: an off switch never reaches the worker."""
        if room_client is None or not netease_room_enabled_for(settings, user_id):
            raise APIError(403, "listen_together_unavailable", "一起听功能未开启。")
        return room_client

    def room_handle(value: str) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 200:
            raise APIError(400, "validation_error", "room_handle 无效。")
        return value

    def room_idempotency_key(data: dict) -> str:
        key = str(data.get("idempotency_key") or "").strip()
        if not 8 <= len(key) <= 200:
            raise APIError(400, "validation_error", "idempotency_key 无效。")
        return key

    def room_track(value) -> dict:
        try:
            track = normalize_music_track(value)
        except MusicTrackInvalid as exc:
            raise APIError(400, "validation_error", "歌曲信息无效。") from exc
        # A room is a NetEase surface.  An Audius id sent here would name a
        # completely different song on the other side of the protocol.
        if track["provider"] != "netease":
            raise APIError(400, "validation_error", "一起听只支持网易云歌曲。")
        return track

    @app.post("/v1/listen-together/rooms", status_code=201)
    async def create_listen_together_room(request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        transport = room_transport(auth.user_id)
        data = _json(raw)
        track = room_track(data.get("initial_track"))
        key = room_idempotency_key(data)
        try:
            snapshot = await asyncio.to_thread(
                transport.create,
                user_id=auth.user_id, initial_track=track, idempotency_key=key,
            )
        except ListenTogetherError as exc:
            # A fresh key plus an existing room means the user tapped “一起听”
            # on another card.  Reuse that room and switch its track.  Replaying
            # the original create key never enters this branch because the
            # manager returns its idempotent create result first.
            if getattr(exc, "code", None) != "room_conflict":
                raise room_api_error(exc) from exc
            try:
                current = await asyncio.to_thread(
                    transport.current, user_id=auth.user_id
                )
                if current is None:
                    raise exc
                reuse_key = "reuse:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
                result = await asyncio.to_thread(
                    transport.command,
                    user_id=auth.user_id,
                    room_handle=current.room_handle,
                    command="play_track",
                    track=track,
                    idempotency_key=reuse_key,
                )
                snapshot = result.room
            except ListenTogetherError as reuse_exc:
                raise room_api_error(reuse_exc) from reuse_exc
        return snapshot.to_wire()

    @app.get("/v1/listen-together/rooms/current")
    async def current_listen_together_room(request: Request):
        auth = await authenticate(request, b"")
        transport = room_transport(auth.user_id)
        try:
            snapshot = await asyncio.to_thread(transport.current, user_id=auth.user_id)
        except ListenTogetherError as exc:
            raise room_api_error(exc) from exc
        if snapshot is None:
            raise APIError(404, "room_not_found", ROOM_ERROR_MESSAGE["room_not_found"])
        return snapshot.to_wire()

    @app.post("/v1/listen-together/rooms/{handle}/commands")
    async def command_listen_together_room(handle: str, request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        transport = room_transport(auth.user_id)
        data = _json(raw)
        command = data.get("command")
        if command not in ROOM_COMMANDS:
            raise APIError(400, "validation_error", "command 无效。")
        raw_track = data.get("track")
        if command == "play_track":
            if raw_track is None:
                raise APIError(400, "validation_error", "换歌需要一首歌。")
            track = room_track(raw_track)
        else:
            if raw_track is not None:
                raise APIError(400, "validation_error", "这个命令不接受歌曲。")
            track = None
        key = room_idempotency_key(data)
        try:
            result = await asyncio.to_thread(
                transport.command,
                user_id=auth.user_id, room_handle=room_handle(handle),
                command=command, track=track, idempotency_key=key,
            )
        except ListenTogetherError as exc:
            raise room_api_error(exc) from exc
        return result.to_wire()

    @app.delete("/v1/listen-together/rooms/{handle}")
    async def close_listen_together_room(handle: str, request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw)
        transport = room_transport(auth.user_id)
        key = room_idempotency_key(_json(raw))
        try:
            snapshot = await asyncio.to_thread(
                transport.close,
                user_id=auth.user_id, room_handle=room_handle(handle),
                idempotency_key=key,
            )
        except ListenTogetherError as exc:
            raise room_api_error(exc) from exc
        return snapshot.to_wire()

    @app.delete("/v1/account", status_code=204)
    async def delete_account(request: Request):
        raw = await limited_json_body(request)
        auth = await authenticate(request, raw, allow_deleting=True)
        # flock, SQLite and filesystem cleanup are intentionally synchronous;
        # keep them off the ASGI event loop so one deletion cannot stall every
        # other user's API requests while waiting for a worker's user lock.
        await asyncio.to_thread(erase_account, store, settings, auth.user_id)
        return Response(status_code=204)

    return app


def run(host: str = "127.0.0.1", port: int = 8766) -> None:
    """CLI entry point for ``murmur app-api --host ... --port ...``."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("App API requires uvicorn>=0.30") from exc
    cfg = Config.load()
    settings = AppSettings.from_env(cfg)
    validate_bind_host(settings, host)
    app = create_app(settings, cfg=cfg)
    uvicorn.run(
        app, host=host, port=port, proxy_headers=True, server_header=False,
        # Only the loopback reverse proxy may assert the client IP; without
        # this, any caller could spoof X-Forwarded-For past the IP rate limit.
        forwarded_allow_ips="127.0.0.1,::1",
    )


def main() -> None:
    run()


def validate_bind_host(settings: AppSettings, host: str) -> None:
    """Production is reachable only through the local Caddy TLS boundary."""
    if settings.production and host not in {"127.0.0.1", "::1"}:
        raise RuntimeError(
            "production App API must bind to loopback behind the HTTPS reverse proxy"
        )
