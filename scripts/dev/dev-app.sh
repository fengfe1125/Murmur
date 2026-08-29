#!/usr/bin/env bash
#
# Run the iOS app against a Murmur App backend -- local, or the VPS over SSH.
#
#   ./scripts/dev/dev-app.sh                     # local API, build, install, launch
#   ./scripts/dev/dev-app.sh --api-only          # just the local API
#   ./scripts/dev/dev-app.sh --invite            # local permanent invite code
#   ./scripts/dev/dev-app.sh --vps user@host     # tunnel to the VPS API and launch
#   ./scripts/dev/dev-app.sh --vps user@host --invite   # invite code on the VPS
#
# Why a tunnel and not a public port: murmur-app-api binds 127.0.0.1 on purpose.
# Without a domain there is no public TLS certificate, so exposing 8766 would
# put the development token and every message on the wire in clear text.  The
# tunnel keeps the app pointed at 127.0.0.1:8766 -- no app config change at all
# -- and SSH does the encrypting.
#
# App Attest does not exist in the Simulator, so app and server agree on a
# shared development token instead.  Locally it lives in .env.dev-app, which is
# generated on first run and git-ignored.  For --vps the token is read from the
# server's own /opt/murmur/.env so the two can never drift apart.

set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$PWD"
ENV_FILE="$ROOT/.env.dev-app"
PY="${PY:-$ROOT/.venv/bin/python}"
PORT="${PORT:-8766}"
SCHEME=Murmur
BUNDLE_ID=com.sakura.Murmur
REMOTE_DIR=/opt/murmur

SSH_TARGET=""
WANT_INVITE=0
API_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --vps) SSH_TARGET="${2:?--vps needs user@host}"; shift 2 ;;
    --invite) WANT_INVITE=1; shift ;;
    --api-only) API_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

[ -x "$PY" ] || { echo "找不到 $PY，先建好 .venv" >&2; exit 1; }

remote() { ssh "$SSH_TARGET" "$@"; }

# ── VPS mode ───────────────────────────────────────────────────────────────
if [ -n "$SSH_TARGET" ]; then
  echo "→ 连接 $SSH_TARGET"
  remote true || { echo "SSH 连不上 $SSH_TARGET" >&2; exit 1; }

  # cd must happen inside sudo: /opt/murmur is 0700 murmur:murmur, so the
  # login user cannot even enter it.
  if [ "$WANT_INVITE" = 1 ]; then
    remote "sudo -u murmur bash -c 'cd $REMOTE_DIR && ./.venv/bin/murmur \
      app-invite --reusable --permanent --alias \"vps dev\"'"
    exit 0
  fi

  # The app must send exactly the token the server checks against.
  TOKEN=$(remote "sudo grep -m1 '^MURMUR_APP_DEVELOPMENT_TOKEN=' $REMOTE_DIR/.env \
    | cut -d= -f2-" 2>/dev/null | tr -d '\r\n' || true)
  if [ -z "$TOKEN" ]; then
    echo "读不到 VPS 上的 MURMUR_APP_DEVELOPMENT_TOKEN。" >&2
    echo "先在 VPS 的 $REMOTE_DIR/.env 里设好 development 模式和令牌，再重试。" >&2
    exit 1
  fi

  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "本机 $PORT 已被占用。先停掉本地 API，否则 App 会连到本地而不是 VPS。" >&2
    exit 1
  fi
  ssh -N -L "$PORT:127.0.0.1:$PORT" "$SSH_TARGET" &
  TUNNEL_PID=$!
  trap 'kill $TUNNEL_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 40); do
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
    sleep 0.25
  done
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 \
    || { echo "隧道没建起来" >&2; exit 1; }
  echo "→ 隧道就绪：本机 $PORT → $SSH_TARGET 的 127.0.0.1:$PORT"
else
  # ── Local mode ───────────────────────────────────────────────────────────
  # The token must survive restarts: the app's Keychain identity is bound to it.
  if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
MURMUR_APP_DB=$ROOT/murmur.db
MURMUR_APP_ATTEST_MODE=development
MURMUR_APP_ALLOW_DEVELOPMENT=1
MURMUR_APP_DEVELOPMENT_TOKEN=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')
MURMUR_APP_BASE_URL=http://127.0.0.1:$PORT
EOF
    chmod 600 "$ENV_FILE"
    echo "已生成 $ENV_FILE"
  fi
  set -a; . "$ENV_FILE"; set +a
  TOKEN="$MURMUR_APP_DEVELOPMENT_TOKEN"

  if [ "$WANT_INVITE" = 1 ]; then
    "$PY" -m murmur.cli app-invite --reusable --permanent --alias "dev"
    exit 0
  fi

  # Reuse a server that is already listening rather than failing on a bound port.
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "API 已在 $PORT 上运行，复用它"
  else
    "$PY" -m murmur.cli app-api --host 127.0.0.1 --port "$PORT" &
    API_PID=$!
    trap 'kill $API_PID 2>/dev/null || true' EXIT
    for _ in $(seq 1 40); do
      lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
      sleep 0.25
    done
    echo "API 已启动 (pid $API_PID)"
  fi

  if [ "$API_ONLY" = 1 ]; then
    echo "Ctrl-C 结束。"
    wait
  fi
fi

# ── Build and launch ───────────────────────────────────────────────────────
UDID=$(xcrun simctl list devices booted -j \
  | "$PY" -c 'import json,sys
ds=[d for v in json.load(sys.stdin)["devices"].values() for d in v if d["state"]=="Booted"]
print(ds[0]["udid"] if ds else "")')
[ -n "$UDID" ] || { echo "没有已启动的模拟器，先在 Xcode 里开一个" >&2; exit 1; }

DD="$ROOT/.build/dev-app"
# pipefail is on: without the if, a failed build still exits 0 here because
# grep happily matches "BUILD FAILED", and simctl would install a stale .app.
if ! xcodebuild -project apps/ios/MurmurApp.xcodeproj -scheme "$SCHEME" -configuration Debug \
  -sdk iphonesimulator -destination "id=$UDID" -derivedDataPath "$DD" build \
  | grep -E "error:|BUILD"; then
  echo "构建失败：不安装残留的旧 .app，先修好上面的 error。" >&2
  exit 1
fi

xcrun simctl terminate "$UDID" "$BUNDLE_ID" 2>/dev/null || true
xcrun simctl install "$UDID" "$DD/Build/Products/Debug-iphonesimulator/$SCHEME.app"
SIMCTL_CHILD_MURMUR_DEV_BYPASS_TOKEN="$TOKEN" \
  xcrun simctl launch "$UDID" "$BUNDLE_ID"

echo
if [ -n "$SSH_TARGET" ]; then
  echo "App 已连到 VPS。取邀请码：./scripts/dev/dev-app.sh --vps $SSH_TARGET --invite"
  echo "Ctrl-C 断开隧道。"
else
  echo "邀请码（长期有效，可重复兑换）："
  "$PY" -m murmur.cli app-invite --reusable --permanent --alias "dev $(date +%m-%d)"
  echo
  echo "Ctrl-C 结束 API。"
fi
wait
