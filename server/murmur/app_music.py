"""Audius-backed music cards for the first-party App.

OAuth is intentionally absent from this module: the iOS client owns its PKCE
session and tokens.  The server accepts one bounded metadata snapshot, resolves
public Audius catalog data, and may ask the chat model for a search query when
the user explicitly asks Murmur to play something.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .config import Config

TRACK_KEYS = frozenset({
    "version", "provider", "track_id", "title", "artists", "artwork_url",
    "canonical_url", "duration_seconds", "explicit",
})
TRACK_REQUIRED = frozenset({
    "version", "provider", "track_id", "title", "artists", "canonical_url",
})
MAX_MUSIC_TRACK_BYTES = 8 * 1024


class MusicError(RuntimeError):
    code = "music_error"
    retryable = False


class MusicTrackInvalid(MusicError):
    code = "invalid_music_track"


class MusicTrackUnavailable(MusicError):
    code = "music_track_unavailable"


class MusicCatalogUnavailable(MusicError):
    code = "music_catalog_unavailable"
    retryable = True


class MusicPlanningFailed(MusicError):
    code = "music_planning_failed"
    retryable = True


def _bounded_text(value, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise MusicTrackInvalid(f"{name} must be text")
    clean = " ".join(value.split())
    if not 1 <= len(clean) <= maximum:
        raise MusicTrackInvalid(f"{name} is invalid")
    return clean


def _https_url(value, name: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    clean = _bounded_text(value, name, 2048)
    parsed = urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise MusicTrackInvalid(f"{name} must be an HTTPS URL")
    return clean


def normalize_music_track(value) -> dict:
    """Validate and canonicalise the shared TrackV1 wire object."""
    if not isinstance(value, dict) or set(value) - TRACK_KEYS:
        raise MusicTrackInvalid("music_track has unknown fields")
    if not TRACK_REQUIRED.issubset(value):
        raise MusicTrackInvalid("music_track is missing fields")
    if value.get("version") != 1 or value.get("provider") != "audius":
        raise MusicTrackInvalid("unsupported music_track version or provider")
    artists = value.get("artists")
    if not isinstance(artists, list) or not 1 <= len(artists) <= 8:
        raise MusicTrackInvalid("artists is invalid")
    clean_artists = [_bounded_text(item, "artists", 120) for item in artists]
    if len(set(clean_artists)) != len(clean_artists):
        raise MusicTrackInvalid("artists contains duplicates")
    duration = value.get("duration_seconds")
    if duration is not None and (
        not isinstance(duration, int) or isinstance(duration, bool)
        or not 1 <= duration <= 24 * 60 * 60
    ):
        raise MusicTrackInvalid("duration_seconds is invalid")
    explicit = value.get("explicit")
    if explicit is not None and not isinstance(explicit, bool):
        raise MusicTrackInvalid("explicit is invalid")
    clean = {
        "version": 1,
        "provider": "audius",
        "track_id": _bounded_text(value.get("track_id"), "track_id", 128),
        "title": _bounded_text(value.get("title"), "title", 200),
        "artists": clean_artists,
        "canonical_url": _https_url(
            value.get("canonical_url"), "canonical_url", required=True
        ),
    }
    if artwork := _https_url(value.get("artwork_url"), "artwork_url", required=False):
        clean["artwork_url"] = artwork
    if duration is not None:
        clean["duration_seconds"] = duration
    if explicit is not None:
        clean["explicit"] = explicit
    return clean


def parse_music_track(raw) -> tuple[dict | None, str | None]:
    if raw in (None, ""):
        return None, None
    if isinstance(raw, bytes):
        encoded = raw
    else:
        encoded = str(raw).encode("utf-8")
    if len(encoded) > MAX_MUSIC_TRACK_BYTES:
        raise MusicTrackInvalid("music_track is too large")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MusicTrackInvalid("music_track must be JSON") from exc
    clean = normalize_music_track(value)
    canonical = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return clean, canonical


def load_music_track(raw: str | None) -> dict | None:
    """Read a stored TrackV1 back.  Stored rows are already normalised, so a
    row that no longer validates is treated as absent rather than fatal: one
    bad historical row must not take down a whole conversation's context."""
    if not raw:
        return None
    try:
        return normalize_music_track(json.loads(raw))
    except (TypeError, json.JSONDecodeError, MusicTrackInvalid):
        return None


