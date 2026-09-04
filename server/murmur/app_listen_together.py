"""Ephemeral listen-together rooms for the first-party App.

The public objects in this module are deliberately narrower than a NetEase
room session.  External room identifiers, account cookies, tokens and protocol
sequence numbers never cross the manager boundary.  The experimental adapter
also has no default endpoint: a reviewed transport must be injected explicitly
before private-protocol traffic is possible.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import stat
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse

ROOM_STATES = frozenset({
    "creating", "waiting_for_user", "connected", "syncing", "ended", "failed",
})
ROOM_COMMANDS = frozenset({
    "play", "pause", "resume", "previous", "next", "play_track",
})
COMMAND_STATUSES = frozenset({"accepted", "synchronized", "failed"})
_TRACK_KEYS = frozenset({
    "version", "provider", "track_id", "title", "artists", "artwork_url",
    "canonical_url", "duration_seconds", "explicit",
})
_TRACK_REQUIRED = frozenset({
    "version", "provider", "track_id", "title", "artists", "canonical_url",
})
_TERMINAL_STATES = frozenset({"ended", "failed"})


@dataclass(frozen=True)
class RoomChatIntent:
    """A deliberately small, high-confidence chat control vocabulary.

    Ambiguous music language is left to the normal conversation/music planner.
    Only phrases whose requested side effect is clear reach the room manager.
    """

    action: str


_CHAT_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("close", re.compile(r"^(?:请|麻烦|帮我)?(?:结束|关闭|退出|停止)(?:这次)?一起听(?:吧|了|一下)?[。！!,.， ]*$")),
    ("pause", re.compile(r"^(?:请|麻烦|帮我)?(?:先)?暂停(?:播放)?(?:一下|吧)?[。！!,.， ]*$")),
    ("resume", re.compile(r"^(?:请|麻烦|帮我)?(?:继续|恢复)(?:播放|放歌)?(?:吧|一下)?[。！!,.， ]*$")),
    ("next", re.compile(r"^(?:请|麻烦|帮我)?(?:切到|换到|播放)?下一首(?:歌)?(?:吧|一下)?[。！!,.， ]*$")),
    ("previous", re.compile(r"^(?:请|麻烦|帮我)?(?:切到|换到|播放)?上一首(?:歌)?(?:吧|一下)?[。！!,.， ]*$")),
)


def parse_room_chat_intent(text: str | None) -> RoomChatIntent | None:
    """Map only unambiguous natural-language room controls.

    A sentence like "我刚才暂停了一下" intentionally matches nothing.  It
    belongs in conversation rather than silently changing remote playback.
    """

    if not isinstance(text, str):
        return None
    clean = " ".join(text.strip().split())
    if not clean or len(clean) > 80:
        return None
    for action, pattern in _CHAT_COMMAND_PATTERNS:
        if pattern.fullmatch(clean):
            return RoomChatIntent(action)
    return None


class ListenTogetherError(RuntimeError):
    code = "listen_together_error"
    retryable = False


class RoomInvalid(ListenTogetherError):
    code = "invalid_room_request"


class RoomNotFound(ListenTogetherError):
    code = "room_not_found"


class RoomConflict(ListenTogetherError):
    code = "room_conflict"


class RoomIdempotencyConflict(RoomConflict):
    code = "room_idempotency_conflict"


class RoomExperimentDisabled(ListenTogetherError):
    code = "room_experiment_disabled"


class RoomProtocolUnsupported(ListenTogetherError):
    code = "room_protocol_unsupported"


class RoomAuthenticationFailed(ListenTogetherError):
    code = "room_authentication_failed"


class RoomTransportUnavailable(ListenTogetherError):
    code = "room_transport_unavailable"
    retryable = True


class RoomConfirmationTimeout(ListenTogetherError):
    code = "room_confirmation_timeout"
    retryable = True


class RoomOutOfOrder(ListenTogetherError):
    code = "room_out_of_order"
    retryable = True


class RoomUnreachable(ListenTogetherError):
    code = "room_unreachable"


class RoomIPCUnavailable(ListenTogetherError):
    code = "room_ipc_unavailable"
    retryable = True


class RoomRemoteError(ListenTogetherError):
    """A typed room error returned by the worker-side IPC server."""

    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _bounded_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise RoomInvalid(f"{name} must be text")
    clean = " ".join(value.split())
    if not 1 <= len(clean) <= maximum:
        raise RoomInvalid(f"{name} is invalid")
    return clean


def _https_url(value: object, name: str) -> str:
    clean = _bounded_text(value, name, 2048)
    parsed = urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise RoomInvalid(f"{name} must be an HTTPS URL")
    return clean


def _public_track(value: object) -> dict:
    """Validate TrackV1 without importing a provider implementation.

    Room code accepts only the two providers Murmur knows about.  It copies the
    whitelisted fields so a transport cannot smuggle stream URLs or credentials
    into a room snapshot.
    """
    if not isinstance(value, Mapping):
        raise RoomInvalid("current_track must be an object")
    if set(value) - _TRACK_KEYS or not _TRACK_REQUIRED.issubset(value):
        raise RoomInvalid("current_track has an invalid shape")
    if value.get("version") != 1 or value.get("provider") not in {"audius", "netease"}:
        raise RoomInvalid("current_track has an unsupported version or provider")
    artists = value.get("artists")
    if not isinstance(artists, list) or not 1 <= len(artists) <= 8:
        raise RoomInvalid("artists is invalid")
    clean_artists = [_bounded_text(item, "artists", 120) for item in artists]
    if len(set(clean_artists)) != len(clean_artists):
        raise RoomInvalid("artists contains duplicates")
    clean = {
        "version": 1,
        "provider": value["provider"],
        "track_id": _bounded_text(value.get("track_id"), "track_id", 128),
        "title": _bounded_text(value.get("title"), "title", 200),
        "artists": clean_artists,
        "canonical_url": _https_url(value.get("canonical_url"), "canonical_url"),
    }
    artwork = value.get("artwork_url")
    if artwork is not None:
        clean["artwork_url"] = _https_url(artwork, "artwork_url")
    duration = value.get("duration_seconds")
    if duration is not None:
        if (
            not isinstance(duration, int) or isinstance(duration, bool)
            or not 1 <= duration <= 24 * 60 * 60
        ):
            raise RoomInvalid("duration_seconds is invalid")
        clean["duration_seconds"] = duration
    explicit = value.get("explicit")
    if explicit is not None:
        if not isinstance(explicit, bool):
            raise RoomInvalid("explicit is invalid")
        clean["explicit"] = explicit
    return clean


@dataclass(frozen=True)
class RoomSnapshotV1:
    room_handle: str
    state: str
    current_track: dict | None
    user_joined: bool
    pending_command: str | None
    invite_url: str | None
    updated_at: str
    error_code: str | None = None
    playback_state: str = "unknown"
    version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if self.state not in ROOM_STATES:
            raise RoomInvalid("invalid public room state")
        _bounded_text(self.room_handle, "room_handle", 200)
        if self.pending_command is not None and self.pending_command not in ROOM_COMMANDS:
            raise RoomInvalid("invalid pending command")
        if self.invite_url is not None:
            _https_url(self.invite_url, "invite_url")
        if self.current_track is not None:
            object.__setattr__(self, "current_track", _public_track(self.current_track))
        if not isinstance(self.user_joined, bool):
            raise RoomInvalid("user_joined must be boolean")
        if self.playback_state not in {"playing", "paused", "unknown"}:
            raise RoomInvalid("invalid public playback state")

    def to_wire(self) -> dict:
        """Return the complete, stable public shape and nothing transport-specific."""
        return {
            "version": 1,
            "room_handle": self.room_handle,
            "state": self.state,
            "current_track": dict(self.current_track) if self.current_track else None,
            "user_joined": self.user_joined,
            "pending_command": self.pending_command,
            "invite_url": self.invite_url,
            "updated_at": self.updated_at,
            "error_code": self.error_code,
            "playback_state": self.playback_state,
        }


@dataclass(frozen=True)
class CommandResultV1:
    status: str
    room: RoomSnapshotV1

    def __post_init__(self) -> None:
        if self.status not in COMMAND_STATUSES:
            raise RoomInvalid("invalid command status")

    def to_wire(self) -> dict:
        return {"status": self.status, "room": self.room.to_wire()}


@dataclass(frozen=True)
class AdapterRoomState:
    """Adapter-to-manager value; ``room_ref`` is never serialized publicly."""

    room_ref: str
    lifecycle: str
    current_track: dict | None
    user_joined: bool
    invite_url: str | None
    playback_state: str = "unknown"


@dataclass(frozen=True)
class AdapterCommandResult:
    status: str
    state: AdapterRoomState
    error_code: str | None = None


class ListenTogetherRoomAdapter(Protocol):
    def create(self, *, initial_track: dict, idempotency_key: str) -> AdapterRoomState: ...

    def snapshot(self, *, room_ref: str) -> AdapterRoomState: ...

    def command(
        self,
        *,
        room_ref: str,
        command: str,
        track: dict | None,
        idempotency_key: str,
    ) -> AdapterCommandResult: ...

    def heartbeat(self, *, room_ref: str) -> AdapterRoomState: ...

    def close(self, *, room_ref: str) -> None: ...


@dataclass
class _ActiveRoom:
    user_id: str
    handle: str
    adapter_state: AdapterRoomState
    state: str
    pending_command: str | None
    error_code: str | None
    updated_at: str
    last_confirmed: float
    last_heartbeat: float


@dataclass(frozen=True)
class _IdempotentResult:
    fingerprint: tuple
    value: RoomSnapshotV1 | CommandResultV1


class ListenTogetherRoomManager:
    """Own one process-local room per user and no durable room state."""

    def __init__(
        self,
        adapter: ListenTogetherRoomAdapter,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        handle_factory: Callable[[], str] | None = None,
        heartbeat_interval_seconds: float = 15.0,
        unreachable_timeout_seconds: float = 10 * 60,
        max_idempotency_entries: int = 1024,
    ):
        if heartbeat_interval_seconds <= 0 or unreachable_timeout_seconds <= 0:
            raise ValueError("room timing values must be positive")
        self.adapter = adapter
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._handle_factory = handle_factory or (lambda: secrets.token_urlsafe(18))
        self.heartbeat_interval = heartbeat_interval_seconds
        self.unreachable_timeout = unreachable_timeout_seconds
        self.max_idempotency_entries = max_idempotency_entries
        self._rooms_by_user: dict[str, _ActiveRoom] = {}
        self._users_by_handle: dict[str, str] = {}
        self._idempotency: dict[tuple[str, str], _IdempotentResult] = {}
        self._lock = threading.RLock()
        self._closed = False

    def _now_text(self) -> str:
        value = self._wall_clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _identity(value: object, name: str) -> str:
        return _bounded_text(value, name, 200)

    def _require_open(self) -> None:
        if self._closed:
            raise RoomExperimentDisabled("room manager is shut down")

    def _remember(
        self,
        user_id: str,
        key: str,
        fingerprint: tuple,
        value: RoomSnapshotV1 | CommandResultV1,
    ) -> None:
        self._idempotency[(user_id, key)] = _IdempotentResult(fingerprint, value)
        while len(self._idempotency) > self.max_idempotency_entries:
            del self._idempotency[next(iter(self._idempotency))]

    def _replay(self, user_id: str, key: str, fingerprint: tuple):
        item = self._idempotency.get((user_id, key))
        if item is None:
            return None
        if item.fingerprint != fingerprint:
            raise RoomIdempotencyConflict("idempotency key was used for another room request")
        return item.value

    @staticmethod
    def _adapter_state(value: AdapterRoomState) -> AdapterRoomState:
        if not isinstance(value, AdapterRoomState):
            raise RoomInvalid("adapter returned an invalid room state")
        room_ref = _bounded_text(value.room_ref, "adapter room ref", 512)
        if value.lifecycle not in {"waiting_for_user", "connected", "ended", "failed"}:
            raise RoomInvalid("adapter returned an invalid lifecycle")
        if value.playback_state not in {"playing", "paused", "unknown"}:
            raise RoomInvalid("adapter returned an invalid playback state")
        track = _public_track(value.current_track) if value.current_track is not None else None
        invite = _https_url(value.invite_url, "invite_url") if value.invite_url else None
        return AdapterRoomState(
            room_ref, value.lifecycle, track, bool(value.user_joined), invite,
            value.playback_state,
        )

    def _snapshot(self, room: _ActiveRoom) -> RoomSnapshotV1:
        state = room.state
        if state not in _TERMINAL_STATES and room.pending_command is None:
            state = room.adapter_state.lifecycle
        return RoomSnapshotV1(
            room_handle=room.handle,
            state=state,
            current_track=room.adapter_state.current_track,
            user_joined=room.adapter_state.user_joined,
            pending_command=room.pending_command,
            invite_url=room.adapter_state.invite_url,
            updated_at=room.updated_at,
            error_code=room.error_code,
            playback_state=room.adapter_state.playback_state,
        )

    def _apply_confirmed(self, room: _ActiveRoom, state: AdapterRoomState) -> None:
        room.adapter_state = self._adapter_state(state)
        room.state = room.adapter_state.lifecycle
        room.pending_command = None
        room.error_code = None
        room.updated_at = self._now_text()
        room.last_confirmed = self._clock()

    def _terminate(self, room: _ActiveRoom, *, state: str, error_code: str | None) -> None:
        room.state = state
        room.pending_command = None
        room.error_code = error_code
        room.updated_at = self._now_text()
        self._rooms_by_user.pop(room.user_id, None)
        self._users_by_handle.pop(room.handle, None)

    def create(
        self, *, user_id: str, initial_track: dict, idempotency_key: str
    ) -> RoomSnapshotV1:
        with self._lock:
            self._require_open()
            user_id = self._identity(user_id, "user_id")
            key = self._identity(idempotency_key, "idempotency_key")
            track = _public_track(initial_track)
            fingerprint = ("create", tuple(_freeze(track)))
            if replay := self._replay(user_id, key, fingerprint):
                assert isinstance(replay, RoomSnapshotV1)
                return replay
            existing = self._rooms_by_user.get(user_id)
            if existing is not None and existing.state not in _TERMINAL_STATES:
                raise RoomConflict("user already has an active room")
            adapter_state = self._adapter_state(
                self.adapter.create(initial_track=track, idempotency_key=key)
            )
            now = self._clock()
            room = _ActiveRoom(
                user_id=user_id,
                handle=self._identity(self._handle_factory(), "room_handle"),
                adapter_state=adapter_state,
                state=adapter_state.lifecycle,
                pending_command=None,
                error_code=None,
                updated_at=self._now_text(),
                last_confirmed=now,
                last_heartbeat=now,
            )
            self._rooms_by_user[user_id] = room
            self._users_by_handle[room.handle] = user_id
            result = self._snapshot(room)
            self._remember(user_id, key, fingerprint, result)
            return result

    def current(self, *, user_id: str, refresh: bool = True) -> RoomSnapshotV1 | None:
        with self._lock:
            self._require_open()
            user_id = self._identity(user_id, "user_id")
            room = self._rooms_by_user.get(user_id)
            if room is None:
                return None
            if refresh:
                self._refresh(room, heartbeat=False)
            return self._snapshot(room)

    def command(
        self,
        *,
        user_id: str,
        room_handle: str,
        command: str,
        idempotency_key: str,
        track: dict | None = None,
    ) -> CommandResultV1:
        with self._lock:
            self._require_open()
            user_id = self._identity(user_id, "user_id")
            handle = self._identity(room_handle, "room_handle")
            key = self._identity(idempotency_key, "idempotency_key")
            if command not in ROOM_COMMANDS:
                raise RoomInvalid("unsupported room command")
            if command == "play_track":
                if track is None:
                    raise RoomInvalid("play_track requires a track")
                clean_track = _public_track(track)
            else:
                if track is not None:
                    raise RoomInvalid("only play_track accepts a track")
                clean_track = None
            fingerprint = ("command", handle, command, tuple(_freeze(clean_track)))
            if replay := self._replay(user_id, key, fingerprint):
                assert isinstance(replay, CommandResultV1)
                return replay
            room = self._room_owned_by(user_id, handle)
            room.state = "syncing"
            room.pending_command = command
            room.error_code = None
            room.updated_at = self._now_text()
            try:
                adapter_result = self.adapter.command(
                    room_ref=room.adapter_state.room_ref,
                    command=command,
                    track=clean_track,
                    idempotency_key=key,
                )
                if (
                    not isinstance(adapter_result, AdapterCommandResult)
                    or adapter_result.status not in COMMAND_STATUSES
                ):
                    raise RoomInvalid("adapter returned an invalid command result")
                room.adapter_state = self._adapter_state(adapter_result.state)
                room.updated_at = self._now_text()
                if adapter_result.status == "synchronized":
                    room.state = room.adapter_state.lifecycle
                    room.pending_command = None
                    room.error_code = None
                    room.last_confirmed = self._clock()
                elif adapter_result.status == "accepted":
                    room.state = "syncing"
                else:
                    # A rejected command is not a failed room. Keep the last
                    # confirmed lifecycle and playback state so the client can
                    # offer another control without manufacturing a new room.
                    room.state = room.adapter_state.lifecycle
                    room.pending_command = None
                    room.error_code = adapter_result.error_code or "room_command_failed"
                result = CommandResultV1(adapter_result.status, self._snapshot(room))
            except RoomAuthenticationFailed as exc:
                self._best_effort_close(room)
                self._terminate(room, state="failed", error_code=exc.code)
                result = CommandResultV1("failed", self._snapshot(room))
            except (RoomConfirmationTimeout, RoomOutOfOrder) as exc:
                room.state = "syncing"
                room.error_code = exc.code
                room.updated_at = self._now_text()
                result = CommandResultV1("failed", self._snapshot(room))
            except RoomUnreachable as exc:
                self._best_effort_close(room)
                self._terminate(room, state="ended", error_code=exc.code)
                result = CommandResultV1("failed", self._snapshot(room))
            except RoomTransportUnavailable as exc:
                if self._clock() - room.last_confirmed >= self.unreachable_timeout:
                    self._best_effort_close(room)
                    self._terminate(room, state="ended", error_code=RoomUnreachable.code)
                else:
                    room.state = "syncing"
                    room.error_code = exc.code
                    room.updated_at = self._now_text()
                result = CommandResultV1("failed", self._snapshot(room))
            self._remember(user_id, key, fingerprint, result)
            return result

    def close(
        self,
        *,
        user_id: str,
        room_handle: str,
        idempotency_key: str | None = None,
    ) -> RoomSnapshotV1:
        with self._lock:
            self._require_open()
            user_id = self._identity(user_id, "user_id")
            handle = self._identity(room_handle, "room_handle")
            key = (
                self._identity(idempotency_key, "idempotency_key")
                if idempotency_key is not None else None
            )
            fingerprint = ("close", handle)
            if key is not None and (replay := self._replay(user_id, key, fingerprint)):
                assert isinstance(replay, RoomSnapshotV1)
                return replay
            room = self._room_owned_by(user_id, handle)
            error_code = None
            try:
                self.adapter.close(room_ref=room.adapter_state.room_ref)
            except ListenTogetherError as exc:
                error_code = exc.code
            self._terminate(room, state="ended", error_code=error_code)
            result = self._snapshot(room)
            if key is not None:
                self._remember(user_id, key, fingerprint, result)
            return result

    def maintain(self) -> list[RoomSnapshotV1]:
        """Heartbeat due rooms; the worker may call this from its existing loop."""
        with self._lock:
            self._require_open()
            changed: list[RoomSnapshotV1] = []
            now = self._clock()
            for room in list(self._rooms_by_user.values()):
                if now - room.last_heartbeat < self.heartbeat_interval:
                    continue
                room.last_heartbeat = now
                before = self._snapshot(room)
                self._refresh(room, heartbeat=True)
                after = self._snapshot(room)
                if after != before:
                    changed.append(after)
            return changed

    def shutdown(self) -> None:
        """Best-effort graceful close; a new manager intentionally restores nothing."""
        with self._lock:
            if self._closed:
                return
            for room in list(self._rooms_by_user.values()):
                self._best_effort_close(room)
                self._terminate(room, state="ended", error_code=None)
            self._idempotency.clear()
            self._closed = True

    def _room_owned_by(self, user_id: str, handle: str) -> _ActiveRoom:
        if self._users_by_handle.get(handle) != user_id:
            raise RoomNotFound("room was not found")
        room = self._rooms_by_user.get(user_id)
        if room is None or room.handle != handle:
            raise RoomNotFound("room was not found")
        return room

    def _refresh(self, room: _ActiveRoom, *, heartbeat: bool) -> None:
        try:
            state = (
                self.adapter.heartbeat(room_ref=room.adapter_state.room_ref)
                if heartbeat else self.adapter.snapshot(room_ref=room.adapter_state.room_ref)
            )
            self._apply_confirmed(room, state)
        except RoomAuthenticationFailed as exc:
            self._best_effort_close(room)
            self._terminate(room, state="failed", error_code=exc.code)
        except RoomUnreachable:
            self._best_effort_close(room)
            self._terminate(room, state="ended", error_code=RoomUnreachable.code)
        except (RoomTransportUnavailable, RoomOutOfOrder, RoomConfirmationTimeout):
            if self._clock() - room.last_confirmed >= self.unreachable_timeout:
                self._best_effort_close(room)
                self._terminate(room, state="ended", error_code=RoomUnreachable.code)

    def _best_effort_close(self, room: _ActiveRoom) -> None:
        try:
            self.adapter.close(room_ref=room.adapter_state.room_ref)
        except Exception:
            # Shutdown/fail-closed paths must not preserve a room just because
            # the private transport is already unavailable.
            pass


def _freeze(value: object):
    if isinstance(value, Mapping):
        return tuple((key, _freeze(value[key])) for key in sorted(value))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if value is None:
        return ()
    return value


@dataclass(frozen=True)
class NeteaseTransportState:
    """Private transport state.  None of its protocol fields are public wire."""

    external_room_id: str
    invite_url: str | None
    lifecycle: str
    current_track: dict | None
    user_joined: bool
    playback_state: str
    server_sequence: int
    queue_version: int
    authenticated: bool = True


@dataclass(frozen=True)
class NeteaseTransportAck:
    accepted: bool
    client_sequence: int
    authenticated: bool = True


class NeteaseRoomTransport(Protocol):
    """Reviewed private-protocol boundary; intentionally has no HTTP implementation."""

    def create_room(
        self,
        *,
        initial_track: dict,
        client_sequence: int,
        queue_version: int,
        idempotency_key: str,
    ) -> NeteaseTransportState: ...

    def get_state(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> NeteaseTransportState: ...

    def send_command(
        self,
        *,
        external_room_id: str,
        command: str,
        track: dict | None,
        client_sequence: int,
        queue_version: int,
        idempotency_key: str,
    ) -> NeteaseTransportAck: ...

    def heartbeat(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> NeteaseTransportState: ...

    def close_room(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> None: ...


@dataclass
class _NeteaseSession:
    external_room_id: str
    client_sequence: int
    server_sequence: int
    queue_version: int
    state: NeteaseTransportState
    last_confirmed: float


class ExperimentalNeteaseRoomAdapter:
    """Fail-closed NetEase protocol coordinator with an injectable transport.

    This class coordinates sequence numbers, queue versions, ACK checks and
    state confirmation.  It does *not* guess private endpoints, signing, login,
    or payload formats.  Until a reviewed transport is injected, calls return a
    specific disabled/unsupported error instead of pretending a room exists.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        transport: NeteaseRoomTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        unreachable_timeout_seconds: float = 10 * 60,
    ):
        self.enabled = enabled
        self.transport = transport
        self._clock = clock
        self.unreachable_timeout = unreachable_timeout_seconds
        self._sessions: dict[str, _NeteaseSession] = {}
        self._lock = threading.RLock()

    def _ready(self) -> NeteaseRoomTransport:
        if not self.enabled:
            raise RoomExperimentDisabled("NetEase room experiment is disabled")
        if self.transport is None:
            raise RoomProtocolUnsupported("NetEase private room transport is not implemented")
        return self.transport

    @staticmethod
    def _validate_state(
        value: NeteaseTransportState, *, expected_room_id: str | None = None
    ) -> NeteaseTransportState:
        if not isinstance(value, NeteaseTransportState):
            raise RoomTransportUnavailable("transport returned an invalid state")
        if not value.authenticated:
            raise RoomAuthenticationFailed("NetEase robot session is not authenticated")
        _bounded_text(value.external_room_id, "external room id", 512)
        if expected_room_id is not None and value.external_room_id != expected_room_id:
            raise RoomTransportUnavailable("transport changed the external room id")
        if value.lifecycle not in {"waiting_for_user", "connected", "ended", "failed"}:
            raise RoomTransportUnavailable("transport returned an invalid lifecycle")
        if value.playback_state not in {"playing", "paused", "unknown"}:
            raise RoomTransportUnavailable("transport returned an invalid playback state")
        if value.invite_url is not None:
            _https_url(value.invite_url, "invite_url")
        if value.current_track is not None:
            _public_track(value.current_track)
        if (
            not isinstance(value.server_sequence, int) or value.server_sequence < 0
            or not isinstance(value.queue_version, int) or value.queue_version < 0
        ):
            raise RoomTransportUnavailable("transport returned invalid protocol counters")
        return value

    @staticmethod
    def _public(value: NeteaseTransportState) -> AdapterRoomState:
        return AdapterRoomState(
            room_ref=value.external_room_id,
            lifecycle=value.lifecycle,
            current_track=_public_track(value.current_track) if value.current_track else None,
            user_joined=value.user_joined,
            invite_url=value.invite_url,
            playback_state=value.playback_state,
        )

    def _transport_call(self, session: _NeteaseSession | None, operation: Callable):
        try:
            return operation()
        except ListenTogetherError:
            raise
        except Exception as exc:
            if (
                session is not None
                and self._clock() - session.last_confirmed >= self.unreachable_timeout
            ):
                raise RoomUnreachable("NetEase room was unreachable for too long") from exc
            raise RoomTransportUnavailable("NetEase room transport failed") from exc

    def _accept_state(
        self, session: _NeteaseSession, value: NeteaseTransportState
    ) -> NeteaseTransportState:
        value = self._validate_state(value, expected_room_id=session.external_room_id)
        if value.server_sequence < session.server_sequence:
            raise RoomOutOfOrder("stale NetEase server sequence")
        if value.queue_version < session.queue_version:
            raise RoomOutOfOrder("stale NetEase queue version")
        session.server_sequence = value.server_sequence
        session.queue_version = value.queue_version
        session.state = value
        session.last_confirmed = self._clock()
        return value

    def _session(self, room_ref: str) -> _NeteaseSession:
        session = self._sessions.get(room_ref)
        if session is None:
            raise RoomNotFound("adapter room was not found")
        return session

    def create(self, *, initial_track: dict, idempotency_key: str) -> AdapterRoomState:
        with self._lock:
            transport = self._ready()
            track = _public_track(initial_track)
            state = self._transport_call(
                None,
                lambda: transport.create_room(
                    initial_track=track,
                    client_sequence=1,
                    queue_version=1,
                    idempotency_key=idempotency_key,
                ),
            )
            state = self._validate_state(state)
            if state.queue_version < 1:
                raise RoomOutOfOrder("create returned a stale queue version")
            session = _NeteaseSession(
                external_room_id=state.external_room_id,
                client_sequence=1,
                server_sequence=state.server_sequence,
                queue_version=state.queue_version,
                state=state,
                last_confirmed=self._clock(),
            )
            self._sessions[state.external_room_id] = session
            return self._public(state)

    def snapshot(self, *, room_ref: str) -> AdapterRoomState:
        with self._lock:
            transport = self._ready()
            session = self._session(room_ref)
            value = self._transport_call(
                session,
                lambda: transport.get_state(
                    external_room_id=session.external_room_id,
                    client_sequence=session.client_sequence,
                    queue_version=session.queue_version,
                ),
            )
            return self._public(self._accept_state(session, value))

    def heartbeat(self, *, room_ref: str) -> AdapterRoomState:
        with self._lock:
            transport = self._ready()
            session = self._session(room_ref)
            session.client_sequence += 1
            value = self._transport_call(
                session,
                lambda: transport.heartbeat(
                    external_room_id=session.external_room_id,
                    client_sequence=session.client_sequence,
                    queue_version=session.queue_version,
                ),
            )
            return self._public(self._accept_state(session, value))

    def command(
        self,
        *,
        room_ref: str,
        command: str,
        track: dict | None,
        idempotency_key: str,
    ) -> AdapterCommandResult:
        with self._lock:
            transport = self._ready()
            session = self._session(room_ref)
            if command not in ROOM_COMMANDS:
                raise RoomInvalid("unsupported room command")
            clean_track = _public_track(track) if track is not None else None
            before = session.state
            session.client_sequence += 1
            if command == "play_track":
                session.queue_version += 1
            sent_sequence = session.client_sequence
            ack = self._transport_call(
                session,
                lambda: transport.send_command(
                    external_room_id=session.external_room_id,
                    command=command,
                    track=clean_track,
                    client_sequence=sent_sequence,
                    queue_version=session.queue_version,
                    idempotency_key=idempotency_key,
                ),
            )
            if not isinstance(ack, NeteaseTransportAck):
                raise RoomTransportUnavailable("transport returned an invalid ACK")
            if not ack.authenticated:
                raise RoomAuthenticationFailed("NetEase robot session is not authenticated")
            if not ack.accepted:
                return AdapterCommandResult("failed", self._public(session.state), "command_rejected")
            if ack.client_sequence != sent_sequence:
                raise RoomOutOfOrder("NetEase ACK did not match the command sequence")
            value = self._transport_call(
                session,
                lambda: transport.get_state(
                    external_room_id=session.external_room_id,
                    client_sequence=session.client_sequence,
                    queue_version=session.queue_version,
                ),
            )
            state = self._accept_state(session, value)
            synchronized = self._command_confirmed(command, clean_track, before, state)
            return AdapterCommandResult(
                "synchronized" if synchronized else "accepted", self._public(state)
            )

    @staticmethod
    def _command_confirmed(
        command: str,
        track: dict | None,
        before: NeteaseTransportState,
        after: NeteaseTransportState,
    ) -> bool:
        if command == "pause":
            return after.playback_state == "paused"
        if command in {"play", "resume"}:
            return after.playback_state == "playing"
        if command == "play_track":
            return bool(
                track and after.current_track
                and after.current_track.get("track_id") == track.get("track_id")
            )
        if command in {"previous", "next"}:
            before_id = before.current_track.get("track_id") if before.current_track else None
            after_id = after.current_track.get("track_id") if after.current_track else None
            return bool(after_id and after_id != before_id)
        return False

    def close(self, *, room_ref: str) -> None:
        with self._lock:
            transport = self._ready()
            session = self._session(room_ref)
            session.client_sequence += 1
            try:
                self._transport_call(
                    session,
                    lambda: transport.close_room(
                        external_room_id=session.external_room_id,
                        client_sequence=session.client_sequence,
                        queue_version=session.queue_version,
                    ),
                )
            finally:
                self._sessions.pop(room_ref, None)


