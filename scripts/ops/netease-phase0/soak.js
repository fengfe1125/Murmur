#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

function validateStatus(value) {
  if (!value || value.state !== "active" || value.inRoom !== true) {
    throw new Error("room_not_connected");
  }
  if (!Number.isInteger(value.participantCount) || value.participantCount < 2) {
    throw new Error("participant_missing");
  }
  if (!Number.isInteger(value.playlistLength) || value.playlistLength < 1) {
    throw new Error("playlist_missing");
  }
  if (value.playStatus !== "PLAY" && value.playStatus !== "PAUSE") {
    throw new Error("play_state_invalid");
  }
  return true;
}

function writeReport(filePath, report) {
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(report, null, 2)}\n`, {
    encoding: "utf8", mode: 0o600, flag: "wx",
  });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, filePath);
  fs.chmodSync(filePath, 0o600);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function main() {
  const [rawBaseUrl, rawOutput, rawDuration = "7200", rawInterval = "30"] = process.argv.slice(2);
  const baseUrl = new URL(rawBaseUrl || "");
  const output = path.resolve(rawOutput || "");
  const durationSeconds = Number(rawDuration);
  const intervalSeconds = Number(rawInterval);
  if (baseUrl.protocol !== "http:" || baseUrl.hostname !== "127.0.0.1" || baseUrl.port !== "18763" ||
      baseUrl.search || baseUrl.hash || !/^\/[A-Za-z0-9_-]{32}\/$/.test(baseUrl.pathname) ||
      !output || !Number.isInteger(durationSeconds) || durationSeconds < 60 || durationSeconds > 8 * 60 * 60 ||
      !Number.isInteger(intervalSeconds) || intervalSeconds < 10 || intervalSeconds > 300) {
    throw new Error("safe loopback URL, output, duration and interval are required");
  }
  const started = Date.now();
  const report = {
    version: 1,
    pid: process.pid,
    state: "running",
    startedAt: new Date(started).toISOString(),
    targetDurationSeconds: durationSeconds,
    intervalSeconds,
    checks: 0,
    successes: 0,
    failures: 0,
    consecutiveFailures: 0,
    maxConsecutiveFailures: 0,
    trackChangesObserved: 0,
    lastCheckedAt: null,
    completedAt: null,
  };
  let lastTrack = null;
  while (Date.now() - started < durationSeconds * 1000) {
    report.checks += 1;
    try {
      const response = await fetch(new URL("api/room/status", baseUrl), {
        cache: "no-store", signal: AbortSignal.timeout(15_000),
      });
      if (!response.ok) throw new Error("status_http_error");
      const status = await response.json();
      validateStatus(status);
      const track = String(status.currentSongId || "");
      if (lastTrack && track && track !== lastTrack) report.trackChangesObserved += 1;
      lastTrack = track;
      report.successes += 1;
      report.consecutiveFailures = 0;
    } catch {
      report.failures += 1;
      report.consecutiveFailures += 1;
      report.maxConsecutiveFailures = Math.max(
        report.maxConsecutiveFailures, report.consecutiveFailures,
      );
    }
    report.lastCheckedAt = new Date().toISOString();
    writeReport(output, report);
    await delay(intervalSeconds * 1000);
  }
  report.state = "complete";
  report.completedAt = new Date().toISOString();
  writeReport(output, report);
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("SOAK_START_FAILED\n");
    process.exit(1);
  });
}

module.exports = { validateStatus, writeReport };
