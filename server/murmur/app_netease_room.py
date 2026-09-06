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
        catalog=None,
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
        # 上游状态里只有 songId。没有曲库就只能拿我们自己发出去的那份顶，
        # 而换歌、放完自动切、对方操作之后那份就过期了——顶部就一直显示上
        # 一首。曲库自己有缓存，同一首反复查很便宜。
        self.catalog = catalog
        self._owns_client = client is None
        self._queue_version = 0
        # 上游状态里只有 songId，没有 Murmur 的 track 形状。当前这首歌以我们
        # 自己发出去的那份为准，换歌成功才更新——上游没有可信的替代来源。
        self._current_track: dict | None = None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def playable_song_ids(self, song_ids: list[str]) -> list[str]:
        if not isinstance(song_ids, list) or not 1 <= len(song_ids) <= 10:
            raise RoomTransportUnavailable("playability requires 1-10 songs")
        clean = []
        for value in song_ids:
            if not isinstance(value, str) or not value.isdecimal() or value in clean:
                raise RoomTransportUnavailable("playability song IDs are invalid")
            clean.append(value)
        body = self._call("POST", "/api/music/playability", {"songIds": clean})
        if set(body) != {"playableSongIds"} or not isinstance(
            body["playableSongIds"], list
        ):
            raise RoomTransportUnavailable("netease playability response is invalid")
        playable = body["playableSongIds"]
        allowed = set(clean)
        if any(
            not isinstance(value, str) or value not in allowed for value in playable
        ):
            raise RoomTransportUnavailable("netease playability response is invalid")
        found = set(playable)
        return [value for value in clean if value in found]

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        """协议服务把每种失败都压成 502 + `{"error": "<code>"}`。

        只看状态码的话，「已经有房间了」和「这首歌下架了」长得一模一样，到
        用户那儿都是同一句「暂时不可用」。要自愈就必须把码读出来。
        """
        try:
            body = response.json()
        except ValueError:
            return None
        return body.get("error") if isinstance(body, dict) else None

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = self.client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise RoomTransportUnavailable("netease protocol service failed") from exc
        if response.status_code == 401:
            raise RoomAuthenticationFailed("netease robot session is not authenticated")
        if response.status_code >= 400:
            raise RoomTransportUnavailable(
                "netease protocol service rejected the call: "
                f"{self._error_code(response) or response.status_code}"
            )
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
        count = body.get("participantCount") or 0
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RoomTransportUnavailable("netease participant count is invalid")
        joined = count > 1
        queue_version = body.get("queueVersion", self._queue_version)
        if (
            not isinstance(queue_version, int) or isinstance(queue_version, bool)
            or queue_version < 0
        ):
            raise RoomTransportUnavailable("netease queue version is invalid")
        self._queue_version = max(self._queue_version, queue_version)
        error_code = body.get("errorCode")
        if error_code not in {None, "counterpart_rights_unavailable"}:
            raise RoomTransportUnavailable("netease room error code is invalid")
        # 上游只说当前是哪个 songId，不给 Murmur 的 track 形状。对不上就是
        # 我们手上这份已经过期了（next/previous 之后就会这样）——那就如实说
        # 不知道，而不是把旧的那首当成正在放的那首端出去。
        current = body.get("currentSongId")
        if current is not None and str(current) != str((track or {}).get("track_id")):
            # 手上这份过期了。先去曲库查这首到底是什么；查不到就说不知道，
            # 不许拿旧的顶——顶部显示错的歌名比不显示更糟。
            track = self._resolve(str(current))
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
            error_code=error_code,
        )

    def _resolve(self, song_id: str) -> dict | None:
        if self.catalog is None:
            return None
        try:
            return self.catalog.resolve("netease", song_id)
        except Exception:
            # 查不到、曲库挂了、返回了看不懂的东西——都只意味着这一刻说不出
            # 是哪首歌，不该让整个房间状态失败。
            return None

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
        body = {"songIds": [song_id], "initialSongId": song_id}
        try:
            self._call("POST", "/api/room/create", body)
        except RoomTransportUnavailable:
            # 房间只活在两处：manager 在 worker 内存里（重启即忘），协议服务
            # 在它自己进程里（重启才清）。worker 每次部署都会留下一个只有协议
            # 服务记得的孤儿房间，之后每一次建房都撞 room_already_active，
            # 用户看到的是永久的「一起听暂时不可用」——重启 App 也不会好。
            #
            # 这里只对这一种情况自愈，而且只重试一次。前提是协议服务全局只有
            # 一个房间、房间白名单只有一个用户，所以残留的那个一定是他自己的。
            # 将来支持多用户时，这个前提不再成立，必须重新设计。
            if not self._stale_room_cleared():
                raise
            self._call("POST", "/api/room/create", body)
        # 建房那一下不返回成员信息，状态要再查一次才完整。
        self._current_track = initial_track
        return self._state(self._call("GET", "/api/room/status"), initial_track)

    def _stale_room_cleared(self) -> bool:
        """协议服务手上是不是有一个 Murmur 已经不认识的房间；有就收掉。"""
        try:
            status = self._call("GET", "/api/room/status")
        except RoomTransportUnavailable:
            return False
        if status.get("state") != "active":
            return False
        self._call("POST", "/api/room/end")
        return True

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
        if state.current_track is not None:
            self._current_track = state.current_track
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
            body = self._call("POST", "/api/room/playlist", {
                "songIds": [song_id], "initialSongId": song_id,
            })
            # 换歌真的完成了才推进队列版本，和 adapter 的算法对齐。
            self._queue_version = max(self._queue_version + 1, queue_version)
            self._current_track = track
            state = self._checked(external_room_id, body) if body.get("state") == "active" else None
            return NeteaseTransportAck(True, client_sequence, state=state)
        action = _COMMANDS.get(command)
        if action is None:
            raise RoomTransportUnavailable("unsupported netease room command")
        body = self._call("POST", "/api/room/command", {"command": action})
        state = self._checked(external_room_id, body) if body.get("state") == "active" else None
        return NeteaseTransportAck(True, client_sequence, state=state)

    def close_room(
        self, *, external_room_id: str, client_sequence: int, queue_version: int
    ) -> None:
        self._call("POST", "/api/room/end")
        self._current_track = None


__all__ = ["NeteaseHTTPRoomTransport"]
