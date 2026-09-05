"""一起听房间的状态机、私有 IPC 与 App API 边界。

这条线没有数据库，也没有生产授权：测试要证明的是"关着的时候什么都不发生"，
以及"确认不了的时候绝不报成功"。真实网易协议不在这里，也不该在这里——适配器
在没有注入 transport 时必须失败，而不是假装房间存在。
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import EnrolledClient, make_config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from murmur.app_api import create_app, room_api_error  # noqa: E402
from murmur.app_listen_together import (  # noqa: E402
    AdapterCommandResult,
    CommandResultV1,
    ExperimentalNeteaseRoomAdapter,
    InMemoryRoomAdapter,
    ListenTogetherRoomManager,
    RoomAuthenticationFailed,
    RoomConfirmationTimeout,
    RoomConflict,
    RoomExperimentDisabled,
    RoomIdempotencyConflict,
    RoomInvalid,
    RoomIPCClient,
    RoomIPCServer,
    RoomIPCUnavailable,
    RoomNotFound,
    RoomSnapshotV1,
    RoomTransportUnavailable,
    _room_from_wire,
    parse_room_chat_intent,
)
from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore  # noqa: E402

TRACK = {
    "version": 1,
    "provider": "netease",
    "track_id": "186016",
    "title": "夜曲",
    "artists": ["周杰伦"],
    "canonical_url": "https://music.163.com/song?id=186016",
}


class RoomChatIntentTests(unittest.TestCase):
    def test_only_explicit_control_phrases_execute(self):
        expected = {
            "暂停一下": "pause",
            "继续播放": "resume",
            "下一首": "next",
            "上一首歌": "previous",
            "结束一起听": "close",
        }
        for text, action in expected.items():
            with self.subTest(text=text):
                self.assertEqual(parse_room_chat_intent(text).action, action)

    def test_ambiguous_conversation_does_not_control_the_room(self):
        for text in (
            "我刚才暂停了一下",
            "下一首你想听什么？",
            "这首歌听起来像上一首",
            "我们要不要结束一起听？",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_room_chat_intent(text))

OTHER_TRACK = {**TRACK, "track_id": "186017", "title": "反方向的钟"}


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def manager(adapter=None, clock=None, **kwargs) -> ListenTogetherRoomManager:
    return ListenTogetherRoomManager(
        adapter or InMemoryRoomAdapter(),
        clock=clock or FakeClock(),
        heartbeat_interval_seconds=15.0,
        unreachable_timeout_seconds=600.0,
        **kwargs,
    )


class RoomLifecycleTests(unittest.TestCase):
    def test_a_new_room_waits_for_the_person_before_it_says_connected(self):
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter)
        snapshot = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        self.assertEqual(snapshot.state, "waiting_for_user")
        self.assertFalse(snapshot.user_joined)
        self.assertEqual(snapshot.current_track, TRACK)
        self.assertEqual(snapshot.playback_state, "playing")
        self.assertTrue(snapshot.invite_url.startswith("https://"))

        adapter.join(next(iter(adapter.rooms)))
        joined = rooms.current(user_id="u1")
        self.assertEqual(joined.state, "connected")
        self.assertTrue(joined.user_joined)

    def test_the_public_snapshot_carries_no_protocol_identifiers(self):
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter)
        # 这个 user_id 必须长到不可能被随机撞上。room_handle 是 24 位
        # base64url，拿「u1」这种两字符的 id 去做子串匹配，早晚会在某次随机
        # token 里撞出一个假阳性——CI 上真的撞到过一次。
        user_id = "user-a1b2c3d4-e5f6-4789-a0b1-c2d3e4f5a6b7"
        snapshot = rooms.create(
            user_id=user_id, initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        wire = json.dumps(snapshot.to_wire(), ensure_ascii=False)
        external = next(iter(adapter.rooms))
        self.assertNotIn(external, wire)
        self.assertNotIn("room_ref", wire)
        self.assertNotIn(user_id, wire)
        self.assertEqual(snapshot.to_wire()["playback_state"], "playing")

    def test_old_worker_snapshots_default_to_an_unknown_playback_state(self):
        snapshot = manager().create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        ).to_wire()
        snapshot.pop("playback_state")
        self.assertEqual(_room_from_wire(snapshot).playback_state, "unknown")

    def test_invalid_public_playback_state_is_refused(self):
        snapshot = manager().create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        with self.assertRaises(RoomInvalid):
            replace(snapshot, playback_state="buffering")

    def test_one_person_may_only_hold_one_room(self):
        rooms = manager()
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")
        with self.assertRaises(RoomConflict):
            rooms.create(
                user_id="u1", initial_track=dict(OTHER_TRACK),
                idempotency_key="key-00000002",
            )
        # 另一个人不受影响。
        other = rooms.create(
            user_id="u2", initial_track=dict(TRACK), idempotency_key="key-00000003"
        )
        self.assertEqual(other.state, "waiting_for_user")

    def test_a_handle_belongs_to_exactly_one_person(self):
        rooms = manager()
        mine = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        with self.assertRaises(RoomNotFound):
            rooms.command(
                user_id="u2", room_handle=mine.room_handle, command="pause",
                idempotency_key="key-00000002",
            )

    def test_closing_ends_the_room_and_lets_a_new_one_start(self):
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter)
        first = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        ended = rooms.close(user_id="u1", room_handle=first.room_handle)
        self.assertEqual(ended.state, "ended")
        self.assertIsNone(rooms.current(user_id="u1"))
        self.assertEqual(adapter.rooms, {})
        second = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000004"
        )
        self.assertNotEqual(second.room_handle, first.room_handle)

    def test_shutdown_closes_rooms_and_restores_nothing(self):
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter)
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")
        rooms.shutdown()
        self.assertEqual(adapter.rooms, {})
        with self.assertRaises(RoomExperimentDisabled):
            rooms.current(user_id="u1")


class RoomCommandTests(unittest.TestCase):
    def test_a_confirmed_command_is_synchronized_and_clears_the_pending_state(self):
        rooms = manager()
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        result = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        self.assertEqual(result.status, "synchronized")
        self.assertIsNone(result.room.pending_command)
        self.assertIsNone(result.room.error_code)

    def test_an_unconfirmed_command_stays_syncing_and_is_never_synchronized(self):
        class AcceptOnly(InMemoryRoomAdapter):
            def command(self, **kwargs):
                super().command(**kwargs)
                return AdapterCommandResult("accepted", self.rooms[kwargs["room_ref"]])

        rooms = manager(AcceptOnly())
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        result = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="next",
            idempotency_key="key-00000002",
        )
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.room.state, "syncing")
        self.assertEqual(result.room.pending_command, "next")

    def test_a_timeout_reports_failed_rather_than_a_synchronized_guess(self):
        class TimingOut(InMemoryRoomAdapter):
            def command(self, **kwargs):
                raise RoomConfirmationTimeout("no confirmation")

        rooms = manager(TimingOut())
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        result = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.room.state, "syncing")
        self.assertEqual(result.room.error_code, "room_confirmation_timeout")
        # 房间还在：一次没确认不等于结束。
        self.assertIsNotNone(rooms.current(user_id="u1", refresh=False))

    def test_a_rejected_command_keeps_the_confirmed_room_active(self):
        class Rejecting(InMemoryRoomAdapter):
            def command(self, **kwargs):
                state = self.rooms[kwargs["room_ref"]]
                return AdapterCommandResult("failed", state, "command_rejected")

        rooms = manager(Rejecting())
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        result = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.room.state, "waiting_for_user")
        self.assertEqual(result.room.playback_state, "playing")
        self.assertEqual(result.room.error_code, "command_rejected")
        self.assertIsNotNone(rooms.current(user_id="u1", refresh=False))

    def test_play_track_needs_a_track_and_the_others_refuse_one(self):
        rooms = manager()
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        with self.assertRaises(RoomInvalid):
            rooms.command(
                user_id="u1", room_handle=room.room_handle, command="play_track",
                idempotency_key="key-00000002",
            )
        with self.assertRaises(RoomInvalid):
            rooms.command(
                user_id="u1", room_handle=room.room_handle, command="pause",
                track=dict(TRACK), idempotency_key="key-00000003",
            )
        with self.assertRaises(RoomInvalid):
            rooms.command(
                user_id="u1", room_handle=room.room_handle, command="rewind",
                idempotency_key="key-00000004",
            )
        changed = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="play_track",
            track=dict(OTHER_TRACK), idempotency_key="key-00000005",
        )
        self.assertEqual(changed.room.current_track["track_id"], OTHER_TRACK["track_id"])

    def test_a_repeated_key_replays_instead_of_acting_twice(self):
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter)
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        again = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        self.assertEqual(again, room)
        self.assertEqual([call for call in adapter.calls if call[0] == "create"], [
            ("create", "key-00000001"),
        ])

        first = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        repeat = rooms.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        self.assertEqual(first, repeat)
        self.assertEqual(
            len([call for call in adapter.calls if call[0] == "command"]), 1
        )

    def test_the_same_key_for_a_different_request_is_a_conflict(self):
        rooms = manager()
        room = rooms.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        rooms.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        with self.assertRaises(RoomIdempotencyConflict):
            rooms.command(
                user_id="u1", room_handle=room.room_handle, command="next",
                idempotency_key="key-00000002",
            )

    def test_the_person_switching_songs_in_netease_is_authoritative(self):
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter)
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")
        ref = next(iter(adapter.rooms))
        state = adapter.rooms[ref]
        adapter.rooms[ref] = replace(state, current_track=dict(OTHER_TRACK))
        followed = rooms.current(user_id="u1")
        self.assertEqual(followed.current_track["track_id"], OTHER_TRACK["track_id"])


class RoomFailureTests(unittest.TestCase):
    def test_ten_quiet_minutes_end_the_local_session(self):
        class Unavailable(InMemoryRoomAdapter):
            failing = True

            def heartbeat(self, *, room_ref: str):
                if self.failing:
                    raise RoomTransportUnavailable("no route")
                return super().heartbeat(room_ref=room_ref)

        clock = FakeClock()
        adapter = Unavailable()
        rooms = manager(adapter, clock=clock)
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")

        clock.advance(60)
        rooms.maintain()
        self.assertIsNotNone(rooms.current(user_id="u1", refresh=False))

        clock.advance(600)
        rooms.maintain()
        self.assertIsNone(rooms.current(user_id="u1", refresh=False))

    def test_idle_alone_never_ends_a_room(self):
        clock = FakeClock()
        rooms = manager(clock=clock)
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")
        for _ in range(20):
            clock.advance(600)
            rooms.maintain()
        self.assertIsNotNone(rooms.current(user_id="u1"))

    def test_a_dead_bot_session_fails_the_room_closed(self):
        class Expired(InMemoryRoomAdapter):
            def heartbeat(self, *, room_ref: str):
                raise RoomAuthenticationFailed("session expired")

        clock = FakeClock()
        rooms = manager(Expired(), clock=clock)
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")
        clock.advance(16)
        rooms.maintain()
        self.assertIsNone(rooms.current(user_id="u1", refresh=False))

    def test_heartbeats_are_not_sent_more_often_than_the_interval(self):
        clock = FakeClock()
        adapter = InMemoryRoomAdapter()
        rooms = manager(adapter, clock=clock)
        rooms.create(user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001")
        rooms.maintain()
        self.assertEqual([c for c in adapter.calls if c[0] == "heartbeat"], [])
        clock.advance(16)
        rooms.maintain()
        rooms.maintain()
        self.assertEqual(len([c for c in adapter.calls if c[0] == "heartbeat"]), 1)
        clock.advance(16)
        rooms.maintain()
        self.assertEqual(len([c for c in adapter.calls if c[0] == "heartbeat"]), 2)


class ExperimentalAdapterTests(unittest.TestCase):
    def test_without_a_reviewed_transport_nothing_pretends_to_work(self):
        adapter = ExperimentalNeteaseRoomAdapter(enabled=True)
        with self.assertRaises(Exception) as caught:
            adapter.create(initial_track=dict(TRACK), idempotency_key="key-00000001")
        self.assertEqual(caught.exception.code, "room_protocol_unsupported")

    def test_the_switch_being_off_is_its_own_error(self):
        adapter = ExperimentalNeteaseRoomAdapter(enabled=False)
        with self.assertRaises(RoomExperimentDisabled):
            adapter.create(initial_track=dict(TRACK), idempotency_key="key-00000001")


class RoomIPCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rooms.sock"
        self.adapter = InMemoryRoomAdapter()
        self.manager = manager(self.adapter)
        self.server = RoomIPCServer(self.manager, self.path)
        self.server.start()
        self.client = RoomIPCClient(self.path, timeout_seconds=5.0)

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def test_the_socket_is_private_to_its_owner(self):
        mode = stat.S_IMODE(os.lstat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_room_round_trips_over_the_socket(self):
        room = self.client.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        self.assertIsInstance(room, RoomSnapshotV1)
        self.assertEqual(self.client.current(user_id="u1").room_handle, room.room_handle)
        result = self.client.command(
            user_id="u1", room_handle=room.room_handle, command="pause",
            idempotency_key="key-00000002",
        )
        self.assertIsInstance(result, CommandResultV1)
        self.assertEqual(result.status, "synchronized")
        closed = self.client.close(user_id="u1", room_handle=room.room_handle)
        self.assertEqual(closed.state, "ended")
        self.assertIsNone(self.client.current(user_id="u1"))

    def test_a_worker_side_refusal_arrives_as_the_same_typed_error(self):
        self.client.create(
            user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000001"
        )
        with self.assertRaises(Exception) as caught:
            self.client.create(
                user_id="u1", initial_track=dict(TRACK), idempotency_key="key-00000009"
            )
        self.assertEqual(caught.exception.code, "room_conflict")

    def test_an_unknown_or_malformed_request_is_refused_before_the_manager(self):
        for payload in (b"not json\n", b"[]\n", b'{"operation":"drop_everything"}\n',
                        b'{"operation":"create"}\n'):
            with self.subTest(payload=payload):
                response = self._raw(payload)
                self.assertFalse(response["ok"])
        self.assertEqual(self.adapter.calls, [])

    def test_a_second_line_is_rejected_rather_than_parsed(self):
        response = self._raw(
            b'{"operation":"current","user_id":"u1"}\n{"operation":"current"}\n'
        )
        self.assertFalse(response["ok"])

    def test_an_absent_worker_is_retryable_not_a_silent_success(self):
        client = RoomIPCClient(Path(self.tmp.name) / "missing.sock", timeout_seconds=1.0)
        with self.assertRaises(RoomIPCUnavailable) as caught:
            client.current(user_id="u1")
        self.assertTrue(caught.exception.retryable)

    def _raw(self, payload: bytes) -> dict:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5.0)
        try:
            connection.connect(str(self.path))
            connection.sendall(payload)
            chunks = bytearray()
            while b"\n" not in chunks:
                part = connection.recv(4096)
                if not part:
                    break
                chunks.extend(part)
        finally:
            connection.close()
        return json.loads(bytes(chunks).split(b"\n")[0])


class RoomErrorMappingTests(unittest.TestCase):
    def test_every_room_failure_has_its_own_status_and_fixed_copy(self):
        cases = {
            RoomInvalid("x"): 400,
            RoomNotFound("x"): 404,
            RoomConflict("x"): 409,
            RoomIdempotencyConflict("x"): 409,
            RoomExperimentDisabled("x"): 403,
            RoomConfirmationTimeout("x"): 504,
            RoomIPCUnavailable("x"): 503,
        }
        for error, status in cases.items():
            with self.subTest(code=error.code):
                api_error = room_api_error(error)
                self.assertEqual(api_error.status, status)
                self.assertEqual(api_error.code, error.code)
                self.assertNotIn("x", api_error.message)

    def test_a_retryable_room_failure_stays_retryable_for_the_client(self):
        self.assertTrue(room_api_error(RoomTransportUnavailable("x")).retryable)
        self.assertFalse(room_api_error(RoomNotFound("x")).retryable)


class RoomIPCTimeoutTests(unittest.TestCase):
    """这条 socket 上没有纯本地的操作，超时必须按上游的实际代价给。"""

    def test_mutating_calls_get_far_longer_than_a_status_read(self):
        client = RoomIPCClient("/tmp/does-not-matter.sock")
        # 读一次状态就是三个并行上游请求，每个自己有十秒预算；建房还要再加上
        # 曲库校验、建房、换歌单、下发播放、心跳。原来两者都是 2 秒，几乎必然
        # 超时，而用户看到的是一句和真实原因无关的「一起听暂时不可用」。
        self.assertGreaterEqual(client.timeout, 10)
        self.assertGreater(client.create_timeout, client.timeout)


def room_secret(root: Path) -> Path:
    """A private bot-session file, because the validator insists on one."""
    secret = root / "netease-bot.json"
    secret.write_text("{}", encoding="utf-8")
    secret.chmod(0o600)
    return secret


def api_settings(root: Path, **overrides) -> AppSettings:
    if overrides.get("netease_room_experiment_enabled"):
        overrides.setdefault("netease_bot_secret_path", room_secret(root))
        overrides.setdefault(
            "netease_room_protocol_base_url", "https://room.invalid"
        )
    value = AppSettings(
        db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
        data_root=root, upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766", app_id="", team_id="",
        attest_mode="development", attest_root_path=None, allow_development=True,
        development_token="development-token-0123456789",
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        requests_per_minute=500, timezone=ZoneInfo("Asia/Shanghai"),
    )
    return replace(value, **overrides)


class ListenTogetherAPITests(unittest.TestCase):
    """房间开关、白名单与错误状态在 HTTP 边界上的样子。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AppStore(self.root / "murmur.db")
        self.adapter = InMemoryRoomAdapter()
        self.manager = manager(self.adapter)
        self.clients = []
        # Enrolment has to happen before the allowlist can name anybody, so the
        # device is created against a plain app and then pointed at the one
        # under test.  Both share this store, which is where the identity lives.
        self.device = EnrolledClient(
            self._client(api_settings(self.root)), self.store,
            api_settings(self.root).development_token,
        )

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.store.close()
        self.tmp.cleanup()

    def _client(self, settings: AppSettings, **kwargs) -> TestClient:
        client = TestClient(create_app(
            settings, cfg=make_config(settings.memory_db_path), store=self.store,
            **kwargs,
        ))
        self.clients.append(client)
        return client

    def use(self, settings: AppSettings, **kwargs) -> TestClient:
        self.device.client = self._client(settings, **kwargs)
        return self.device.client

    def room_settings(self, allowlist=None, **overrides) -> AppSettings:
        return api_settings(
            self.root,
            netease_catalog_enabled=True,
            netease_room_experiment_enabled=True,
            netease_room_user_allowlist=frozenset(
                allowlist if allowlist is not None else {self.device.user_id}
            ),
            **overrides,
        )

    def open_room_api(self) -> TestClient:
        return self.use(self.room_settings(), room_client=self.manager)

    def test_the_routes_refuse_everything_while_the_switch_is_off(self):
        client = self.use(api_settings(self.root))
        calls = [
            ("POST", "/v1/listen-together/rooms",
             {"initial_track": dict(TRACK), "idempotency_key": "key-00000001"}),
            ("GET", "/v1/listen-together/rooms/current", None),
            ("POST", "/v1/listen-together/rooms/abc/commands",
             {"command": "pause", "idempotency_key": "key-00000002"}),
            ("DELETE", "/v1/listen-together/rooms/abc", None),
        ]
        for method, path, body in calls:
            with self.subTest(path=path, method=method):
                response = client.request(
                    method, path, headers=self.device.headers(), json=body
                )
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(
                    response.json()["error"]["code"], "listen_together_unavailable"
                )
        self.assertEqual(self.adapter.calls, [])

    def test_an_account_outside_the_allowlist_is_refused(self):
        client = self.use(
            self.room_settings(allowlist={"somebody-else"}), room_client=self.manager
        )
        response = client.get(
            "/v1/listen-together/rooms/current", headers=self.device.headers()
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.adapter.calls, [])

    def test_create_current_command_and_close_over_http(self):
        client = self.open_room_api()
        created = client.post(
            "/v1/listen-together/rooms", headers=self.device.headers(),
            json={"initial_track": dict(TRACK), "idempotency_key": "key-00000001"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        room = created.json()
        self.assertEqual(room["state"], "waiting_for_user")
        self.assertEqual(room["version"], 1)
        self.assertEqual(room["playback_state"], "playing")

        current = client.get(
            "/v1/listen-together/rooms/current", headers=self.device.headers()
        )
        self.assertEqual(current.json()["room_handle"], room["room_handle"])

        commanded = client.post(
            f"/v1/listen-together/rooms/{room['room_handle']}/commands",
            headers=self.device.headers(),
            json={"command": "pause", "idempotency_key": "key-00000002"},
        )
        self.assertEqual(commanded.status_code, 200, commanded.text)
        self.assertEqual(commanded.json()["status"], "synchronized")

        closed = client.request(
            "DELETE", f"/v1/listen-together/rooms/{room['room_handle']}",
            headers=self.device.headers(),
            json={"idempotency_key": "key-00000003"},
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertEqual(closed.json()["state"], "ended")

        gone = client.get(
            "/v1/listen-together/rooms/current", headers=self.device.headers()
        )
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(gone.json()["error"]["code"], "room_not_found")

    def test_create_reuses_an_active_room_and_switches_its_track(self):
        client = self.open_room_api()
        first = client.post(
            "/v1/listen-together/rooms", headers=self.device.headers(),
            json={"initial_track": dict(TRACK), "idempotency_key": "key-00000001"},
        )
        changed = client.post(
            "/v1/listen-together/rooms", headers=self.device.headers(),
            json={
                "initial_track": dict(OTHER_TRACK),
                "idempotency_key": "key-00000002",
            },
        )
        self.assertEqual(changed.status_code, 201, changed.text)
        self.assertEqual(
            changed.json()["room_handle"], first.json()["room_handle"]
        )
        self.assertEqual(
            changed.json()["current_track"]["track_id"], OTHER_TRACK["track_id"]
        )
        self.assertEqual(
            len([call for call in self.adapter.calls if call[0] == "create"]), 1
        )

    def test_close_requires_and_replays_an_idempotency_key(self):
        client = self.open_room_api()
        created = client.post(
            "/v1/listen-together/rooms", headers=self.device.headers(),
            json={"initial_track": dict(TRACK), "idempotency_key": "key-00000001"},
        ).json()
        path = f"/v1/listen-together/rooms/{created['room_handle']}"
        missing = client.request(
            "DELETE", path, headers=self.device.headers(), json={}
        )
        self.assertEqual(missing.status_code, 400, missing.text)

        body = {"idempotency_key": "key-00000002"}
        first = client.request(
            "DELETE", path, headers=self.device.headers(), json=body
        )
        again = client.request(
            "DELETE", path, headers=self.device.headers(), json=body
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(first.json(), again.json())

    def test_a_room_only_accepts_netease_songs(self):
        client = self.open_room_api()
        audius = {
            **TRACK, "provider": "audius",
            "canonical_url": "https://audius.co/a/track",
        }
        response = client.post(
            "/v1/listen-together/rooms", headers=self.device.headers(),
            json={"initial_track": audius, "idempotency_key": "key-00000001"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.adapter.calls, [])

    def test_bad_commands_and_keys_never_reach_the_worker(self):
        client = self.open_room_api()
        created = client.post(
            "/v1/listen-together/rooms", headers=self.device.headers(),
            json={"initial_track": dict(TRACK), "idempotency_key": "key-00000001"},
        )
        handle = created.json()["room_handle"]
        bad_bodies = [
            {"command": "self_destruct", "idempotency_key": "key-00000002"},
            {"command": "pause", "idempotency_key": "short"},
            {"command": "play_track", "idempotency_key": "key-00000003"},
            {"command": "pause", "track": dict(TRACK),
             "idempotency_key": "key-00000004"},
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                response = client.post(
                    f"/v1/listen-together/rooms/{handle}/commands",
                    headers=self.device.headers(), json=body,
                )
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            [call for call in self.adapter.calls if call[0] == "command"], []
        )

    def test_another_persons_handle_is_a_404_not_a_leak(self):
        client = self.open_room_api()
        stranger = self.manager.create(
            user_id="somebody-else", initial_track=dict(TRACK),
            idempotency_key="key-00000009",
        )
        response = client.post(
            f"/v1/listen-together/rooms/{stranger.room_handle}/commands",
            headers=self.device.headers(),
            json={"command": "pause", "idempotency_key": "key-00000002"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "room_not_found")

    def test_a_worker_that_is_not_running_is_retryable(self):
        client = self.use(
            self.room_settings(),
            room_client=RoomIPCClient(self.root / "absent.sock", timeout_seconds=1.0),
        )
        response = client.get(
            "/v1/listen-together/rooms/current", headers=self.device.headers()
        )
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()["error"]
        self.assertEqual(body["code"], "room_ipc_unavailable")
        self.assertTrue(body["retryable"])


if __name__ == "__main__":
    unittest.main()