class InMemoryRoomAdapter:
    """Deterministic adapter for tests and local orchestration; never uses network."""

    def __init__(self):
        self.rooms: dict[str, AdapterRoomState] = {}
        self.calls: list[tuple] = []
        self._counter = 0

    def create(self, *, initial_track: dict, idempotency_key: str) -> AdapterRoomState:
        self._counter += 1
        ref = f"fake-external-{self._counter}"
        state = AdapterRoomState(
            ref, "waiting_for_user", _public_track(initial_track), False,
            f"https://music.163.com/listen-together/invite/{self._counter}", "playing",
        )
        self.rooms[ref] = state
        self.calls.append(("create", idempotency_key))
        return state

    def snapshot(self, *, room_ref: str) -> AdapterRoomState:
        self.calls.append(("snapshot", room_ref))
        return self._get(room_ref)

    def heartbeat(self, *, room_ref: str) -> AdapterRoomState:
        self.calls.append(("heartbeat", room_ref))
        return self._get(room_ref)

    def command(
        self,
        *,
        room_ref: str,
        command: str,
        track: dict | None,
        idempotency_key: str,
    ) -> AdapterCommandResult:
        state = self._get(room_ref)
        playback = state.playback_state
        if command == "pause":
            playback = "paused"
        elif command in {"play", "resume"}:
            playback = "playing"
        current = _public_track(track) if command == "play_track" and track else state.current_track
        updated = AdapterRoomState(
            state.room_ref, state.lifecycle, current, state.user_joined,
            state.invite_url, playback,
        )
        self.rooms[room_ref] = updated
        self.calls.append(("command", command, idempotency_key))
        return AdapterCommandResult("synchronized", updated)

    def close(self, *, room_ref: str) -> None:
        self.calls.append(("close", room_ref))
        self.rooms.pop(room_ref, None)

    def join(self, room_ref: str) -> None:
        state = self._get(room_ref)
        self.rooms[room_ref] = AdapterRoomState(
            state.room_ref, "connected", state.current_track, True,
            state.invite_url, state.playback_state,
        )

    def _get(self, room_ref: str) -> AdapterRoomState:
        try:
            return self.rooms[room_ref]
        except KeyError as exc:
            raise RoomNotFound("adapter room was not found") from exc


