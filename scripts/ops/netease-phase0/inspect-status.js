#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const VALUE_KEYS = /^(code|inRoom|playStatus|progress|songId|targetSongId|formerSongId|commandType|clientSeq|serverSeq|version|status|position|paused|playing)$/i;
const REDACT_KEYS = /(cookie|token|csrf|secret|user|nickname|avatar|roomId|inviter|account)/i;

function safeShape(value, key = "", depth = 0) {
  if (depth > 8) return "<depth-limit>";
  if (REDACT_KEYS.test(key)) return "<redacted>";
  if (value === null) return null;
  if (Array.isArray(value)) {
    return { length: value.length, sample: value.slice(0, 3).map((item) => safeShape(item, key, depth + 1)) };
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([childKey, child]) => [childKey, safeShape(child, childKey, depth + 1)]),
    );
  }
  if (VALUE_KEYS.test(key) && ["string", "number", "boolean"].includes(typeof value)) return value;
  return `<${typeof value}>`;
}

async function main() {
  const [apiRoot, sessionPath, roomId] = process.argv.slice(2);
  if (!apiRoot || !sessionPath || !/^[A-Za-z0-9_-]{8,200}$/.test(roomId || "")) {
    throw new Error("api root, session path and room id are required");
  }
  const session = JSON.parse(fs.readFileSync(path.resolve(sessionPath), "utf8"));
  const cookie = Object.entries(session.cookies || {}).map(([name, value]) => `${name}=${value}`).join("; ");
  if (!cookie) throw new Error("session missing");
  const apiRequire = createRequire(path.join(path.resolve(apiRoot), "package.json"));
  const api = apiRequire("./main.js");
  const options = { cookie, timeout: 10_000 };
  const [status, check, playlist] = await Promise.all([
    api.listentogether_status(options),
    api.listentogether_room_check({ ...options, roomId }),
    api.listentogether_sync_playlist_get({ ...options, roomId }),
  ]);
  process.stdout.write(`${JSON.stringify({
    status: safeShape(status?.body),
    check: safeShape(check?.body),
    playlist: safeShape(playlist?.body),
  }, null, 2)}\n`);
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("SAFE_STATUS_INSPECTION_FAILED\n");
    process.exit(1);
  });
}

module.exports = { safeShape };
