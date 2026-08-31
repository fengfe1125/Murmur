"""Resolve user-shared music links without trusting share-sheet prose.

Only HTTPS song URLs on the explicitly allowed NetEase hosts are understood.
Short links are followed manually through a bounded, public-address-only chain;
after extracting an ID the catalog is always queried again for authoritative
TrackV1 metadata.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from .app_music import (
    MusicCatalogUnavailable,
    MusicTrackInvalid,
)

MAX_SHARED_TEXT_BYTES = 8 * 1024
MAX_SHARED_URL_CHARS = 2048
MAX_SHORT_LINK_REDIRECTS = 3
NETEASE_LONG_HOST = "music.163.com"
NETEASE_SHORT_HOSTS = frozenset({"y.music.163.com", "163cn.tv"})

_URL = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_SHARE_PUNCTUATION = ").,;!?，。；！？、】》」』”’"


class MusicLinkRejected(MusicTrackInvalid):
    """The text looked like a supported music share but was unsafe/invalid."""

    code = "music_link_rejected"


@dataclass(frozen=True)
class TrackRef:
    provider: str
    track_id: str


class CatalogResolver(Protocol):
    def resolve(self, provider: str, track_id: str) -> dict: ...


AddressResolver = Callable[[str], list[str] | tuple[str, ...]]


def _system_addresses(host: str) -> list[str]:
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MusicLinkRejected("music link host could not be verified") from exc
    return list({answer[4][0] for answer in answers})


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global)


class MusicLink:
    """Parse one shared song and return freshly resolved TrackV1 metadata."""

    def __init__(
        self,
        catalog: CatalogResolver,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        address_resolver: AddressResolver | None = None,
        max_redirects: int = MAX_SHORT_LINK_REDIRECTS,
    ):
        if client is not None and transport is not None:
            raise ValueError("pass client or transport, not both")
        if not 0 <= max_redirects <= MAX_SHORT_LINK_REDIRECTS:
            raise ValueError("max_redirects is outside the safe bound")
        self.catalog = catalog
        self.client = client or httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self.address_resolver = address_resolver or _system_addresses
        self.max_redirects = max_redirects

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _validated_url(url: str, *, allowed_hosts: frozenset[str]) -> tuple[str, str]:
        if len(url) > MAX_SHARED_URL_CHARS:
            raise MusicLinkRejected("music link is too long")
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise MusicLinkRejected("music link port is invalid") from exc
        host = (parsed.hostname or "").rstrip(".").casefold()
        if (
            parsed.scheme.casefold() != "https"
            or not host
            or host not in allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
        ):
            raise MusicLinkRejected("music link host is not allowed")
        return url, host

    def _require_public_dns(self, host: str) -> None:
        addresses = self.address_resolver(host)
        if not addresses or any(not _is_public_address(value) for value in addresses):
            raise MusicLinkRejected("music link resolved to a non-public address")

    @staticmethod
    def _song_ref(url: str) -> TrackRef | None:
        parsed = urlparse(url)
        if (parsed.hostname or "").rstrip(".").casefold() != NETEASE_LONG_HOST:
            return None

        song_url = parsed
        if parsed.fragment:
            fragment = parsed.fragment
            if not fragment.startswith("/"):
                fragment = f"/{fragment}"
            fragment_url = urlparse(fragment)
            if fragment_url.path.rstrip("/") in {"/song", "/m/song"}:
                song_url = fragment_url
        if song_url.path.rstrip("/") not in {"/song", "/m/song"}:
            return None
        ids = parse_qs(song_url.query, keep_blank_values=True).get("id", [])
        if len(ids) != 1 or not ids[0].isdecimal() or not 1 <= len(ids[0]) <= 20:
            raise MusicLinkRejected("music link has an invalid song id")
        return TrackRef("netease", ids[0])

    def _follow_short_link(self, url: str) -> str:
        current = url
        short_hosts = NETEASE_SHORT_HOSTS
        allowed_targets = frozenset({NETEASE_LONG_HOST}) | short_hosts
        for redirect_index in range(self.max_redirects + 1):
            _, host = self._validated_url(current, allowed_hosts=allowed_targets)
            self._require_public_dns(host)
            if host == NETEASE_LONG_HOST:
                return current
            if redirect_index == self.max_redirects:
                raise MusicLinkRejected("music short link redirected too many times")
            try:
                response = self.client.get(
                    current,
                    headers={
                        "accept": "text/html,application/xhtml+xml",
                        "user-agent": "Murmur-Music-Link-PoC/1",
                    },
                    follow_redirects=False,
                )
            except httpx.HTTPError as exc:
                raise MusicCatalogUnavailable("music short link request failed") from exc
            if response.status_code not in {301, 302, 303, 307, 308}:
                raise MusicLinkRejected("music short link did not redirect")
            location = response.headers.get("location")
            if not location:
                raise MusicLinkRejected("music short link redirect is missing")
            current = urljoin(current, location)
        raise MusicLinkRejected("music short link could not be resolved")

    def parse_shared_text(self, text: str) -> dict | None:
        """Return authoritative TrackV1, ``None`` for unrelated text.

        A supported-looking but malformed or unsafe share is rejected.  This
        distinction lets an API ignore normal chat while showing a useful
        validation error for a broken music share.
        """
        if not isinstance(text, str):
            raise MusicLinkRejected("shared music text must be text")
        if len(text.encode("utf-8")) > MAX_SHARED_TEXT_BYTES:
            raise MusicLinkRejected("shared music text is too large")
        candidates = [match.group(0).rstrip(_TRAILING_SHARE_PUNCTUATION) for match in _URL.finditer(text)]
        for candidate in candidates:
            parsed = urlparse(candidate)
            host = (parsed.hostname or "").rstrip(".").casefold()
            if host == NETEASE_LONG_HOST:
                self._validated_url(candidate, allowed_hosts=frozenset({NETEASE_LONG_HOST}))
                ref = self._song_ref(candidate)
                if ref is None:
                    continue
                return self.catalog.resolve(ref.provider, ref.track_id)
            if host in NETEASE_SHORT_HOSTS:
                self._validated_url(candidate, allowed_hosts=NETEASE_SHORT_HOSTS)
                resolved_url = self._follow_short_link(candidate)
                ref = self._song_ref(resolved_url)
                if ref is None:
                    raise MusicLinkRejected("music short link is not a song")
                return self.catalog.resolve(ref.provider, ref.track_id)
        return None


__all__ = [
    "MAX_SHARED_TEXT_BYTES",
    "MusicLink",
    "MusicLinkRejected",
    "TrackRef",
]