_ROOM_WIRE_REQUIRED_KEYS = frozenset({
    "version", "room_handle", "state", "current_track", "user_joined",
    "pending_command", "invite_url", "updated_at", "error_code",
})
_ROOM_WIRE_KEYS = _ROOM_WIRE_REQUIRED_KEYS | {"playback_state"}


def _room_from_wire(value: object) -> RoomSnapshotV1:
    if (
        not isinstance(value, dict)
        or not _ROOM_WIRE_REQUIRED_KEYS.issubset(value)
        or not set(value).issubset(_ROOM_WIRE_KEYS)
    ):
        raise RoomIPCUnavailable("worker returned an invalid room response")
    if value.get("version") != 1:
        raise RoomIPCUnavailable("worker returned an unsupported room version")
    try:
        return RoomSnapshotV1(
            room_handle=value["room_handle"],
            state=value["state"],
            current_track=value["current_track"],
            user_joined=value["user_joined"],
            pending_command=value["pending_command"],
            invite_url=value["invite_url"],
            updated_at=value["updated_at"],
            error_code=value["error_code"],
            playback_state=value.get("playback_state", "unknown"),
        )
    except (KeyError, RoomInvalid) as exc:
        raise RoomIPCUnavailable("worker returned an invalid room response") from exc


class RoomIPCServer:
    """One-request-per-connection Unix socket facade owned by the worker.

    The socket file is mode ``0600``.  Each request and response is one bounded
    JSON line; malformed, oversized or multi-line requests are rejected before
    reaching the manager.
    """

    def __init__(
        self,
        manager: ListenTogetherRoomManager,
        socket_path: str | os.PathLike[str],
        *,
        max_message_bytes: int = 64 * 1024,
        accept_timeout_seconds: float = 0.25,
    ):
        if max_message_bytes < 1024:
            raise ValueError("IPC message limit is too small")
        self.manager = manager
        self.socket_path = os.fspath(socket_path)
        self.max_message_bytes = max_message_bytes
        self.accept_timeout = accept_timeout_seconds
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._socket is not None:
            return
        parent = os.path.dirname(os.path.abspath(self.socket_path))
        if not os.path.isdir(parent):
            raise RoomIPCUnavailable("room IPC directory does not exist")
        try:
            existing = os.lstat(self.socket_path)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
                raise RoomIPCUnavailable("refusing to replace a non-socket IPC path")
            os.unlink(self.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(self.socket_path)
            os.chmod(self.socket_path, 0o600)
            server.listen(16)
            server.settimeout(self.accept_timeout)
        except Exception:
            server.close()
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
            raise
        self._socket = server
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.serve_forever, name="murmur-room-ipc", daemon=True
        )
        self._thread.start()

    def serve_forever(self) -> None:
        server = self._socket
        if server is None:
            raise RoomIPCUnavailable("room IPC server has not started")
        while not self._stop.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                raise
            with connection:
                self._serve_connection(connection)

    def _serve_connection(self, connection: socket.socket) -> None:
        try:
            payload = self._read_line(connection)
            request = json.loads(payload)
            if not isinstance(request, dict):
                raise RoomInvalid("IPC request must be an object")
            result = self._dispatch(request)
            response = {"ok": True, "data": result}
        except ListenTogetherError as exc:
            response = {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc)[:500],
                    "retryable": bool(exc.retryable),
                },
            }
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            response = {
                "ok": False,
                "error": {
                    "code": RoomInvalid.code,
                    "message": "invalid room IPC request",
                    "retryable": False,
                },
            }
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > self.max_message_bytes:
            encoded = (
                b'{"ok":false,"error":{"code":"room_ipc_unavailable",'
                b'"message":"room IPC response is too large","retryable":true}}'
            )
        try:
            connection.sendall(encoded + b"\n")
        except OSError:
            pass

    def _read_line(self, connection: socket.socket) -> str:
        chunks = bytearray()
        while len(chunks) <= self.max_message_bytes:
            part = connection.recv(min(4096, self.max_message_bytes + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
            if b"\n" in part:
                break
        if len(chunks) > self.max_message_bytes:
            raise RoomInvalid("room IPC request is too large")
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if not separator or remainder.strip():
            raise RoomInvalid("room IPC request must be one JSON line")
        return line.decode("utf-8")

    def _dispatch(self, request: dict) -> dict | None:
        operation = request.get("operation")
        allowed = {
            "create": {"operation", "user_id", "initial_track", "idempotency_key"},
            "current": {"operation", "user_id"},
            "command": {
                "operation", "user_id", "room_handle", "command", "track",
                "idempotency_key",
            },
            "close": {"operation", "user_id", "room_handle", "idempotency_key"},
        }
        if operation not in allowed or set(request) != allowed[operation]:
            raise RoomInvalid("invalid room IPC operation")
        if operation == "create":
            return self.manager.create(
                user_id=request["user_id"],
                initial_track=request["initial_track"],
                idempotency_key=request["idempotency_key"],
            ).to_wire()
        if operation == "current":
            room = self.manager.current(user_id=request["user_id"])
            return room.to_wire() if room else None
        if operation == "command":
            return self.manager.command(
                user_id=request["user_id"],
                room_handle=request["room_handle"],
                command=request["command"],
                track=request["track"],
                idempotency_key=request["idempotency_key"],
            ).to_wire()
        return self.manager.close(
            user_id=request["user_id"],
            room_handle=request["room_handle"],
            idempotency_key=request["idempotency_key"],
        ).to_wire()

    def close(self) -> None:
        self._stop.set()
        server, self._socket = self._socket, None
        if server is not None:
            server.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.accept_timeout * 4))
        self._thread = None
        try:
            existing = os.lstat(self.socket_path)
            if stat.S_ISSOCK(existing.st_mode) and existing.st_uid == os.getuid():
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass


