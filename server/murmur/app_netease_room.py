"""把 Phase 0 那个受审协议服务，接成 adapter 认识的 transport。

这一层只做翻译，不碰网易云的私有协议本身：签名、登录、端点形状都留在
隔离运行的 Phase 0 服务里，它以专用可丢弃账号登录，只监听回环。这里把它
的 JSON 翻成 `NeteaseTransportState` / `NeteaseTransportAck`，并且在任何
一步对不上时抛错，而不是编一个「同步成功」出来。

两个计数器的来源不同，值得写清楚：

* `server_sequence` 是网易云自己给的 `playCommand.serverSeq`，Phase 0 只在
  真的看到更新的远端命令时才前移，所以直接透传即可，天然单调。
* `queue_version` 上游没有对应物。它由本类维护：建房是 1，每成功换一次歌
  加一。adapter 那边也是这么算的，两边只有都以「真的换过歌」为依据才不会
  漂——所以它记的是已经完成的换歌次数，不是发出去的命令数。
"""

from __future__ import annotations

import httpx

from .app_listen_together import (
    NeteaseTransportAck,
    NeteaseTransportState,
    RoomAuthenticationFailed,
    RoomTransportUnavailable,
)

# Phase 0 只持有一个房间、一个机器人账号，所以命令里不带房间号；接进来之后
# 仍然按外部房间号核对，房间号对不上宁可报错也不发命令。
_COMMANDS = {
    "play": "PLAY",
    "pause": "PAUSE",
    "resume": "PLAY",
    "previous": "PREVIOUS",
    "next": "NEXT",
}
_PLAYBACK = {"PLAY": "playing", "PAUSE": "paused"}


class NeteaseHTTPRoomTransport:
    """受审协议服务的 HTTP 客户端；只说 adapter 那套词汇。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ):
        if client is not None and transport is not None:
            raise ValueError("pass client or transport, not both")
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            base_url=self.base_url, timeout=timeout, transport=transport,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._queue_version = 0
        # 上游状态里只有 songId，没有 Murmur 的 track 形状。当前这首歌以我们
        # 自己发出去的那份为准，换歌成功才更新——上游没有可信的替代来源。
        self._current_track: dict | None = None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = self.client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise RoomTransportUnavailable("netease protocol service failed") from exc
        if response.status_code == 401:
            raise RoomAuthenticationFailed("netease robot session is not authenticated")
        if response.status_code >= 400:
            raise RoomTransportUnavailable("netease protocol service rejected the call")
        try:
            body = response.json()
        except ValueError as exc:
            raise RoomTransportUnavailable("netease protocol service returned non-JSON") from exc
        if not isinstance(body, dict):
            raise RoomTransportUnavailable("netease protocol service returned an invalid body")
        return body

    @staticmethod
    def _song_id(track: dict) -> str:
        value = track.get("track_id")
        if not isinstance(value, str) or not value.isdecimal():
            raise RoomTransportUnavailable("track is not a NetEase song")
        return value

    def _state(self, body: dict, track: dict | None) -> NeteaseTransportState:
        """把一份 Phase 0 房间状态翻成 adapter 的形状。

        没有房间号就是没有房间：宁可报 transport 失败，也不能返回一个
        adapter 会当成有效房间的空壳。
        """
        if body.get("state") != "active":
            raise RoomTransportUnavailable("netease room is not active")
        room_id = body.get("roomId")
        if not isinstance(room_id, (str, int)) or not str(room_id).strip():
            raise RoomTransportUnavailable("netease room id is missing")
        server_seq = body.get("serverSeq", 0)
        if not isinstance(server_seq, int) or isinstance(server_seq, bool) or server_seq < 0:
            raise RoomTransportUnavailable("netease server sequence is invalid")
        invite = body.get("inviteUrl")
        if invite is not None and not (
            isinstance(invite, str) and invite.startswith("https://")
        ):
            raise RoomTransportUnavailable("netease invite url is invalid")
        # roomUsers 只在自己一个人时长度为 1；对方进来才算连上。
        joined = int(body.get("participantCount") or 0) > 1
        return NeteaseTransportState(
            external_room_id=str(room_id),
            invite_url=invite,
            lifecycle="connected" if joined else "waiting_for_user",
            current_track=track,
            user_joined=joined,
            playback_state=_PLAYBACK.get(str(body.get("playStatus") or ""), "unknown"),
            server_sequence=server_seq,
            queue_version=self._queue_version,
            authenticated=True,
        )

    def create_room(
        self,
        *,
        initial_track: dict,
        client_sequence: int,
        queue_version: int,
        idempotency_key: str,
    ) -> NeteaseTransportState:
        song_id = self._song_id(initial_track)
        self._queue_version = max(queue_version, 1)
        self._call("POST", "/api/room/create", {
            "songIds": [song_id], "initialSongId": song_id,
        })
        # 建房那一下不返回成员信息，状态要再查一次才完整。
        self._current_track = initial_track
        return self._state(self._call("GET", "/api/room/status"), initial_track)

    def get_state(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> NeteaseTransportState:
        return self._checked(external_room_id, self._call("GET", "/api/room/status"))

    def heartbeat(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> NeteaseTransportState:
        return self.get_state(
            external_room_id=external_room_id,
            client_sequence=client_sequence,
            queue_version=queue_version,
        )

    def _checked(self, external_room_id: str, body: dict) -> NeteaseTransportState:
        state = self._state(body, self._current_track)
        if state.external_room_id != external_room_id:
            raise RoomTransportUnavailable("netease room id changed underneath us")
        return state

    def send_command(
        self,
        *,
        external_room_id: str,
        command: str,
        track: dict | None,
        client_sequence: int,
        queue_version: int,
        idempotency_key: str,
    ) -> NeteaseTransportAck:
        if command == "play_track":
            if track is None:
                raise RoomTransportUnavailable("play_track requires a track")
            song_id = self._song_id(track)
            self._call("POST", "/api/room/playlist", {
                "songIds": [song_id], "initialSongId": song_id,
            })
            # 换歌真的完成了才推进队列版本，和 adapter 的算法对齐。
            self._queue_version = max(self._queue_version + 1, queue_version)
            self._current_track = track
            return NeteaseTransportAck(True, client_sequence)
        action = _COMMANDS.get(command)
        if action is None:
            raise RoomTransportUnavailable("unsupported netease room command")
        self._call("POST", "/api/room/command", {"command": action})
        return NeteaseTransportAck(True, client_sequence)

    def close_room(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> None:
        self._call("POST", "/api/room/end")
        self._current_track = None


__all__ = ["NeteaseHTTPRoomTransport"]
