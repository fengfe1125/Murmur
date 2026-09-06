#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { createRequire } = require("node:module");
const { NeteasePhase0, Phase0Error } = require("./phase0");

function argument(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function sendJson(response, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": data.length,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
  response.end(data);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let length = 0;
    request.on("data", (chunk) => {
      length += chunk.length;
      if (length > 8 * 1024) {
        reject(new Phase0Error("request_too_large"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      if (!chunks.length) return resolve({});
      try {
        const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        resolve(parsed && typeof parsed === "object" ? parsed : {});
      } catch {
        reject(new Phase0Error("invalid_json"));
      }
    });
    request.on("error", reject);
  });
}

function staticFile(response, filePath, contentType) {
  const data = fs.readFileSync(filePath);
  response.writeHead(200, {
    "Content-Type": contentType,
    "Content-Length": data.length,
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
  response.end(data);
}

function writePrivateFile(filePath, contents) {
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${contents}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, filePath);
  fs.chmodSync(filePath, 0o600);
}

function createFaultableApi(api) {
  let faultEnabled = false;
  const guarded = new Proxy(api, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver);
      if (typeof value !== "function") return value;
      return (...args) => {
        if (faultEnabled) return Promise.reject(new Error("upstream_network_fault"));
        return value.apply(target, args);
      };
    },
  });
  return {
    api: guarded,
    set(enabled) { faultEnabled = enabled === true; },
    get enabled() { return faultEnabled; },
  };
}

function createServer({ phase0, capability, publicDirectory, faultController = null }) {
  const prefix = `/${capability}`;
  let mutation = Promise.resolve();
  const serialize = (operation) => {
    const pending = mutation.then(operation, operation);
    mutation = pending.catch(() => {});
    return pending;
  };
  return http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://127.0.0.1");
      if (!url.pathname.startsWith(`${prefix}/`) && url.pathname !== prefix) {
        sendJson(response, 404, { error: "not_found" });
        return;
      }
      const route = url.pathname.slice(prefix.length) || "/";
      if (request.method === "GET" && route === "/") {
        staticFile(response, path.join(publicDirectory, "index.html"), "text/html; charset=utf-8");
        return;
      }
      if (request.method === "GET" && route === "/app.js") {
        staticFile(response, path.join(publicDirectory, "app.js"), "text/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && route === "/style.css") {
        staticFile(response, path.join(publicDirectory, "style.css"), "text/css; charset=utf-8");
        return;
      }

      let result;
      if (request.method === "POST" && route === "/api/login/start") {
        result = await serialize(() => phase0.loginStart());
      } else if (request.method === "GET" && route === "/api/login/status") {
        result = await phase0.loginStatus();
      } else if (request.method === "POST" && route === "/api/music/playability") {
        const body = await readJson(request);
        result = { playableSongIds: await phase0.playableSongIds(body.songIds) };
      } else if (request.method === "POST" && route === "/api/room/create") {
        const body = await readJson(request);
        result = await serialize(() => phase0.createRoom(body));
      } else if (request.method === "GET" && route === "/api/room/status") {
        result = await phase0.roomStatus();
      } else if (request.method === "GET" && route === "/api/room/invite-qr") {
        result = await phase0.roomInviteQr();
      } else if (request.method === "POST" && route === "/api/room/playlist") {
        const body = await readJson(request);
        result = await serialize(() => phase0.setPlaylist(body));
      } else if (request.method === "POST" && route === "/api/room/command") {
        const body = await readJson(request);
        result = await serialize(() => phase0.command(body));
      } else if (request.method === "POST" && route === "/api/room/end") {
        result = await serialize(() => phase0.endRoom());
      } else if (request.method === "POST" && route === "/api/fault/upstream" && faultController) {
        const body = await readJson(request);
        if (typeof body.enabled !== "boolean") throw new Phase0Error("invalid_request");
        faultController.set(body.enabled);
        result = { enabled: faultController.enabled };
      } else {
        sendJson(response, 404, { error: "not_found" });
        return;
      }
      sendJson(response, 200, result);
    } catch (error) {
      const code = error instanceof Phase0Error ? error.code : "internal_error";
      sendJson(response, code === "invalid_request" ? 400 : 502, { error: code });
    }
  });
}

