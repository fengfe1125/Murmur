"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { NeteasePhase0, Phase0Error, cookiesFromResponse } = require("./phase0");
const { safeShape } = require("./inspect-status");
const { positiveIds } = require("./check-playable");
const { validateStatus } = require("./soak");
const { buildCases, safeBaseUrl, summarize } = require("./command-benchmark");
const { sessionCookie } = require("./logout-session");
const { createFaultableApi } = require("./server");

function fakeApi(calls, remote) {
  const ok = (body = {}) => ({ body: { code: 200, ...body }, cookie: [] });
  return new Proxy({}, {
    get(_target, name) {
      return async (query) => {
        calls.push([name, query]);
        if (remote.failAll) throw new Error("network_unreachable");
        if (remote.authenticated === false && String(name).startsWith("listentogether_")) {
          return { body: { code: 301 }, cookie: [] };
        }
        if (name === "login_qr_key") return ok({ data: { unikey: "test-login-key" } });
        if (name === "login_qr_check") {
          return {
            body: { code: 803 },
            cookie: [
              "MUSIC_U=secret; Path=/", "__csrf=csrf; Path=/", "ignored=x; Path=/",
            ],
          };
        }
        if (name === "login_status") {
          if (remote.authenticated === false) {
            return { body: { data: { code: 301, profile: null } } };
          }
          return { body: { data: { code: 200, profile: { userId: 42 } } } };
        }
        if (name === "song_detail") {
          return ok({ songs: query.ids.split(",").map((id) => ({ id })) });
        }
        if (name === "listentogether_room_create") {
          remote.inRoom = true;
          remote.roomId = "room-7";
          return ok({ data: { roomInfo: { roomId: "room-7" } } });
        }
        if (name === "listentogether_status") {
          return ok({ data: {
            inRoom: remote.inRoom !== false,
            roomInfo: { roomId: remote.roomId, roomUsers: [{}, {}] },
          } });
        }
        if (name === "listentogether_sync_list_command") {
          remote.playlist = query.displayList.split(",");
        }
        if (name === "listentogether_sync_playlist_get" && remote.playCommand) {
          return ok({ data: {
            playCommand: remote.playCommand,
            playlist: { displayList: { result: remote.playlist } },
          } });
        }
        if (name === "listentogether_play_command" && remote.failPlayCommandOnce) {
          remote.failPlayCommandOnce = false;
          return { body: { code: 500 }, cookie: [] };
        }
        if (name === "listentogether_play_command") {
          remote.serverSeq = (remote.serverSeq || 0) + 1;
          remote.playCommand = {
            targetSongId: query.targetSongId,
            playStatus: query.playStatus,
            progress: query.progress,
            serverSeq: remote.serverSeq,
          };
        }
        if (name === "listentogether_end") remote.inRoom = false;
        return ok({ data: { success: true } });
      };
    },
  });
}

function fixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "murmur-netease-phase0-"));
  const sessionPath = path.join(directory, "secrets", "session.json");
  const calls = [];
  const remote = {};
  let currentTime = 1_700_000_000_000;
  const phase0 = new NeteasePhase0({
    api: fakeApi(calls, remote), qrToSvg: async () => "<svg></svg>", sessionPath,
    now: () => currentTime,
    monotonicNow: () => currentTime,
  });
  return {
    calls, directory, phase0, remote, sessionPath,
    advance(milliseconds) { currentTime += milliseconds; },
  };
}

test("QR login persists only allowlisted cookies with private permissions", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  assert.equal((await item.phase0.loginStart()).state, "waiting");
  assert.equal((await item.phase0.loginStatus()).state, "authenticated");
  const saved = JSON.parse(fs.readFileSync(item.sessionPath, "utf8"));
  assert.deepEqual(saved.cookies, { MUSIC_U: "secret", __csrf: "csrf" });
  assert.equal(fs.statSync(item.sessionPath).mode & 0o777, 0o600);
  assert.equal(fs.statSync(path.dirname(item.sessionPath)).mode & 0o777, 0o700);
});

test("cookie parsing rejects a QR response without an authenticated session", () => {
  assert.throws(
    () => cookiesFromResponse({ cookie: ["__csrf=value; Path=/"] }),
    (error) => error instanceof Phase0Error && error.code === "login_session_missing",
  );
});

