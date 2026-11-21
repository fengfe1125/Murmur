"""Bounded email endpoints; authenticated binding uses the existing request proof."""

import asyncio

from fastapi import Request

from .app_email import EmailError


def install(app, identity, settings, authenticate, read_body):
    from .app_api import _json, _required_text

    def enabled():
        if not settings.email_registration_enabled:
            raise EmailError("email_unavailable", "邮箱注册尚未开放。", 503)

    @app.post("/v1/email/codes")
    async def send_code(request: Request):
        enabled()
        data = _json(await read_body(request))
        return await asyncio.to_thread(
            identity.issue,
            _required_text(data, "email", 254),
            _required_text(data, "key_id", 180),
            request.client.host if request.client else "unknown",
        )

    @app.post("/v1/email/verify")
    async def verify_code(request: Request):
        enabled()
        data = _json(await read_body(request))
        return await asyncio.to_thread(
            identity.verify,
            _required_text(data, "verification_id"),
            _required_text(data, "code", 6),
            _required_text(data, "key_id", 180),
        )

    @app.post("/v1/account/email/codes")
    async def send_binding_code(request: Request):
        enabled()
        raw = await read_body(request)
        context = await authenticate(request, raw)
        data = _json(raw)
        return await asyncio.to_thread(
            identity.issue,
            _required_text(data, "email", 254),
            context.key_id,
            request.client.host if request.client else "unknown",
            context.user_id,
        )

    @app.post("/v1/account/email/verify")
    async def verify_binding_code(request: Request):
        enabled()
        raw = await read_body(request)
        context = await authenticate(request, raw)
        data = _json(raw)
        return await asyncio.to_thread(
            identity.verify,
            _required_text(data, "verification_id"),
            _required_text(data, "code", 6),
            context.key_id,
            context.user_id,
        )

    @app.post("/v1/account/email")
    async def bind_email(request: Request):
        enabled()
        raw = await read_body(request)
        context = await authenticate(request, raw)
        return await asyncio.to_thread(
            identity.bind,
            _required_text(_json(raw), "email_grant"),
            context.key_id,
            context.user_id,
        )

    @app.get("/v1/account/email")
    async def get_email(request: Request):
        context = await authenticate(request)

        def read():
            with identity.store._lock:
                row = identity.store.conn.execute(
                    "SELECT email FROM app_emails WHERE user_id=?", (context.user_id,)
                ).fetchone()
                return {"email": row[0] if row else None}

        return await asyncio.to_thread(read)
