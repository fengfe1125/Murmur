#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

function positiveId(value) {
  const text = String(value ?? "").trim();
  if (!/^[1-9][0-9]{0,19}$/.test(text)) throw new Error("invalid_song_id");
  return text;
}

function safeBaseUrl(value) {
  const result = new URL(value || "");
  if (result.protocol !== "http:" || result.hostname !== "127.0.0.1" ||
      result.port !== "18763" || result.search || result.hash ||
      !/^\/[A-Za-z0-9_-]{32}\/$/.test(result.pathname)) {
    throw new Error("safe_loopback_url_required");
  }
  return result;
}

function buildCases(firstSongId, secondSongId) {
  const first = positiveId(firstSongId);
  const second = positiveId(secondSongId);
  if (first === second) throw new Error("two_distinct_song_ids_required");
  const cycle = [
    { command: "GOTO", songId: first, expectedSongId: first, expectedStatus: "PLAY" },
    { command: "PAUSE", expectedSongId: first, expectedStatus: "PAUSE" },
    { command: "PLAY", expectedSongId: first, expectedStatus: "PLAY" },
    { command: "NEXT", expectedSongId: second, expectedStatus: "PLAY" },
    { command: "PAUSE", expectedSongId: second, expectedStatus: "PAUSE" },
    { command: "PLAY", expectedSongId: second, expectedStatus: "PLAY" },
    { command: "PREVIOUS", expectedSongId: first, expectedStatus: "PLAY" },
    { command: "GOTO", songId: second, expectedSongId: second, expectedStatus: "PLAY" },
    { command: "PAUSE", expectedSongId: second, expectedStatus: "PAUSE" },
    { command: "PLAY", expectedSongId: second, expectedStatus: "PLAY" },
  ];
  return Array.from({ length: 5 }, () => cycle.map((item) => ({ ...item }))).flat();
}

function percentile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)];
}

function summarize(results, thresholdMs = 2_000) {
  const synchronized = results.filter((item) => item.synchronized);
  const latencies = synchronized.map((item) => item.latencyMs);
  const verifiedReads = results.filter((item) =>
    item.independentlyVerified && Number.isFinite(item.statusLatencyMs));
  const statusLatencies = verifiedReads.map((item) => item.statusLatencyMs);
  const withinThreshold = synchronized.filter((item) => item.latencyMs <= thresholdMs).length;
  return {
    commands: results.length,
    synchronized: synchronized.length,
    failed: results.length - synchronized.length,
    withinThreshold,
    withinThresholdRate: results.length ? withinThreshold / results.length : 0,
    minLatencyMs: latencies.length ? Math.min(...latencies) : null,
    medianLatencyMs: percentile(latencies, 0.5),
    p95LatencyMs: percentile(latencies, 0.95),
    maxLatencyMs: latencies.length ? Math.max(...latencies) : null,
    statusReads: verifiedReads.length,
    statusMedianLatencyMs: percentile(statusLatencies, 0.5),
    statusP95LatencyMs: percentile(statusLatencies, 0.95),
    statusMaxLatencyMs: statusLatencies.length ? Math.max(...statusLatencies) : null,
  };
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

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store", signal: AbortSignal.timeout(15_000), ...options,
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(String(body?.error || "http_error"));
  return body;
}

function snapshotMatches(status, item) {
  return status.state === "active" && status.inRoom === true &&
    String(status.currentSongId) === item.expectedSongId &&
    status.playStatus === item.expectedStatus;
}

async function main() {
  const [rawBaseUrl, rawOutput, rawFirst, rawSecond] = process.argv.slice(2);
  const baseUrl = safeBaseUrl(rawBaseUrl);
  const output = path.resolve(rawOutput || "");
  if (!rawOutput) throw new Error("output_required");
  const cases = buildCases(rawFirst, rawSecond);
  const thresholdMs = 2_000;
  const startedAt = new Date();
  const results = [];
  const report = {
    version: 1,
    state: "running",
    startedAt: startedAt.toISOString(),
    completedAt: null,
    thresholdMs,
    summary: summarize(results, thresholdMs),
    results,
  };
  writeReport(output, report);

  for (const [index, item] of cases.entries()) {
    const started = Date.now();
    let synchronized = false;
    let independentlyVerified = false;
    let latencyMs = null;
    let statusLatencyMs = null;
    let error = null;
    try {
      const payload = { command: item.command };
      if (item.songId) payload.songId = item.songId;
      const accepted = await jsonRequest(new URL("api/room/command", baseUrl), {
        method: "POST", body: JSON.stringify(payload),
      });
      latencyMs = Date.now() - started;
      synchronized = accepted.commandAccepted === true && accepted.synchronized === true &&
        String(accepted.currentSongId) === item.expectedSongId &&
        accepted.playStatus === item.expectedStatus;
      if (!synchronized) throw new Error("command_not_synchronized");
      const statusStarted = Date.now();
      const status = await jsonRequest(new URL("api/room/status", baseUrl));
      statusLatencyMs = Date.now() - statusStarted;
      independentlyVerified = snapshotMatches(status, item);
      if (!independentlyVerified) throw new Error("independent_snapshot_mismatch");
    } catch (caught) {
      synchronized = false;
      error = String(caught?.message || "command_failed").slice(0, 80);
    }
    results.push({
      sequence: index + 1, command: item.command, synchronized,
      independentlyVerified, latencyMs, statusLatencyMs, error,
    });
    report.summary = summarize(results, thresholdMs);
    writeReport(output, report);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  report.state = "complete";
  report.completedAt = new Date().toISOString();
  report.summary = summarize(results, thresholdMs);
  writeReport(output, report);
  if (report.summary.synchronized !== 50 || report.summary.withinThresholdRate < 0.95 ||
      report.summary.statusReads !== 50 || report.summary.statusP95LatencyMs > thresholdMs) {
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("COMMAND_BENCHMARK_FAILED\n");
    process.exit(1);
  });
}

module.exports = { buildCases, safeBaseUrl, summarize };
