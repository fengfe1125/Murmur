"""HTTPS-facing API for the first-party Murmur iOS app.

FastAPI and multipart imports are intentionally delayed so the existing bot
commands remain usable before the App API optional runtime has been installed.
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
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .app_auth import (
    AppAttestUnsupported,
    AppAuthenticator,
    AppAuthError,
    AttestationRequired,
    InvalidAttestation,
)
from .app_lock import UserOperationLock
from .app_settings import AppSettings
from .app_store import (
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


def create_app(
    settings: AppSettings | None = None,
    *,
    cfg: Config | None = None,
    store: AppStore | None = None,
    authenticator: AppAuthenticator | None = None,
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
    # With postponed annotations the locally imported Request name must also be
    # visible to FastAPI's type-hint resolver; otherwise it becomes a query field.
    globals()["Request"] = Request
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
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

    def authenticate(
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
            challenge_id, challenge, expires_at = store.issue_challenge(
                purpose, key_id=key_id if purpose == "request" else None
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
        result = authenticator.enroll(
            challenge_id=challenge_id,
            key_id=key_id,
            attestation_b64=data.get("attestation"),
            development_token=request.headers.get("x-murmur-development-token"),
            platform="ios",
        )
        if environment != result.environment:
            raise APIError(401, "invalid_attestation", "验证环境不匹配。")
        enrollment = store.redeem_invite(
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
        auth = authenticate(request, raw)
        enrollment = store.enrollment_for_key(auth.key_id)
        return {
            "user_id": enrollment.user_id,
            "device_id": enrollment.device_id,
            "key_id": enrollment.key_id,
        }

    @app.post("/v1/moments", status_code=202)
    async def create_moment(request: Request):
        async with limited_multipart_body(request) as wire_digest:
            auth = authenticate(request, None, body_digest=wire_digest)
            try:
                form = await request.form(
                    max_files=1, max_fields=4, max_part_size=64 * 1024
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
                if not note and not temp_path:
                    raise APIError(
                        400, "validation_error", "文字和图片至少要有一个。"
                    )

                apply_stop_preference(store, auth.user_id, note)
                digest = hashlib.sha256(
                    note.encode("utf-8") + b"\x00" + image_hash.digest()
                ).hexdigest()
                result = store.create_moment(
                    user_id=auth.user_id,
                    note=note or None,
                    image_path=str(temp_path) if temp_path else None,
                    idempotency_key=idempotency_key,
                    request_digest=digest,
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
        auth = authenticate(request, raw)
        store.moment_for_user(moment_id, auth.user_id)
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
                events = store.events_after(moment_id, auth.user_id, sequence)
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
        auth = authenticate(request, b"")
        current = store.current_proactive(auth.user_id)
        if current is None:
            return Response(status_code=204)
        return current

    @app.post("/v1/moments/{moment_id}/ack")
    async def acknowledge(moment_id: str, request: Request):
        raw = await limited_json_body(request)
        auth = authenticate(request, raw)
        data = _json(raw)
        reply = data.get("reply")
        if reply is not None and (not isinstance(reply, str)
                                  or len(reply.strip()) > settings.max_note_chars):
            raise APIError(400, "validation_error", "reply 无效。")
        moment = store.acknowledge(moment_id, auth.user_id)
        reply = reply.strip() if isinstance(reply, str) else ""
        if reply and moment.get("memory_entry_id"):
            with Memory(settings.memory_db_path) as memory:
                memory.add_reply(int(moment["memory_entry_id"]), reply)
        apply_stop_preference(store, auth.user_id, reply)
        return {"acknowledged": True}

    @app.put("/v1/device")
    async def update_device(request: Request):
        raw = await limited_json_body(request)
        auth = authenticate(request, raw)
        data = _json(raw)
        environment = data.get("environment")
        if environment not in {"development", "production"}:
            raise APIError(400, "validation_error", "environment 无效。")
        if environment != store.auth_key(auth.key_id).environment:
            raise APIError(400, "validation_error", "设备环境与注册环境不匹配。")
        token = data.get("apns_token")
        if token is not None and (
            not isinstance(token, str)
            or not 32 <= len(token) <= 256
            or re.fullmatch(r"[0-9A-Fa-f]+", token) is None
        ):
            raise APIError(400, "validation_error", "APNs token 无效。")
        timezone = str(data.get("timezone") or "Asia/Shanghai")
        if len(timezone) > 80:
            raise APIError(400, "validation_error", "timezone 无效。")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise APIError(400, "validation_error", "timezone 无效。") from exc
        device = store.update_device(
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
        auth = authenticate(request, b"")
        return {"devices": store.devices(auth.user_id)}

    @app.delete("/v1/devices/{device_id}", status_code=204)
    async def revoke_device(device_id: str, request: Request):
        auth = authenticate(request, b"")
        if not store.revoke_device(auth.user_id, device_id):
            raise APIError(404, "not_found", "没有找到对应设备。")
        return Response(status_code=204)

    @app.get("/v1/preferences")
    async def get_preferences(request: Request):
        auth = authenticate(request, b"")
        prefs = store.preferences(auth.user_id)
        return {
            "daily_frequency": prefs["daily_frequency"],
            "quiet_start": prefs["quiet_start"],
            "quiet_end": prefs["quiet_end"],
        }

    @app.patch("/v1/preferences")
    async def update_preferences(request: Request):
        raw = await limited_json_body(request)
        auth = authenticate(request, raw)
        data = _json(raw)
        current = store.preferences(auth.user_id)
        frequency = data.get("daily_frequency", current["daily_frequency"])
        if not isinstance(frequency, int) or isinstance(frequency, bool):
            raise APIError(400, "validation_error", "daily_frequency 无效。")
        try:
            return store.update_preferences(
                auth.user_id,
                daily_frequency=frequency,
                quiet_start=str(data.get("quiet_start", current["quiet_start"])),
                quiet_end=str(data.get("quiet_end", current["quiet_end"])),
            )
        except ValueError as exc:
            raise APIError(400, "validation_error", str(exc)) from exc

    @app.delete("/v1/account", status_code=204)
    async def delete_account(request: Request):
        raw = await limited_json_body(request)
        auth = authenticate(request, raw, allow_deleting=True)
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
    uvicorn.run(app, host=host, port=port, proxy_headers=True, server_header=False)


def main() -> None:
    run()


def validate_bind_host(settings: AppSettings, host: str) -> None:
    """Production is reachable only through the local Caddy TLS boundary."""
    if settings.production and host not in {"127.0.0.1", "::1"}:
        raise RuntimeError(
            "production App API must bind to loopback behind the HTTPS reverse proxy"
        )
