#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const SESSION_COOKIE_NAMES = new Set([
  "MUSIC_U", "MUSIC_A", "__csrf", "NMTID", "WNMCID", "os", "appver",
]);

function sessionCookie(session) {
  const cookies = session?.cookies;
  if (!cookies || typeof cookies !== "object" || (!cookies.MUSIC_U && !cookies.MUSIC_A)) {
    throw new Error("authenticated_session_required");
  }
  return Object.entries(cookies)
    .filter(([name, value]) => SESSION_COOKIE_NAMES.has(name) &&
      typeof value === "string" && value && !/[\r\n;]/.test(value))
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
}

async function main() {
  const [apiRootArg, sessionPathArg, confirmation] = process.argv.slice(2);
  if (!apiRootArg || !sessionPathArg || confirmation !== "EXPIRE-TEST-SESSION") {
    throw new Error("explicit confirmation required");
  }
  const apiRoot = path.resolve(apiRootArg);
  const sessionPath = path.resolve(sessionPathArg);
  const session = JSON.parse(fs.readFileSync(sessionPath, "utf8"));
  const cookie = sessionCookie(session);
  const apiRequire = createRequire(path.join(apiRoot, "package.json"));
  const api = apiRequire("./main.js");
  const result = await api.logout({ cookie, timeout: 10_000 });
  if (Number(result?.body?.code) !== 200) throw new Error("logout_failed");
  process.stdout.write("SESSION_LOGOUT_REQUESTED\n");
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("SESSION_LOGOUT_FAILED\n");
    process.exit(1);
  });
}

module.exports = { sessionCookie };
