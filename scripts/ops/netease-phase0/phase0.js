"use strict";

const fs = require("node:fs");
const path = require("node:path");

const SESSION_COOKIE_NAMES = new Set([
  "MUSIC_U", "MUSIC_A", "__csrf", "NMTID", "WNMCID", "os", "appver",
]);
const COMMANDS = new Set(["PLAY", "PAUSE", "NEXT", "PREVIOUS", "GOTO"]);

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

class Phase0Error extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = "Phase0Error";
    this.code = code;
  }
}

function positiveId(value, field = "songId") {
  const text = String(value ?? "").trim();
  if (!/^[1-9][0-9]{0,19}$/.test(text)) {
    throw new Phase0Error("invalid_request", `${field} must be a positive decimal ID`);
  }
  return text;
}

function normalizeSongIds(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 50) {
    throw new Phase0Error("invalid_request", "songIds must contain 1-50 IDs");
  }
  return [...new Set(value.map((item) => positiveId(item)))];
}

function normalizePlayableSongIds(value) {
  const ids = normalizeSongIds(value);
  if (ids.length > 10) {
    throw new Phase0Error("invalid_request", "songIds must contain 1-10 IDs");
  }
  return ids;
}

function responseBody(response) {
  return response && typeof response.body === "object" && response.body !== null
    ? response.body
    : {};
}

function requireCode(response, operation, allowed = new Set([200])) {
  const body = responseBody(response);
  if (!allowed.has(Number(body.code))) {
    throw new Phase0Error(`${operation}_failed`);
  }
  return body;
}

function cookiesFromResponse(response) {
  const values = [];
  if (Array.isArray(response?.cookie)) values.push(...response.cookie);
  if (typeof response?.body?.cookie === "string") {
    values.push(...response.body.cookie.split(/,(?=[^;,]+=)/));
  }
  const cookies = new Map();
  for (const raw of values) {
    const first = String(raw).split(";", 1)[0];
    const separator = first.indexOf("=");
    if (separator <= 0) continue;
    const name = first.slice(0, separator).trim();
    const value = first.slice(separator + 1).trim();
    if (SESSION_COOKIE_NAMES.has(name) && value && !/[\r\n;]/.test(value)) {
      cookies.set(name, value);
    }
  }
  if (!cookies.has("MUSIC_U") && !cookies.has("MUSIC_A")) {
    throw new Phase0Error("login_session_missing");
  }
  return Object.fromEntries(cookies);
}

