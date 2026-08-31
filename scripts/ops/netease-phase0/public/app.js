"use strict";

const base = window.location.pathname.replace(/\/$/, "");
const byId = (id) => document.getElementById(id);
let pollTimer = null;

async function request(route, options = {}) {
  const response = await fetch(`${base}${route}`, {
    cache: "no-store", headers: { "Content-Type": "application/json" }, ...options,
  });
  const body = await response.json();
  if (!response.ok || body.error) throw new Error(body.error || "request_failed");
  byId("error").textContent = "";
  return body;
}

function roomEnabled(enabled) {
  document.querySelectorAll("[data-command], #goto, #refresh, #end, #playlist-replace").forEach((element) => {
    element.disabled = !enabled;
  });
}

function renderRoom(room) {
  if (room.state !== "active") {
    byId("room-state").textContent = room.state === "ended" ? "房间已结束" : "房间未创建";
    byId("invite").hidden = true;
    byId("invite-qr").hidden = true;
    roomEnabled(false);
    return;
  }
  const joined = typeof room.participantCount === "number" ? `，成员 ${room.participantCount} 人` : "";
  const progress = Number.isFinite(room.progressMs) ? `，${Math.floor(room.progressMs / 1000)} 秒` : "";
  byId("room-state").textContent = `活动中：歌曲 ${room.currentSongId}，${room.playStatus}${progress}${joined}`;
  byId("invite").href = room.inviteUrl;
  byId("invite").hidden = false;
  byId("goto-song-id").value = room.currentSongId;
  roomEnabled(true);
}

async function renderInviteQr() {
  const result = await request("/api/room/invite-qr");
  byId("invite-qr").innerHTML = result.qrSvg;
  byId("invite-qr").hidden = false;
}

function showError(error) {
  byId("error").textContent = `操作失败：${error.message}`;
}

async function pollLogin() {
  try {
    const status = await request("/api/login/status");
    const labels = {
      not_started: "尚未开始", waiting: "等待扫码", scanned: "已扫码，请在手机上确认",
      authenticated: "登录成功，会话已安全保存", expired: "二维码或登录会话已过期",
    };
    byId("login-state").textContent = labels[status.state] || status.state;
    if (status.state === "authenticated") {
      clearInterval(pollTimer);
      byId("room-create").disabled = false;
      byId("qr").hidden = true;
    }
  } catch (error) { showError(error); }
}

byId("login-start").addEventListener("click", async () => {
  try {
    const result = await request("/api/login/start", { method: "POST", body: "{}" });
    byId("qr").innerHTML = result.qrSvg;
    byId("qr").hidden = false;
    byId("login-state").textContent = "等待扫码";
    clearInterval(pollTimer);
    pollTimer = setInterval(pollLogin, 2500);
  } catch (error) { showError(error); }
});

byId("room-create").addEventListener("click", async () => {
  try {
    const songIds = byId("song-ids").value.split(",").map((item) => item.trim()).filter(Boolean);
    renderRoom(await request("/api/room/create", {
      method: "POST",
      body: JSON.stringify({ songIds, initialSongId: byId("initial-song-id").value.trim() }),
    }));
    await renderInviteQr();
  } catch (error) { showError(error); }
});

byId("playlist-replace").addEventListener("click", async () => {
  try {
    const songIds = byId("song-ids").value.split(",").map((item) => item.trim()).filter(Boolean);
    renderRoom(await request("/api/room/playlist", {
      method: "POST",
      body: JSON.stringify({ songIds, initialSongId: byId("initial-song-id").value.trim() }),
    }));
  } catch (error) { showError(error); }
});

document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      renderRoom(await request("/api/room/command", {
        method: "POST", body: JSON.stringify({ command: button.dataset.command }),
      }));
    } catch (error) { showError(error); }
  });
});

byId("goto").addEventListener("click", async () => {
  try {
    renderRoom(await request("/api/room/command", {
      method: "POST",
      body: JSON.stringify({ command: "GOTO", songId: byId("goto-song-id").value.trim() }),
    }));
  } catch (error) { showError(error); }
});

byId("refresh").addEventListener("click", async () => {
  try { renderRoom(await request("/api/room/status")); } catch (error) { showError(error); }
});

byId("end").addEventListener("click", async () => {
  try {
    renderRoom(await request("/api/room/end", { method: "POST", body: "{}" }));
  } catch (error) { showError(error); }
});

pollLogin();