class RoomIPCClient:
    """Fail-closed API-side facade with the manager's public methods."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        # 这条 socket 上没有一个操作是纯本地的：worker 那头每次都要打网易云。
        # 读一次状态就是三个并行上游请求，每个自己有十秒预算；建房还要再加上
        # 曲库校验、建房、换歌单、下发播放、心跳。原来的 2 秒几乎必然超时，而
        # 超时之后用户看到的是一句和真实原因无关的「一起听暂时不可用」——
        # 客户端那边的刷新还是静默失败，于是表现成「header 不会自己更新」。
        timeout_seconds: float = 15.0,
        create_timeout_seconds: float = 45.0,
        max_message_bytes: int = 64 * 1024,
    ):
        self.socket_path = os.fspath(socket_path)
        self.timeout = timeout_seconds
        self.create_timeout = create_timeout_seconds
        self.max_message_bytes = max_message_bytes

    def create(
        self, *, user_id: str, initial_track: dict, idempotency_key: str
    ) -> RoomSnapshotV1:
        data = self._request({
            "operation": "create",
            "user_id": user_id,
            "initial_track": initial_track,
            "idempotency_key": idempotency_key,
        }, timeout=self.create_timeout)
        return _room_from_wire(data)

    def current(self, *, user_id: str) -> RoomSnapshotV1 | None:
        data = self._request({"operation": "current", "user_id": user_id})
        return _room_from_wire(data) if data is not None else None

    def command(
        self,
        *,
        user_id: str,
        room_handle: str,
        command: str,
        idempotency_key: str,
        track: dict | None = None,
    ) -> CommandResultV1:
        data = self._request({
            "operation": "command",
            "user_id": user_id,
            "room_handle": room_handle,
            "command": command,
            "track": track,
            "idempotency_key": idempotency_key,
        }, timeout=self.create_timeout)
        if not isinstance(data, dict) or set(data) != {"status", "room"}:
            raise RoomIPCUnavailable("worker returned an invalid command response")
        try:
            return CommandResultV1(data["status"], _room_from_wire(data["room"]))
        except RoomInvalid as exc:
            raise RoomIPCUnavailable("worker returned an invalid command response") from exc

    def close(
        self,
        *,
        user_id: str,
        room_handle: str,
        idempotency_key: str | None = None,
    ) -> RoomSnapshotV1:
        data = self._request({
            "operation": "close",
            "user_id": user_id,
            "room_handle": room_handle,
            "idempotency_key": idempotency_key,
        })
        return _room_from_wire(data)

    def _request(self, request: dict, *, timeout: float | None = None):
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > self.max_message_bytes:
            raise RoomInvalid("room IPC request is too large")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout if timeout is None else timeout)
        try:
            connection.connect(self.socket_path)
            connection.sendall(encoded + b"\n")
            payload = self._read_response(connection)
        except (OSError, TimeoutError) as exc:
            raise RoomIPCUnavailable("room worker is unavailable") from exc
        finally:
            connection.close()
        try:
            response = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RoomIPCUnavailable("room worker returned invalid JSON") from exc
        if not isinstance(response, dict) or response.get("ok") not in {True, False}:
            raise RoomIPCUnavailable("room worker returned an invalid response")
        if response["ok"] is True:
            if set(response) != {"ok", "data"}:
                raise RoomIPCUnavailable("room worker returned an invalid response")
            return response["data"]
        error = response.get("error")
        if not isinstance(error, dict) or set(error) != {"code", "message", "retryable"}:
            raise RoomIPCUnavailable("room worker returned an invalid error")
        raise RoomRemoteError(
            str(error["message"]), code=str(error["code"]),
            retryable=bool(error["retryable"]),
        )

    def _read_response(self, connection: socket.socket) -> bytes:
        chunks = bytearray()
        while len(chunks) <= self.max_message_bytes:
            part = connection.recv(min(4096, self.max_message_bytes + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
            if b"\n" in part:
                break
        if len(chunks) > self.max_message_bytes:
            raise RoomIPCUnavailable("room IPC response is too large")
        line, separator, remainder = bytes(chunks).partition(b"\n")
        if not separator or remainder.strip():
            raise RoomIPCUnavailable("room IPC response must be one JSON line")
        return line