/// 能力值默认每次启动重新随机——研究工具只要一个一次性的私密地址。
///
/// 但接进 Murmur 之后它同时是 worker 的接入地址：每次重启换一个，worker 就会
/// 一直 404，直到有人手工改配置再重启。给了 --capability-file 就把它固定下来：
/// 文件在就复用，不在就生成并以 0600 落盘。
function resolveCapability(capabilityFileArg) {
  if (!capabilityFileArg) return crypto.randomBytes(24).toString("base64url");
  const capabilityFile = path.resolve(capabilityFileArg);
  if (fs.existsSync(capabilityFile)) {
    const existing = fs.readFileSync(capabilityFile, "utf8").trim();
    // 只认自己写过的形状：路径里塞进别的东西会变成一个静默的开放端点。
    if (!/^[A-Za-z0-9_-]{32,64}$/.test(existing)) {
      throw new Error("capability file does not contain a valid capability");
    }
    return existing;
  }
  const created = crypto.randomBytes(24).toString("base64url");
  writePrivateFile(capabilityFile, created);
  return created;
}

async function main() {
  const apiRootArg = argument("api-root", "");
  const stateDirectoryArg = argument("state-dir", "");
  const host = argument("host", "127.0.0.1");
  const port = Number(argument("port", "18763"));
  const runtimeDirectoryArg = argument("runtime-dir", "");
  const allowFaultInjection = argument("allow-fault-injection", "0") === "1";
  if (!apiRootArg || !stateDirectoryArg || host !== "127.0.0.1" ||
      !Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error("safe --api-root, --state-dir, --host 127.0.0.1 and --port are required");
  }
  const apiRoot = path.resolve(apiRootArg);
  const stateDirectory = path.resolve(stateDirectoryArg);
  const runtimeDirectory = runtimeDirectoryArg ? path.resolve(runtimeDirectoryArg) : null;
  const apiPackage = path.join(apiRoot, "package.json");
  if (!fs.existsSync(apiPackage)) throw new Error("NetEase API root is missing");
  const apiRequire = createRequire(apiPackage);
  const upstreamApi = apiRequire("./main.js");
  const faultController = createFaultableApi(upstreamApi);
  const QRCode = apiRequire("qrcode");
  const capability = resolveCapability(argument("capability-file", ""));
  const phase0 = new NeteasePhase0({
    api: faultController.api,
    qrToSvg: (value) => QRCode.toString(value, {
      type: "svg", errorCorrectionLevel: "M", margin: 2,
    }),
    sessionPath: path.join(stateDirectory, "bot-session.json"),
    recordTiming: (sample) => {
      // Stage-only telemetry: never add IDs, room/account data, search text or
      // request bodies here. Journal aggregation can derive P50/P95.
      process.stdout.write(`PHASE0_TIMING ${JSON.stringify(sample)}\n`);
    },
  });
  const server = createServer({
    phase0, capability, publicDirectory: path.join(__dirname, "public"),
    faultController: allowFaultInjection ? faultController : null,
  });
  await phase0.closeStaleRemoteRoom();
  server.requestTimeout = 15_000;
  server.headersTimeout = 17_000;
  let readyFile = null;
  let pidFile = null;
  if (runtimeDirectory) {
    fs.mkdirSync(runtimeDirectory, { recursive: true, mode: 0o700 });
    fs.chmodSync(runtimeDirectory, 0o700);
    readyFile = path.join(runtimeDirectory, "phase0-ready.txt");
    pidFile = path.join(runtimeDirectory, "phase0.pid");
    writePrivateFile(pidFile, String(process.pid));
  }
  server.listen(port, host, () => {
    const ready = `http://${host}:${port}/${capability}/`;
    if (readyFile) writePrivateFile(readyFile, ready);
    else process.stdout.write(`PHASE0_READY ${ready}\n`);
  });

  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    await phase0.endRoom().catch(() => {});
    for (const filePath of [readyFile, pidFile]) {
      if (!filePath) continue;
      try { fs.unlinkSync(filePath); } catch (error) {
        if (error.code !== "ENOENT") process.exitCode = 1;
      }
    }
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 12_000).unref();
  };
  process.on("SIGINT", close);
  process.on("SIGTERM", close);
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("PHASE0_START_FAILED\n");
    process.exit(1);
  });
}

module.exports = {
  createFaultableApi, createServer, readJson, resolveCapability, sendJson, writePrivateFile,
};