test("protocol inspection redacts identity and secret values", () => {
  const safe = safeShape({
    code: 200,
    songId: 186016,
    progress: 1234,
    roomId: "room-secret",
    roomUsers: [{ userId: 42, nickname: "private", playStatus: "PLAY" }],
    cookie: "MUSIC_U=secret",
    message: "not allowlisted",
  });
  assert.equal(safe.code, 200);
  assert.equal(safe.songId, 186016);
  assert.equal(safe.progress, 1234);
  assert.equal(safe.roomId, "<redacted>");
  assert.equal(safe.roomUsers, "<redacted>");
  assert.equal(safe.cookie, "<redacted>");
  assert.equal(safe.message, "<string>");
});

test("playability checker accepts only a bounded list of decimal IDs", () => {
  assert.deepEqual(positiveIds(["11", "22", "11"]), ["11", "22"]);
  assert.throws(() => positiveIds(["not-an-id"]));
  assert.throws(() => positiveIds([]));
});

test("soak monitor accepts only a connected room with two participants", () => {
  assert.equal(validateStatus({
    state: "active", inRoom: true, participantCount: 2,
    playlistLength: 2, playStatus: "PLAY",
  }), true);
  assert.throws(() => validateStatus({
    state: "active", inRoom: true, participantCount: 1,
    playlistLength: 2, playStatus: "PLAY",
  }));
});

test("command benchmark builds 50 mixed commands and summarizes latency", () => {
  const cases = buildCases("11", "22");
  assert.equal(cases.length, 50);
  assert.deepEqual(new Set(cases.map((item) => item.command)), new Set([
    "PLAY", "PAUSE", "NEXT", "PREVIOUS", "GOTO",
  ]));
  assert.equal(safeBaseUrl(`http://127.0.0.1:18763/${"a".repeat(32)}/`).port, "18763");
  assert.throws(() => safeBaseUrl("http://example.com/unsafe/"));
  const results = Array.from({ length: 50 }, (_unused, index) => ({
    synchronized: index !== 49,
    latencyMs: index === 49 ? null : 200 + index,
  }));
  const summary = summarize(results);
  assert.equal(summary.synchronized, 49);
  assert.equal(summary.failed, 1);
  assert.equal(summary.withinThresholdRate, 0.98);
});

test("logout helper accepts only an authenticated allowlisted session shape", () => {
  assert.equal(sessionCookie({ cookies: {
    MUSIC_U: "secret", __csrf: "csrf", untrusted: "ignored",
  } }),
    "MUSIC_U=secret; __csrf=csrf");
  assert.throws(() => sessionCookie({ cookies: { __csrf: "csrf" } }));
});

test("fault injection blocks only the guarded experimental upstream", async () => {
  const controller = createFaultableApi({ ping: async () => "pong" });
  assert.equal(await controller.api.ping(), "pong");
  controller.set(true);
  await assert.rejects(controller.api.ping(), /upstream_network_fault/);
  controller.set(false);
  assert.equal(await controller.api.ping(), "pong");
});

test("room lifecycle sends playlist, control, status and end calls", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  const created = await item.phase0.createRoom({
    songIds: ["11", "22"], initialSongId: "11",
  });
  assert.equal(created.state, "active");
  assert.match(created.inviteUrl, /^https:\/\/st\.music\.163\.com\/listen-together\/share\//);
  assert.equal((await item.phase0.roomInviteQr()).qrSvg, "<svg></svg>");
  assert.equal((await item.phase0.command({ command: "NEXT" })).currentSongId, "22");
  assert.equal((await item.phase0.command({ command: "PREVIOUS" })).currentSongId, "11");
  assert.equal((await item.phase0.command({ command: "GOTO", songId: "22" })).currentSongId, "22");
  item.advance(12_345);
  const paused = await item.phase0.command({ command: "PAUSE" });
  assert.equal(paused.playStatus, "PAUSE");
  assert.equal(paused.progressMs, 12_345);
  const pauseCall = item.calls.filter(([name]) => name === "listentogether_play_command").at(-1);
  assert.equal(pauseCall[1].progress, 12_345);
  item.advance(5_000);
  assert.equal((await item.phase0.command({ command: "PLAY" })).progressMs, 12_345);
  assert.equal((await item.phase0.command({ command: "PAUSE" })).synchronized, true);
  const replaced = await item.phase0.setPlaylist({
    songIds: ["22", "33"], initialSongId: "33",
  });
  assert.equal(replaced.currentSongId, "33");
  assert.equal(replaced.playlistLength, 2);
  assert.equal(replaced.playlistReplaced, true);
  assert.equal((await item.phase0.roomStatus()).participantCount, 2);
  assert.equal((await item.phase0.endRoom()).state, "ended");
  const names = item.calls.map(([name]) => name);
  for (const expected of [
    "listentogether_room_create", "listentogether_sync_list_command",
    "listentogether_play_command", "listentogether_heatbeat",
    "listentogether_status", "listentogether_room_check",
    "listentogether_sync_playlist_get", "listentogether_end",
  ]) assert.ok(names.includes(expected), expected);
});