def normalize_playback_track(value) -> dict:
    """Validate the smaller snapshot used by discrete playback reporting."""
    allowed = {"provider", "track_id", "title", "artists", "duration_seconds"}
    required = {"provider", "track_id", "title", "artists"}
    if not isinstance(value, dict) or set(value) - allowed or not required.issubset(value):
        raise MusicTrackInvalid("playback track is invalid")
    if value.get("provider") != "audius":
        raise MusicTrackInvalid("unsupported playback provider")
    artists = value.get("artists")
    if not isinstance(artists, list) or not 1 <= len(artists) <= 8:
        raise MusicTrackInvalid("artists is invalid")
    clean = {
        "provider": "audius",
        "track_id": _bounded_text(value.get("track_id"), "track_id", 128),
        "title": _bounded_text(value.get("title"), "title", 200),
        "artists": [_bounded_text(item, "artists", 120) for item in artists],
    }
    duration = value.get("duration_seconds")
    if duration is not None:
        if (not isinstance(duration, int) or isinstance(duration, bool)
                or not 1 <= duration <= 24 * 60 * 60):
            raise MusicTrackInvalid("duration_seconds is invalid")
        clean["duration_seconds"] = duration
    return clean


def music_fallback_text(track: dict) -> str:
    """一条只有文字的客户端也读得懂的那一行。

    卡片是 bubble 上多出来的一个字段，所以每一张卡片都必须同时带着这行字：
    旧客户端把字段丢掉之后，剩下的仍然是一条说得通的消息。
    """
    artists = ", ".join(track["artists"])
    return f"🎵 {track['title']} — {artists}\n{track['canonical_url']}"


def music_prompt_line(track: dict, *, actor: str) -> str:
    artists = "、".join(track["artists"])
    return f"[{actor}分享的音乐｜Audius] {track['title']} — {artists}"


class Catalog(Protocol):
    def get_track(self, track_id: str) -> dict: ...
    def search_tracks(self, query: str, limit: int = 5) -> list[dict]: ...


