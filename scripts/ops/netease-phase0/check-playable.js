#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

function positiveIds(values) {
  const ids = [...new Set(values.map((value) => String(value).trim()))];
  if (!ids.length || ids.length > 10 || ids.some((id) => !/^[1-9][0-9]{0,19}$/.test(id))) {
    throw new Error("1-10 positive song IDs are required");
  }
  return ids;
}

async function main() {
  const [apiRoot, sessionPath, ...rawIds] = process.argv.slice(2);
  if (!apiRoot || !sessionPath) throw new Error("api root and session path are required");
  const ids = positiveIds(rawIds);
  const session = JSON.parse(fs.readFileSync(path.resolve(sessionPath), "utf8"));
  const cookie = Object.entries(session.cookies || {}).map(([name, value]) => `${name}=${value}`).join("; ");
  if (!cookie) throw new Error("session missing");
  const apiRequire = createRequire(path.join(path.resolve(apiRoot), "package.json"));
  const api = apiRequire("./main.js");
  const detail = await api.song_detail({ ids: ids.join(","), cookie, timeout: 10_000 });
  if (Number(detail?.body?.code) !== 200 || !Array.isArray(detail?.body?.songs)) {
    throw new Error("song detail failed");
  }
  const songs = new Map(detail.body.songs.map((song) => [String(song.id), song]));
  const results = [];
  for (const id of ids) {
    const song = songs.get(id);
    const checked = await api.check_music({ id, cookie, timeout: 10_000 });
    results.push({
      id,
      title: typeof song?.name === "string" ? song.name.slice(0, 200) : "",
      artists: Array.isArray(song?.ar)
        ? song.ar.map((artist) => String(artist?.name || "").slice(0, 120)).filter(Boolean).slice(0, 8)
        : [],
      playable: checked?.body?.success === true,
    });
  }
  process.stdout.write(`${JSON.stringify(results, null, 2)}\n`);
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("SAFE_PLAYABILITY_CHECK_FAILED\n");
    process.exit(1);
  });
}

module.exports = { positiveIds };