test("room rejects a target outside its validated playlist", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  await item.phase0.createRoom({ songIds: ["11"], initialSongId: "11" });
  await assert.rejects(
    item.phase0.command({ command: "GOTO", songId: "22" }),
    (error) => error instanceof Phase0Error && error.code === "song_not_in_playlist",
  );
  await item.phase0.endRoom();
});

test("failed active-room replacement restores the previous local and remote playlist", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  await item.phase0.createRoom({ songIds: ["11", "22"], initialSongId: "11" });
  item.remote.failPlayCommandOnce = true;
  await assert.rejects(item.phase0.setPlaylist({
    songIds: ["22", "33"], initialSongId: "33",
  }));
  const status = item.phase0.publicRoom();
  assert.equal(status.currentSongId, "11");
  assert.equal(status.playlistLength, 2);
  const playlistCalls = item.calls.filter(([name]) => name === "listentogether_sync_list_command");
  assert.equal(playlistCalls.at(-1)[1].displayList, "11,22");
  await item.phase0.endRoom();
});

test("a newer remote server sequence becomes the authoritative local state", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  await item.phase0.createRoom({ songIds: ["11", "22"], initialSongId: "22" });
  item.remote.playlist = ["11", "22"];
  item.remote.playCommand = {
    targetSongId: "11", playStatus: "PLAY", progress: 1713, serverSeq: 9,
  };
  const status = await item.phase0.roomStatus();
  assert.equal(status.currentSongId, "11");
  assert.equal(status.playStatus, "PLAY");
  assert.equal(status.progressMs, 1713);
  item.remote.playCommand = {
    targetSongId: "22", playStatus: "PAUSE", progress: 9000, serverSeq: 8,
  };
  assert.equal((await item.phase0.roomStatus()).currentSongId, "11");
  await item.phase0.endRoom();
});

test("ten minutes of unreachable heartbeats fail closed without clearing an unverified session", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  await item.phase0.createRoom({ songIds: ["11", "22"], initialSongId: "11" });
  item.remote.failAll = true;
  assert.equal(await item.phase0.heartbeatTick(), false);
  item.advance(10 * 60 * 1000 - 1);
  assert.equal(await item.phase0.heartbeatTick(), false);
  assert.equal(item.phase0.publicRoom().state, "active");
  item.advance(1);
  assert.equal(await item.phase0.heartbeatTick(), false);
  assert.deepEqual(item.phase0.publicRoom(), {
    state: "failed", errorCode: "room_unreachable_timeout",
  });
  assert.equal(fs.existsSync(item.sessionPath), true);
});

test("a transient login-status outage never deletes a valid stored session", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  item.remote.failAll = true;
  await assert.rejects(
    item.phase0.loginStatus(),
    (error) => error instanceof Phase0Error && error.code === "login_status_unreachable",
  );
  assert.equal(fs.existsSync(item.sessionPath), true);
});

test("confirmed authentication expiry clears the session and room capability", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  const phase0 = new NeteasePhase0({
    api: fakeApi(item.calls, item.remote), qrToSvg: async () => "<svg></svg>",
    sessionPath: item.sessionPath, now: () => Date.now(),
    monotonicNow: (() => {
      let value = 1_000;
      return () => { value += 1_000; return value; };
    })(),
    heartbeatFailureTimeoutMs: 1_000,
  });
  await phase0.loginStart();
  await phase0.loginStatus();
  await phase0.createRoom({ songIds: ["11"], initialSongId: "11" });
  item.remote.authenticated = false;
  item.remote.failAll = true;
  await phase0.heartbeatTick();
  item.remote.failAll = false;
  await phase0.heartbeatTick();
  assert.deepEqual(phase0.publicRoom(), {
    state: "failed", errorCode: "authentication_expired",
  });
  assert.equal(fs.existsSync(item.sessionPath), false);
});

test("startup cleanup ends a remotely active stale room", async (t) => {
  const item = fixture();
  t.after(() => fs.rmSync(item.directory, { recursive: true, force: true }));
  await item.phase0.loginStart();
  await item.phase0.loginStatus();
  item.remote.inRoom = true;
  item.remote.roomId = "stale-room";
  assert.deepEqual(await item.phase0.closeStaleRemoteRoom(), { state: "stale_room_ended" });
  assert.equal(item.remote.inRoom, false);
});
