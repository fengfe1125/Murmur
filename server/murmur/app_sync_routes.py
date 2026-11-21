import asyncio

from fastapi import Request
from fastapi.responses import Response

from .app_sync import HistorySync


def install(app, store, authenticate, read_body):
    from .app_api import _json

    sync = HistorySync(store)

    @app.get("/v1/sync/settings")
    async def settings(request: Request):
        context = await authenticate(request)
        return await asyncio.to_thread(sync.settings, context.user_id)

    @app.put("/v1/sync/settings")
    async def update_settings(request: Request):
        raw = await read_body(request)
        context = await authenticate(request, raw)
        data = _json(raw)
        if type(data.get("enabled")) is not bool:
            raise ValueError("同步开关无效。")
        return await asyncio.to_thread(sync.settings, context.user_id, data["enabled"])

    @app.get("/v1/sync/{kind}/records")
    async def pull(request: Request, kind: str, cursor: int = 0):
        context = await authenticate(request)
        return await asyncio.to_thread(sync.pull, context.user_id, kind, cursor)

    @app.post("/v1/sync/{kind}/records")
    async def upload(request: Request, kind: str):
        raw = await read_body(request)
        context = await authenticate(request, raw)
        data = _json(raw)
        return await asyncio.to_thread(
            sync.upload, context.user_id, kind, data.get("generation"), data.get("records")
        )

    @app.delete("/v1/sync/{kind}/records")
    async def clear(request: Request, kind: str):
        raw = await read_body(request)
        context = await authenticate(request, raw)
        return await asyncio.to_thread(
            sync.clear, context.user_id, kind, _json(raw).get("generation")
        )

    @app.put("/v1/sync/{kind}/preview/{record_id}")
    async def upload_preview(request: Request, kind: str, record_id: str, generation: int):
        raw = await read_body(request)
        context = await authenticate(request, raw)
        return await asyncio.to_thread(
            sync.upload_preview, context.user_id, kind, generation, record_id, raw
        )

    @app.get("/v1/sync/{kind}/preview/{record_id}")
    async def preview(request: Request, kind: str, record_id: str):
        context = await authenticate(request)
        data = await asyncio.to_thread(sync.preview, context.user_id, kind, record_id)
        return Response(
            content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )
