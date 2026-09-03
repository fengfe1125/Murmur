"""受审协议服务 → adapter transport 的翻译层。

守两件事：一是 adapter 依赖的那几个不变量（房间号一致、序号单调、ACK 回原
序号）必须被真的满足；二是任何一步对不上时要抛错，不能编一个能让 adapter
当成「同步成功」的状态出来。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from murmur.app_listen_together import (  # noqa: E402
    ExperimentalNeteaseRoomAdapter,
    RoomAuthenticationFailed,
    RoomExperimentDisabled,
    RoomOutOfOrder,
    RoomTransportUnavailable,
)
from murmur.app_netease_room import NeteaseHTTPRoomTransport  # noqa: E402

TRACK = {
    "version": 1,
    "provider": "netease",
    "track_id": "186016",
    "title": "晴天",
    "artists": ["周杰伦"],
    "artwork_url": None,
    "canonical_url": "https://music.163.com/song?id=186016",
    "duration_seconds": 269,
    "explicit": False,
}
OTHER = {**TRACK, "track_id": "1391891631", "title": "嗜好", "artists": ["颜人中"],
         "canonical_url": "https://music.163.com/song?id=1391891631"}


def room_body(**extra):
    return {
        "state": "active", "roomId": "room-1", "serverSeq": 0,
        "currentSongId": "186016", "playStatus": "PLAY", "progressMs": 0,
        "playlistLength": 1, "participantCount": 1,
        "inviteUrl": "https://st.music.163.com/listen-together/share/?roomId=room-1",
        **extra,
    }


class FakeService:
    """一个够真的 Phase 0 替身：记下调用，按脚本回状态。"""

    def __init__(self, *, states=None, status=200):
        self.calls = []
        self.states = list(states or [])
        self.status = status
        self.last = room_body()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        if request.url.path == "/api/room/status":
            if self.states:
                self.last = self.states.pop(0)
            return httpx.Response(200, json=self.last)
        return httpx.Response(200, json={"ok": True})

    def transport(self):
        return NeteaseHTTPRoomTransport(
            "https://127.0.0.1:18763",
            transport=httpx.MockTransport(self.handler),
        )


class TransportTranslationTests(unittest.TestCase):
    def test_creating_a_room_reads_membership_from_a_second_status_call(self):
        """建房那一下不带成员信息，所以状态必须再查一次才完整。"""
        service = FakeService()
        state = service.transport().create_room(
            initial_track=TRACK, client_sequence=1, queue_version=1,
            idempotency_key="k",
        )
        self.assertEqual(state.external_room_id, "room-1")
        self.assertEqual(state.lifecycle, "waiting_for_user")
        self.assertFalse(state.user_joined)
        self.assertEqual(state.playback_state, "playing")
        self.assertEqual(state.current_track, TRACK)
        self.assertEqual(
            service.calls,
            [("POST", "/api/room/create"), ("GET", "/api/room/status")],
        )

    def test_a_second_participant_is_what_makes_the_room_connected(self):
        service = FakeService(states=[room_body(participantCount=2)])
        state = service.transport().create_room(
            initial_track=TRACK, client_sequence=1, queue_version=1,
            idempotency_key="k",
        )
        self.assertEqual(state.lifecycle, "connected")
        self.assertTrue(state.user_joined)

    def test_a_paused_room_is_reported_paused_and_an_unknown_status_is_not_guessed(self):
        for raw, expected in (("PLAY", "playing"), ("PAUSE", "paused"), ("", "unknown")):
            with self.subTest(raw=raw):
                service = FakeService(states=[room_body(playStatus=raw)])
                state = service.transport().create_room(
                    initial_track=TRACK, client_sequence=1, queue_version=1,
                    idempotency_key="k",
                )
                self.assertEqual(state.playback_state, expected)

    def test_an_idle_service_is_an_error_not_an_empty_room(self):
        """没有房间就是没有房间，不能返回一个 adapter 会当真的空壳。"""
        service = FakeService(states=[{"state": "idle"}])
        with self.assertRaises(RoomTransportUnavailable):
            service.transport().create_room(
                initial_track=TRACK, client_sequence=1, queue_version=1,
                idempotency_key="k",
            )

    def test_a_room_id_that_changes_underneath_us_is_refused(self):
        service = FakeService(states=[room_body(), room_body(roomId="room-2")])
        transport = service.transport()
        transport.create_room(
            initial_track=TRACK, client_sequence=1, queue_version=1,
            idempotency_key="k",
        )
        with self.assertRaises(RoomTransportUnavailable):
            transport.get_state(
                external_room_id="room-1", client_sequence=1, queue_version=1
            )

    def test_an_unauthenticated_service_is_an_authentication_failure(self):
        service = FakeService(status=401)
        with self.assertRaises(RoomAuthenticationFailed):
            service.transport().create_room(
                initial_track=TRACK, client_sequence=1, queue_version=1,
                idempotency_key="k",
            )

    def test_a_command_ack_echoes_the_sequence_the_adapter_sent(self):
        """adapter 会拿它和自己发出去的号对，对不上就判乱序。"""
        service = FakeService()
        ack = service.transport().send_command(
            external_room_id="room-1", command="pause", track=None,
            client_sequence=7, queue_version=1, idempotency_key="k",
        )
        self.assertTrue(ack.accepted)
        self.assertEqual(ack.client_sequence, 7)
        self.assertEqual(service.calls, [("POST", "/api/room/command")])

    def test_changing_the_song_replaces_the_playlist_and_moves_the_queue_version(self):
        # 换歌之后服务端报的是新的那首，两边对得上，track 才留得住。
        service = FakeService(states=[
            room_body(), room_body(currentSongId=OTHER["track_id"]),
        ])
        transport = service.transport()
        transport.create_room(
            initial_track=TRACK, client_sequence=1, queue_version=1,
            idempotency_key="k",
        )
        transport.send_command(
            external_room_id="room-1", command="play_track", track=OTHER,
            client_sequence=2, queue_version=2, idempotency_key="k2",
        )
        state = transport.get_state(
            external_room_id="room-1", client_sequence=2, queue_version=2
        )
        self.assertGreaterEqual(state.queue_version, 2)
        self.assertEqual(state.current_track, OTHER)
        self.assertIn(("POST", "/api/room/playlist"), service.calls)

    def test_a_room_that_moved_on_reports_no_track_instead_of_the_old_one(self):
        """上游只给 songId。对不上就是我们手上这份过期了——如实说不知道。

        `next` / `previous` 之后房间的当前歌会变，而 Murmur 的 track 形状上游
        给不了。这时候把旧的那首继续端出去，卡片就会显示错的歌，而且命令确认
        那一步还可能据此判成「已同步」。
        """
        service = FakeService(states=[
            room_body(), room_body(currentSongId="99999999"),
        ])
        transport = service.transport()
        transport.create_room(
            initial_track=TRACK, client_sequence=1, queue_version=1,
            idempotency_key="k",
        )
        state = transport.get_state(
            external_room_id="room-1", client_sequence=1, queue_version=1
        )
        self.assertIsNone(state.current_track)

    def test_a_nonsense_participant_count_is_a_transport_error(self):
        service = FakeService(states=[room_body(participantCount="两个")])
        with self.assertRaises(RoomTransportUnavailable):
            service.transport().create_room(
                initial_track=TRACK, client_sequence=1, queue_version=1,
                idempotency_key="k",
            )

    def test_a_track_that_is_not_a_netease_song_never_reaches_the_service(self):
        service = FakeService()
        with self.assertRaises(RoomTransportUnavailable):
            service.transport().create_room(
                initial_track={**TRACK, "track_id": "not-a-number"},
                client_sequence=1, queue_version=1, idempotency_key="k",
            )
        self.assertEqual(service.calls, [])


class AdapterWithRealTransportTests(unittest.TestCase):
    """接上真 adapter 跑一遍，证明这套翻译满足它的全部不变量。"""

    def test_a_full_room_lifecycle_runs_through_the_adapter(self):
        service = FakeService(states=[
            room_body(),                                  # create
            room_body(participantCount=2, serverSeq=3),   # after join
            room_body(participantCount=2, serverSeq=4, playStatus="PAUSE"),
        ])
        adapter = ExperimentalNeteaseRoomAdapter(
            enabled=True, transport=service.transport()
        )
        created = adapter.create(initial_track=TRACK, idempotency_key="k")
        self.assertEqual(created.lifecycle, "waiting_for_user")

        joined = adapter.snapshot(room_ref=created.room_ref)
        self.assertEqual(joined.lifecycle, "connected")
        self.assertTrue(joined.user_joined)

        result = adapter.command(
            room_ref=created.room_ref, command="pause", track=None,
            idempotency_key="k2",
        )
        self.assertIn(result.status, {"synchronized", "accepted"})
        self.assertEqual(result.state.playback_state, "paused")

        adapter.close(room_ref=created.room_ref)

    def test_a_disabled_adapter_still_refuses_even_with_a_transport(self):
        service = FakeService()
        adapter = ExperimentalNeteaseRoomAdapter(
            enabled=False, transport=service.transport()
        )
        with self.assertRaises(RoomExperimentDisabled):
            adapter.create(initial_track=TRACK, idempotency_key="k")
        self.assertEqual(service.calls, [])

    def test_the_adapter_refuses_a_state_that_goes_backwards(self):
        """服务端序号倒退意味着状态漂了，宁可报错也不能当成新状态。"""
        service = FakeService(states=[
            room_body(serverSeq=9), room_body(serverSeq=2),
        ])
        adapter = ExperimentalNeteaseRoomAdapter(
            enabled=True, transport=service.transport()
        )
        created = adapter.create(initial_track=TRACK, idempotency_key="k")
        with self.assertRaises(RoomOutOfOrder):
            adapter.snapshot(room_ref=created.room_ref)



class WorkerWiringTests(unittest.TestCase):
    """协议地址才是真开关：房间开关自己到不了网易云。"""

    def _settings(self, root: Path, protocol_url):
        from zoneinfo import ZoneInfo

        from murmur.app_settings import AppSettings
        secret = root / "netease-bot.json"
        secret.write_text("{}", encoding="utf-8")
        secret.chmod(0o600)
        return AppSettings(
            db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
            data_root=root, upload_dir=root / "uploads",
            public_base_url="http://127.0.0.1:8766", app_id="", team_id="",
            attest_mode="development", attest_root_path=None,
            allow_development=True,
            development_token="development-token-0123456789",
            apns_key_path=None, apns_key_id=None, apns_team_id=None,
            apns_topic="com.sakura.Murmur", apns_environment="development",
            requests_per_minute=500, timezone=ZoneInfo("Asia/Shanghai"),
            netease_catalog_enabled=True,
            netease_room_experiment_enabled=True,
            netease_room_user_allowlist=frozenset({"u1"}),
            netease_bot_secret_path=secret,
            netease_room_protocol_base_url=protocol_url,
        )

    def test_without_a_protocol_url_the_adapter_still_has_no_transport(self):
        import tempfile

        from murmur.app_worker import _start_listen_together
        with tempfile.TemporaryDirectory() as tmp:
            manager, server = _start_listen_together(
                self._settings(Path(tmp), None)
            )
            try:
                self.assertIsNone(manager.adapter.transport)
            finally:
                if server is not None:
                    server.close()

    def test_a_protocol_url_injects_the_http_transport(self):
        import tempfile

        from murmur.app_worker import _start_listen_together
        with tempfile.TemporaryDirectory() as tmp:
            manager, server = _start_listen_together(
                self._settings(Path(tmp), "https://127.0.0.1:18763")
            )
            try:
                self.assertIsInstance(
                    manager.adapter.transport, NeteaseHTTPRoomTransport
                )
            finally:
                if server is not None:
                    server.close()

if __name__ == "__main__":
    unittest.main()