function atomicWriteSecret(filePath, data) {
  const directory = path.dirname(filePath);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.chmodSync(directory, 0o700);
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(data)}\n`, {
    encoding: "utf8", mode: 0o600, flag: "wx",
  });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, filePath);
  fs.chmodSync(filePath, 0o600);
}

function readSecret(filePath) {
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  if ((stat.mode & 0o077) !== 0) throw new Phase0Error("session_permissions_unsafe");
  const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (!parsed || parsed.version !== 1 || typeof parsed.cookies !== "object") {
    throw new Phase0Error("session_invalid");
  }
  if (!parsed.cookies.MUSIC_U && !parsed.cookies.MUSIC_A) {
    throw new Phase0Error("session_invalid");
  }
  return parsed.cookies;
}

class NeteasePhase0 {
  constructor({
    api,
    qrToSvg,
    sessionPath,
    now = () => Date.now(),
    monotonicNow = () => Number(process.hrtime.bigint() / 1_000_000n),
    heartbeatFailureTimeoutMs = 10 * 60 * 1000,
    recordTiming = () => {},
  }) {
    if (!Number.isInteger(heartbeatFailureTimeoutMs) || heartbeatFailureTimeoutMs < 1_000) {
      throw new Error("heartbeatFailureTimeoutMs must be at least one second");
    }
    this.api = api;
    this.qrToSvg = qrToSvg;
    this.sessionPath = sessionPath;
    this.now = now;
    this.monotonicNow = monotonicNow;
    this.heartbeatFailureTimeoutMs = heartbeatFailureTimeoutMs;
    this.recordTiming = recordTiming;
    this.cookies = readSecret(sessionPath);
    this.qrKey = null;
    this.room = null;
    this.heartbeatTimer = null;
    this.heartbeatFailureSince = null;
    this.lastTerminalState = null;
  }

  async timed(stage, operation) {
    const started = this.monotonicNow();
    let outcome = "success";
    try {
      return await operation();
    } catch (error) {
      outcome = "failure";
      throw error;
    } finally {
      try {
        this.recordTiming({
          stage,
          durationMs: Math.max(0, this.monotonicNow() - started),
          outcome,
        });
      } catch {
        // Telemetry must never turn a successful playback operation into a
        // user-visible failure.
      }
    }
  }

  cookieHeader() {
    if (!this.cookies) throw new Phase0Error("not_authenticated");
    return Object.entries(this.cookies).map(([name, value]) => `${name}=${value}`).join("; ");
  }

  clearSession() {
    this.cookies = null;
    try {
      fs.unlinkSync(this.sessionPath);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  async loginStart() {
    const result = await this.api.login_qr_key({ timestamp: this.now() });
    const body = requireCode(result, "login_start");
    const key = body?.data?.unikey;
    if (typeof key !== "string" || key.length < 8 || key.length > 256) {
      throw new Phase0Error("login_key_missing");
    }
    this.qrKey = key;
    const url = `https://music.163.com/login?codekey=${encodeURIComponent(key)}`;
    return { state: "waiting", qrSvg: await this.qrToSvg(url) };
  }

  async loginStatus() {
    if (this.cookies) {
      const login = await this.checkLoginState();
      if (!login.reachable) throw new Phase0Error("login_status_unreachable");
      if (login.authenticated) return { state: "authenticated" };
      this.clearSession();
      return { state: "expired" };
    }
    if (!this.qrKey) return { state: "not_started" };
    const result = await this.api.login_qr_check({ key: this.qrKey, timestamp: this.now() });
    const code = Number(responseBody(result).code);
    if (code === 800) {
      this.qrKey = null;
      return { state: "expired" };
    }
    if (code === 801) return { state: "waiting" };
    if (code === 802) return { state: "scanned" };
    if (code !== 803) throw new Phase0Error("login_check_failed");
    const cookies = cookiesFromResponse(result);
    atomicWriteSecret(this.sessionPath, {
      version: 1, cookies, savedAt: new Date(this.now()).toISOString(),
    });
    this.cookies = cookies;
    this.qrKey = null;
    const login = await this.checkLoginState();
    if (!login.reachable) throw new Phase0Error("login_verification_unreachable");
    if (!login.authenticated) {
      this.clearSession();
      throw new Phase0Error("login_verification_failed");
    }
    return { state: "authenticated" };
  }

  async verifyLogin() {
    return (await this.checkLoginState()).authenticated;
  }

  async checkLoginState() {
    if (!this.cookies) return { reachable: true, authenticated: false };
    try {
      const result = await this.api.login_status({
        cookie: this.cookieHeader(), timestamp: this.now(), timeout: 10_000,
      });
      const body = responseBody(result);
      return {
        reachable: true,
        authenticated: Number(body?.data?.code) === 200 && Boolean(body?.data?.profile?.userId),
      };
    } catch {
      return { reachable: false, authenticated: false };
    }
  }

  async closeStaleRemoteRoom() {
    if (!this.cookies) return { state: "not_authenticated" };
    const login = await this.checkLoginState();
    if (!login.reachable) return { state: "unreachable" };
    if (!login.authenticated) {
      this.clearSession();
      return { state: "expired" };
    }
    const result = await this.api.listentogether_status({
      cookie: this.cookieHeader(), timeout: 10_000,
    });
    const body = requireCode(result, "stale_room_status");
    const roomId = body?.data?.roomInfo?.roomId;
    if (!body?.data?.inRoom || (typeof roomId !== "string" && typeof roomId !== "number")) {
      return { state: "clear" };
    }
    const ended = await this.api.listentogether_end({
      roomId: String(roomId), cookie: this.cookieHeader(), timeout: 10_000,
    });
    const endedBody = requireCode(ended, "stale_room_end");
    if (endedBody?.data?.success !== true) throw new Phase0Error("stale_room_end_unconfirmed");
    return { state: "stale_room_ended" };
  }

  async accountId() {
    const result = await this.api.login_status({
      cookie: this.cookieHeader(), timestamp: this.now(), timeout: 10_000,
    });
    return positiveId(responseBody(result)?.data?.profile?.userId, "userId");
  }

  async playableSongIds(songIds) {
    const ids = normalizePlayableSongIds(songIds);
    return this.checkPlayableSongIds(ids);
  }

  async checkPlayableSongIds(ids) {
    return this.timed("playability_check", async () => {
      const checks = await Promise.all(ids.map(async (id) => {
        const result = await this.api.check_music({
          id, cookie: this.cookieHeader(), timeout: 10_000,
        });
        return responseBody(result).success === true ? id : null;
      }));
      return checks.filter(Boolean);
    });
  }

  async createRoom({ songIds, initialSongId }) {
    if (this.room) throw new Phase0Error("room_already_active");
    const { playlist, initial, durations } = await this.validatePlaylist(songIds, initialSongId);
    const accountId = await this.accountId();
    const created = await this.api.listentogether_room_create({
      cookie: this.cookieHeader(), timeout: 10_000,
    });
    const roomId = requireCode(created, "room_create")?.data?.roomInfo?.roomId;
    if (typeof roomId !== "string" && typeof roomId !== "number") {
      throw new Phase0Error("room_create_failed");
    }
    this.room = {
      roomId: String(roomId), accountId, playlist, durations,
      currentIndex: playlist.indexOf(initial), clientSeq: 1,
      lastServerSeq: 0, queueVersion: 1, errorCode: null,
      participantCount: 1, inRoom: true,
      playStatus: "PLAY", progress: 0, playStartedAt: this.monotonicNow(),
      createdAt: this.now(),
    };
    this.lastTerminalState = null;
    this.heartbeatFailureSince = null;
    try {
      await this.replacePlaylist();
      await this.sendPlayCommand("GOTO", initial);
      await this.sendHeartbeat();
      this.startHeartbeat();
    } catch (error) {
      await this.endRoom().catch(() => {});
      throw error;
    }
    return this.publicRoom();
  }

  async validatePlaylist(songIds, initialSongId) {
    const playlist = normalizeSongIds(songIds);
    const initial = positiveId(initialSongId);
    if (!playlist.includes(initial)) throw new Phase0Error("invalid_request");
    const details = await this.api.song_detail({
      ids: playlist.join(","), cookie: this.cookieHeader(), timeout: 10_000,
    });
    const detailBody = requireCode(details, "song_detail");
    const available = new Set((detailBody.songs || []).map((song) => String(song.id)));
    if (!playlist.every((id) => available.has(id))) throw new Phase0Error("song_unavailable");
    const playable = new Set(await this.checkPlayableSongIds(playlist));
    if (!playlist.every((id) => playable.has(id))) throw new Phase0Error("song_unavailable");
    // 时长本来被丢掉了，但没有它就不知道歌什么时候放完——进度会一直往上爬，
    // 对面看到「3:50 的歌播到第 6 分钟」就判定同步失败。
    const durations = new Map();
    for (const song of detailBody.songs || []) {
      const ms = Number(song?.dt);
      if (Number.isFinite(ms) && ms > 0) durations.set(String(song.id), Math.floor(ms));
    }
    return { playlist, initial, durations };
  }

  publicRoom(extra = {}) {
    if (!this.room) return { ...(this.lastTerminalState || { state: "idle" }), ...extra };
    const currentSongId = this.room.playlist[this.room.currentIndex];
    const invite = new URL("https://st.music.163.com/listen-together/share/");
    invite.searchParams.set("songId", currentSongId);
    invite.searchParams.set("roomId", this.room.roomId);
    invite.searchParams.set("inviterId", this.room.accountId);
    return {
      state: "active", currentSongId, playStatus: this.room.playStatus,
      progressMs: this.currentProgress(), playlistLength: this.room.playlist.length,
      inviteUrl: invite.toString(),
      // Murmur 的 transport 要按外部房间号寻址，并且要拿服务端自己的序号来
      // 判断状态有没有倒退。两者本来都只留在进程内，界面用不到，接入才要。
      roomId: this.room.roomId,
      serverSeq: this.room.lastServerSeq,
      queueVersion: this.room.queueVersion,
      participantCount: this.room.participantCount,
      inRoom: this.room.inRoom,
      ...(this.room.errorCode ? { errorCode: this.room.errorCode } : {}),
      ...extra,
    };
  }

  async roomInviteQr() {
    const room = this.publicRoom();
    if (room.state !== "active") throw new Phase0Error("room_not_active");
    return { qrSvg: await this.qrToSvg(room.inviteUrl) };
  }

  async replacePlaylist() {
    const room = this.requireRoom();
    const ids = room.playlist.join(",");
    return this.timed("playlist_write", async () => {
      const result = await this.api.listentogether_sync_list_command({
        roomId: room.roomId, commandType: "REPLACE", userId: room.accountId,
        version: room.clientSeq++, displayList: ids, randomList: ids,
        cookie: this.cookieHeader(), timeout: 10_000,
      });
      requireCode(result, "playlist_replace");
    });
  }

  async setPlaylist({ songIds, initialSongId }) {
    const room = this.requireRoom();
    const { playlist, initial, durations } = await this.validatePlaylist(songIds, initialSongId);
    const previous = {
      playlist: room.playlist,
      durations: room.durations,
      currentIndex: room.currentIndex,
      playStatus: room.playStatus,
      progress: room.progress,
      playStartedAt: room.playStartedAt,
      queueVersion: room.queueVersion,
      errorCode: room.errorCode,
      lastServerSeq: room.lastServerSeq,
    };
    const former = room.playlist[room.currentIndex];
    room.playlist = playlist;
    room.durations = durations;
    room.currentIndex = playlist.indexOf(initial);
    try {
      await this.replacePlaylist();
      await this.sendPlayCommand("GOTO", initial, former);
      const confirmed = await this.confirmRemoteCommand(
        initial, "PLAY", previous.lastServerSeq,
      );
      const newerRemoteWon = !confirmed && room.lastServerSeq > previous.lastServerSeq;
      if (!confirmed && !newerRemoteWon) {
        throw new Phase0Error("command_sync_timeout");
      }
      await this.sendHeartbeat();
      if (confirmed) {
        room.queueVersion += 1;
        room.errorCode = null;
      }
    } catch (error) {
      Object.assign(room, previous);
      await this.replacePlaylist().catch(() => {});
      await this.sendPlayCommand("GOTO", former).catch(() => {});
      throw error;
    }
    return this.publicRoom({ playlistReplaced: true });
  }

  async command({ command, songId }) {
    const action = String(command || "").toUpperCase();
    if (!COMMANDS.has(action)) throw new Phase0Error("invalid_command");
    const room = this.requireRoom();
    let target = room.playlist[room.currentIndex];
    const former = target;
    const previousServerSeq = room.lastServerSeq;
    let targetIndex = room.currentIndex;
    if (action === "NEXT") {
      targetIndex = (room.currentIndex + 1) % room.playlist.length;
      target = room.playlist[targetIndex];
      await this.sendPlayCommand("GOTO", target, former);
    } else if (action === "PREVIOUS") {
      targetIndex = (room.currentIndex - 1 + room.playlist.length) % room.playlist.length;
      target = room.playlist[targetIndex];
      await this.sendPlayCommand("GOTO", target, former);
    } else if (action === "GOTO") {
      target = positiveId(songId);
      const index = room.playlist.indexOf(target);
      if (index < 0) throw new Phase0Error("song_not_in_playlist");
      targetIndex = index;
      await this.sendPlayCommand("GOTO", target, former);
    } else {
      await this.sendPlayCommand(action, target);
    }
    room.currentIndex = targetIndex;
    if (!(await this.confirmRemoteCommand(target, room.playStatus, previousServerSeq))) {
      throw new Phase0Error("command_sync_timeout");
    }
    return this.publicRoom({ commandAccepted: true, synchronized: true });
  }

  async confirmRemoteCommand(targetSongId, playStatus, previousServerSeq) {
    return this.timed("command_confirm", async () => {
      const started = this.monotonicNow();
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const result = await this.api.listentogether_sync_playlist_get({
          roomId: this.requireRoom().roomId,
          cookie: this.cookieHeader(), timeout: 1_200,
        });
        const body = requireCode(result, "command_status");
        await this.applyRemotePlaylist(body);
        const command = body?.data?.playCommand;
        if (String(command?.targetSongId) === String(targetSongId) &&
            String(command?.playStatus || "").toUpperCase() === playStatus) {
          return true;
        }
        const observedServerSeq = Number(command?.serverSeq);
        if (Number.isSafeInteger(observedServerSeq) &&
            observedServerSeq > previousServerSeq) return false;
        if (this.monotonicNow() - started >= 4_000) break;
        await delay(100);
      }
      return false;
    });
  }

  /// 当前这首歌的时长，拿不到就是 null（不知道就别装作知道）。
  currentDuration() {
    if (!this.room) return null;
    const id = this.room.playlist[this.room.currentIndex];
    const value = this.room.durations?.get(id);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  currentProgress() {
    if (!this.room) return 0;
    const room = this.room;
    let value = room.progress;
    if (room.playStatus === "PLAY" && Number.isFinite(room.playStartedAt)) {
      value += this.monotonicNow() - room.playStartedAt;
    }
    // 上界是歌本身的长度。没有它，进度会一直爬过结尾，对面的真实客户端
    // 放完这首之后就和房主对不上，显示同步失败。
    const duration = this.currentDuration();
    if (duration !== null) value = Math.min(value, duration);
    return Math.max(0, Math.floor(value));
  }

  /// 这首放完了没有。时长未知就永远当成没放完——宁可不切，也不要瞎切。
  currentTrackFinished() {
    const duration = this.currentDuration();
    if (duration === null || !this.room || this.room.playStatus !== "PLAY") return false;
    return this.currentProgress() >= duration;
  }

  async sendPlayCommand(commandType, targetSongId, formerSongId = null) {
    const room = this.requireRoom();
    let nextStatus = room.playStatus;
    let nextProgress = this.currentProgress();
    if (commandType === "PLAY") nextStatus = "PLAY";
    if (commandType === "PAUSE") nextStatus = "PAUSE";
    if (commandType === "GOTO") {
      nextStatus = "PLAY";
      nextProgress = 0;
    }
    const result = await this.api.listentogether_play_command({
      roomId: room.roomId, progress: nextProgress, commandType,
      formerSongId: formerSongId || "-1", targetSongId,
      clientSeq: room.clientSeq++, playStatus: nextStatus,
      cookie: this.cookieHeader(), timeout: 10_000,
    });
    requireCode(result, "play_command");
    room.progress = nextProgress;
    room.playStatus = nextStatus;
    room.playStartedAt = nextStatus === "PLAY" ? this.monotonicNow() : null;
  }

  /// 放完一首之后往下走：多首就切下一首，单首就重放这一首。
  ///
  /// 「停在结尾」不是一个真实播放器会有的状态——对面放完就走了，房主不动
  /// 就是同步失败。单首循环至少是一个真实客户端能跟上的行为。
  async advanceAfterTrackEnd() {
    const room = this.requireRoom();
    const former = room.playlist[room.currentIndex];
    const nextIndex = (room.currentIndex + 1) % room.playlist.length;
    const target = room.playlist[nextIndex];
    await this.sendPlayCommand("GOTO", target, former);
    room.currentIndex = nextIndex;
  }

  async sendHeartbeat() {
    const room = this.requireRoom();
    const result = await this.api.listentogether_heatbeat({
      roomId: room.roomId, songId: room.playlist[room.currentIndex],
      playStatus: room.playStatus, progress: this.currentProgress(),
      cookie: this.cookieHeader(), timeout: 10_000,
    });
    requireCode(result, "heartbeat");
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatFailureSince = null;
    this.heartbeatTimer = setInterval(() => this.heartbeatTick().catch(() => {}), 10_000);
    this.heartbeatTimer.unref?.();
  }

  async heartbeatTick() {
    if (this.heartbeatBusy || !this.room) return;
    this.heartbeatBusy = true;
    const room = this.room;
    const roomId = room.roomId;
    try {
      const [status, playlist] = await Promise.all([
        this.api.listentogether_status({ cookie: this.cookieHeader(), timeout: 10_000 }),
        this.api.listentogether_sync_playlist_get({
          roomId, cookie: this.cookieHeader(), timeout: 10_000,
        }),
      ]);
      if (!this.room || this.room.roomId !== roomId) return;
      const statusBody = requireCode(status, "room_status");
      await this.applyRemotePlaylist(requireCode(playlist, "playlist_status"));
      room.inRoom = Boolean(statusBody?.data?.inRoom);
      room.participantCount = Array.isArray(statusBody?.data?.roomInfo?.roomUsers)
        ? statusBody.data.roomInfo.roomUsers.length : 0;
      // 一首放完之后要真的往下走。不然进度停在结尾、房主永远卡在同一首，
      // 对面的真实客户端早就播完进下一首了，两边立刻对不上。
      if (this.currentTrackFinished()) await this.advanceAfterTrackEnd();
      await this.sendHeartbeat();
      this.heartbeatFailureSince = null;
      return true;
    } catch {
      const failedAt = this.monotonicNow();
      if (this.heartbeatFailureSince === null) this.heartbeatFailureSince = failedAt;
      if (failedAt - this.heartbeatFailureSince >= this.heartbeatFailureTimeoutMs) {
        const login = await this.checkLoginState();
        const authenticationExpired = login.reachable && !login.authenticated;
        if (authenticationExpired) this.clearSession();
        this.stopHeartbeat();
        this.room = null;
        this.lastTerminalState = {
          state: "failed",
          errorCode: authenticationExpired ? "authentication_expired" : "room_unreachable_timeout",
        };
      }
      return false;
    } finally {
      this.heartbeatBusy = false;
    }
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  requireRoom() {
    if (!this.room) throw new Phase0Error("room_not_active");
    return this.room;
  }

  async applyRemotePlaylist(body) {
    const room = this.requireRoom();
    const data = body?.data;
    const command = data?.playCommand;
    if (!command || typeof command !== "object") return false;
    const serverSeq = Number(command.serverSeq);
    if (!Number.isSafeInteger(serverSeq) || serverSeq <= room.lastServerSeq) return false;
    const playStatus = String(command.playStatus || "").toUpperCase();
    if (playStatus !== "PLAY" && playStatus !== "PAUSE") return false;
    const progress = Number(command.progress);
    if (!Number.isFinite(progress) || progress < 0 || progress > 24 * 60 * 60 * 1000) return false;

    let playlist = room.playlist;
    const remoteList = data?.playlist?.displayList?.result;
    if (Array.isArray(remoteList) && remoteList.length) {
      try { playlist = normalizeSongIds(remoteList); } catch { return false; }
    }
    let target;
    try { target = positiveId(command.targetSongId); } catch { return false; }
    const currentIndex = playlist.indexOf(target);
    if (currentIndex < 0) return false;

    const previousTarget = room.playlist[room.currentIndex];
    const queueChanged = previousTarget !== target ||
      playlist.length !== room.playlist.length ||
      playlist.some((id, index) => id !== room.playlist[index]);
    if (!queueChanged) {
      room.playStatus = playStatus;
      room.progress = Math.floor(progress);
      room.playStartedAt = playStatus === "PLAY" ? this.monotonicNow() : null;
      room.lastServerSeq = serverSeq;
      return true;
    }

    const details = await this.api.song_detail({
      ids: playlist.join(","), cookie: this.cookieHeader(), timeout: 10_000,
    });
    const detailBody = requireCode(details, "song_detail");
    const songs = new Map((detailBody.songs || []).map((song) => [String(song.id), song]));
    if (!playlist.every((id) => songs.has(id))) {
      await this.restoreAfterRightsConflict(serverSeq);
      return false;
    }
    const playable = await this.checkPlayableSongIds([target]);
    if (!playable.includes(target)) {
      await this.restoreAfterRightsConflict(serverSeq);
      return false;
    }

    const durations = new Map(room.durations);
    for (const song of songs.values()) {
      const ms = Number(song?.dt);
      if (Number.isFinite(ms) && ms > 0) durations.set(String(song.id), Math.floor(ms));
    }

    room.playlist = playlist;
    room.durations = durations;
    room.currentIndex = currentIndex;
    room.playStatus = playStatus;
    room.progress = Math.floor(progress);
    room.playStartedAt = playStatus === "PLAY" ? this.monotonicNow() : null;
    room.lastServerSeq = serverSeq;
    room.queueVersion += 1;
    room.errorCode = null;
    return true;
  }

  async restoreAfterRightsConflict(serverSeq) {
    const room = this.requireRoom();
    const previousTarget = room.playlist[room.currentIndex];
    // Do not adopt the incompatible remote queue. Re-publish the last queue
    // both accounts could play, then move playback back to its current song.
    // Consuming the rejected server sequence prevents every 3s status poll
    // from repeating the same rollback forever.
    await this.replacePlaylist();
    await this.sendPlayCommand("GOTO", previousTarget);
    await this.sendHeartbeat();
    room.lastServerSeq = serverSeq;
    room.errorCode = "counterpart_rights_unavailable";
  }

  async roomStatus() {
    if (!this.room) return this.publicRoom();
    return this.timed("status_refresh", async () => {
      const room = this.room;
      const playlist = await this.api.listentogether_sync_playlist_get({
        roomId: room.roomId, cookie: this.cookieHeader(), timeout: 10_000,
      });
      await this.applyRemotePlaylist(requireCode(playlist, "playlist_status"));
      return this.publicRoom();
    });
  }

  async endRoom() {
    if (!this.room) return { state: "idle" };
    const roomId = this.room.roomId;
    this.stopHeartbeat();
    this.heartbeatFailureSince = null;
    try {
      const result = await this.api.listentogether_end({
        roomId, cookie: this.cookieHeader(), timeout: 10_000,
      });
      const body = requireCode(result, "room_end");
      if (body?.data?.success !== true) throw new Phase0Error("room_end_unconfirmed");
    } finally {
      this.room = null;
      this.lastTerminalState = null;
    }
    return { state: "ended" };
  }
}

module.exports = {
  NeteasePhase0, Phase0Error, atomicWriteSecret,
  cookiesFromResponse, normalizeSongIds, positiveId, readSecret,
};