class AudiusCatalogClient:
    """Small, bounded client for public catalog metadata only."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.audius.co/v1",
        timeout: float = 5.0,
        client: httpx.Client | None = None,
        cache_ttl_seconds: float = 6 * 60 * 60,
        negative_ttl_seconds: float = 5 * 60,
        max_cache_entries: int = 512,
        search_ttl_seconds: float = 5 * 60,
        max_search_entries: int = 256,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self.cache_ttl = cache_ttl_seconds
        self.negative_ttl = negative_ttl_seconds
        self.max_cache_entries = max_cache_entries
        self.search_ttl = search_ttl_seconds
        self.max_search_entries = max_search_entries
        self._cache: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
        self._search_cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _cached(self, track_id: str):
        now = time.monotonic()
        with self._lock:
            item = self._cache.get(track_id)
            if item is None:
                return False, None
            expires, value = item
            if expires <= now:
                del self._cache[track_id]
                return False, None
            self._cache.move_to_end(track_id)
            return True, value

    def _remember(self, track_id: str, value: dict | None) -> None:
        ttl = self.cache_ttl if value is not None else self.negative_ttl
        with self._lock:
            self._cache[track_id] = (time.monotonic() + ttl, value)
            self._cache.move_to_end(track_id)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)

    def _get(self, path: str, params: dict | None = None) -> object:
        try:
            response = self.client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"x-api-key": self.api_key, "accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise MusicCatalogUnavailable("Audius request failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise MusicCatalogUnavailable("Audius is temporarily unavailable")
        if response.status_code >= 400:
            raise MusicTrackUnavailable("Audius rejected the track")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MusicCatalogUnavailable("Audius returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MusicCatalogUnavailable("Audius returned an invalid payload")
        return payload.get("data")

    @staticmethod
    def _api_track_playable(value) -> bool:
        return bool(
            isinstance(value, dict)
            and not value.get("is_delete", False)
            and not value.get("is_unlisted", False)
            and value.get("is_streamable", True) is not False
        )

    @staticmethod
    def _normalise_api_track(value) -> dict:
        if not isinstance(value, dict):
            raise MusicCatalogUnavailable("Audius returned an invalid track")
        user = value.get("user") if isinstance(value.get("user"), dict) else {}
        artist = value.get("artist_name") or user.get("name")
        artwork = value.get("artwork") if isinstance(value.get("artwork"), dict) else {}
        artwork_url = (
            artwork.get("_1000x1000") or artwork.get("_480x480")
            or artwork.get("_150x150")
        )
        permalink = value.get("permalink")
        if isinstance(permalink, str) and permalink.startswith("/"):
            permalink = f"https://audius.co{permalink}"
        raw = {
            "version": 1,
            "provider": "audius",
            "track_id": value.get("id"),
            "title": value.get("title"),
            "artists": [artist] if artist else [],
            "canonical_url": permalink,
        }
        if artwork_url:
            raw["artwork_url"] = artwork_url
        duration = value.get("duration")
        if isinstance(duration, int) and not isinstance(duration, bool):
            raw["duration_seconds"] = duration
        if isinstance(value.get("is_explicit"), bool):
            raw["explicit"] = value["is_explicit"]
        try:
            return normalize_music_track(raw)
        except MusicTrackInvalid as exc:
            raise MusicCatalogUnavailable("Audius returned incomplete track metadata") from exc

    def get_track(self, track_id: str) -> dict:
        track_id = _bounded_text(track_id, "track_id", 128)
        found, cached = self._cached(track_id)
        if found:
            if cached is None:
                raise MusicTrackUnavailable("track is unavailable")
            return dict(cached)
        data = self._get(f"/tracks/{track_id}")
        if data is None:
            self._remember(track_id, None)
            raise MusicTrackUnavailable("track is unavailable")
        # Audius endpoints have returned both one object and one-item arrays.
        if isinstance(data, list):
            data = data[0] if data else None
        if data is None:
            self._remember(track_id, None)
            raise MusicTrackUnavailable("track is unavailable")
        if not self._api_track_playable(data):
            self._remember(track_id, None)
            raise MusicTrackUnavailable("track is unavailable")
        track = self._normalise_api_track(data)
        self._remember(track_id, track)
        return track

    def search_tracks(self, query: str, limit: int = 5) -> list[dict]:
        query = _bounded_text(query, "query", 120)
        limit = max(1, min(int(limit), 5))
        cache_key = " ".join(query.casefold().split())
        now = time.monotonic()
        with self._lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                self._search_cache.move_to_end(cache_key)
                return [dict(item) for item in cached[1][:limit]]
            if cached is not None:
                del self._search_cache[cache_key]
        data = self._get("/tracks/search", {"query": query, "limit": limit})
        if not isinstance(data, list):
            raise MusicCatalogUnavailable("Audius returned invalid search results")
        tracks: list[dict] = []
        for item in data[:limit]:
            if not self._api_track_playable(item):
                continue
            try:
                track = self._normalise_api_track(item)
            except MusicCatalogUnavailable:
                continue
            tracks.append(track)
            self._remember(track["track_id"], track)
        with self._lock:
            self._search_cache[cache_key] = (
                time.monotonic() + self.search_ttl, [dict(item) for item in tracks]
            )
            self._search_cache.move_to_end(cache_key)
            while len(self._search_cache) > self.max_search_entries:
                self._search_cache.popitem(last=False)
        return tracks


MUSIC_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "requested": {"type": "boolean"},
        "mode": {"type": "string", "enum": ["exact", "discover"]},
        "query": {"type": ["string", "null"], "maxLength": 120},
        "title": {"type": ["string", "null"], "maxLength": 200},
        "artist": {"type": ["string", "null"], "maxLength": 120},
    },
    "required": ["requested", "mode", "query", "title", "artist"],
    "additionalProperties": False,
}

_MUSIC_WORD = re.compile(r"歌|音乐|曲|听点|听首|放首|播首|来首|点歌")


@dataclass(frozen=True)
class MusicPlan:
    requested: bool
    mode: str
    query: str | None
    title: str | None = None
    artist: str | None = None


class AppMusicPlanner:
    """App-only structured classifier; it never chooses IDs or URLs."""

    def __init__(self, cfg: Config, client=None):
        self.cfg = cfg
        self.client = client

    @staticmethod
    def might_be_request(text: str | None) -> bool:
        return bool(text and _MUSIC_WORD.search(text))

    def plan(self, text: str) -> MusicPlan:
        if not self.might_be_request(text):
            return MusicPlan(False, "discover", None)
        if self.client is None:
            from .engine import _client

            client = _client(self.cfg)
        else:
            client = self.client
        try:
            response = client.chat.completions.create(
                model=self.cfg.model,
                max_tokens=160,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "判断用户是否明确要求你现在推荐或播放一首音乐。"
                            "只是谈到、回忆或分享音乐不算。点名歌曲时用 exact 并分别"
                            "提取 title/artist；没有点名时用 discover。query 给出适合 "
                            "Audius catalog 搜索的简短关键词。不要输出 ID、URL 或解释。"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "music_request",
                        "strict": True,
                        "schema": MUSIC_REQUEST_SCHEMA,
                    },
                },
            )
            raw = response.choices[0].message.content or ""
            data = json.loads(raw)
        except Exception as exc:
            raise MusicPlanningFailed("music request planning failed") from exc
        expected = {"requested", "mode", "query", "title", "artist"}
        if not isinstance(data, dict) or set(data) != expected:
            raise MusicPlanningFailed("music request planning returned invalid data")
        requested, mode, query = data["requested"], data["mode"], data["query"]
        title, artist = data["title"], data["artist"]
        if not isinstance(requested, bool) or mode not in {"exact", "discover"}:
            raise MusicPlanningFailed("requested is invalid")
        if requested:
            try:
                query = _bounded_text(query, "query", 120)
            except MusicTrackInvalid as exc:
                raise MusicPlanningFailed("query is invalid") from exc
            if mode == "exact":
                try:
                    title = _bounded_text(title, "title", 200)
                    artist = (
                        _bounded_text(artist, "artist", 120)
                        if artist is not None else None
                    )
                except MusicTrackInvalid as exc:
                    raise MusicPlanningFailed("exact music identity is invalid") from exc
            elif title is not None or artist is not None:
                raise MusicPlanningFailed("discover must not name an exact track")
            return MusicPlan(True, mode, query, title, artist)
        if query is not None or title is not None or artist is not None:
            raise MusicPlanningFailed("non-request fields must be null")
        return MusicPlan(False, mode, None)


_NORMALISE_MATCH = re.compile(r"[^\w]+", re.UNICODE)


def choose_music_track(plan: MusicPlan, tracks: list[dict]) -> dict | None:
    """Choose deterministically; exact requests never degrade to a different song."""
    if not plan.requested or not tracks:
        return None
    if plan.mode == "discover":
        return tracks[0]

    def normal(value: str) -> str:
        return _NORMALISE_MATCH.sub("", value.casefold())

    wanted_title = normal(plan.title or "")
    wanted_artist = normal(plan.artist or "")
    for track in tracks:
        if normal(track.get("title", "")) != wanted_title:
            continue
        if wanted_artist and not any(
            normal(name) == wanted_artist for name in track.get("artists", [])
        ):
            continue
        return track
    return None


# 点了歌却什么都没找到时说的那一句。固定文案，不让模型即兴发挥：
# 这一句的意义是"我没找到"，模型一旦自由发挥就会开始描述一首不存在的歌。
MUSIC_NOT_FOUND_LINE = "我在 Audius 上没找到合适的，你说个名字我再找找。"


@dataclass(frozen=True)
class MusicChoice:
    """这一轮点歌的结果。

    `requested` 和 `track` 是两件事：他确实点了歌但没找到，要说那一句固定
    的说明；他压根没点歌，就什么都不做。两种情况的 `track` 都是 None。
    """

    requested: bool
    track: dict | None = None

    @property
    def not_found(self) -> bool:
        return self.requested and self.track is None


class AppMusic:
    """一条 moment 的音乐，在一个地方定完。

    两个方向都走这里：他附上的那首歌，和他开口要的那首歌。一条 moment 不会
    两者都是——递过来一首歌不等于在要另一首。
    """

    def __init__(self, catalog: Catalog, planner: AppMusicPlanner):
        self.catalog = catalog
        self.planner = planner

    def verify_shared(self, raw: str | None) -> dict | None:
        """按 track_id 重新去公开 catalog 核验他附上的那首歌。

        客户端提交的标题和封面只是发送当时的快照，一律以服务端这次查回来的
        为准。查不到就退回那份快照：歌可能已经下架，或者创作者关掉了 API
        access，但这仍然是他发出的一条消息，不该因为一首歌整条失败。
        """
        snapshot = load_music_track(raw)
        if snapshot is None:
            return None
        try:
            return self.catalog.get_track(snapshot["track_id"])
        except MusicError:
            return snapshot

    def choose_for(self, text: str | None) -> MusicChoice:
        """他明确开口要的那首歌，没有就是没有。

        每一步都可以什么都不返回：门禁没过、规划失败、搜索挂了、结果里没有
        一首对得上的。任何一步落空都不许伪造一张卡片出来。
        """
        if not self.planner.might_be_request(text):
            return MusicChoice(False)
        try:
            plan = self.planner.plan(text)
        except MusicPlanningFailed:
            return MusicChoice(False)
        if not plan.requested or not plan.query:
            return MusicChoice(False)
        try:
            tracks = self.catalog.search_tracks(plan.query, limit=5)
        except MusicError:
            return MusicChoice(True)
        return MusicChoice(True, choose_music_track(plan, tracks))


def playback_prompt_line(playback: dict | None) -> str | None:
    """他此刻正在听什么，说成一句话。

    只有离散状态，没有进度条：这里能说的仅仅是"正在放"还是"停在那儿"。
    """
    if not playback:
        return None
    track = playback.get("track")
    if not isinstance(track, dict) or not track.get("title"):
        return None
    artists = "、".join(track.get("artists") or []) or "未知艺人"
    state = playback.get("state")
    if state == "paused":
        return f"（他把 {track['title']} — {artists} 暂停在那儿了。）"
    if state in {"started", "resumed"}:
        return f"（他正在听 {track['title']} — {artists}。）"
    return None
