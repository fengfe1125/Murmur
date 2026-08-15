#!/usr/bin/env bash
#
# Stage Murmur on a VPS, then perform a guarded cutover from this machine.
# The final cutover requires typing CUTOVER: it stops only discovered Murmur
# processes, takes a consistent SQLite backup, transfers state over SSH, and
# starts only the official App services. Platform credentials never cause a
# test bot to be enabled.

set -euo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
MODE=""
SSH_TARGET=""
GCE_INSTANCE=""
GCE_ZONE=""
GCE_PROJECT=""
LOCAL_ALREADY_STOPPED=0

usage() {
  cat <<'EOF'
Usage:
  ./scripts/migrate-to-vps.sh --ssh user@host
  ./scripts/migrate-to-vps.sh --gcloud INSTANCE --zone ZONE --project PROJECT

Options:
  --local-stopped  The Murmur processes on this computer are already stopped.
  -h, --help       Show this help.

The script stages code first.  It transfers .env, the SQLite database,
dossiers and photo previews only after you type CUTOVER.
EOF
}

while (($#)); do
  case "$1" in
    --ssh) MODE="ssh"; SSH_TARGET="${2:?--ssh needs user@host}"; shift 2 ;;
    --gcloud) MODE="gcloud"; GCE_INSTANCE="${2:?--gcloud needs an instance}"; shift 2 ;;
    --zone) GCE_ZONE="${2:?--zone needs a value}"; shift 2 ;;
    --project) GCE_PROJECT="${2:?--project needs a value}"; shift 2 ;;
    --local-stopped) LOCAL_ALREADY_STOPPED=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$MODE" == "ssh" && -n "$SSH_TARGET" ]]; then
  :
elif [[ "$MODE" == "gcloud" && -n "$GCE_INSTANCE" && -n "$GCE_ZONE" && -n "$GCE_PROJECT" ]]; then
  :
else
  usage >&2
  exit 2
fi

[[ -x "$PYTHON_BIN" ]] || { echo "Missing $PYTHON_BIN; create the local .venv first." >&2; exit 2; }
[[ -f "$ROOT_DIR/.env" ]] || { echo "Missing $ROOT_DIR/.env; refusing to migrate without production configuration." >&2; exit 2; }
CONFIG_INFO=$(cd "$ROOT_DIR" && "$PYTHON_BIN" - "$ROOT_DIR/.env" <<'PY'
import sys

from dotenv import load_dotenv

load_dotenv(sys.argv[1], override=True)

from murmur.app_settings import AppSettings
from murmur.config import Config

cfg = Config.load()
settings = AppSettings.from_env(cfg)
settings.validate()
paths = {
    cfg.db_path.resolve(),
    settings.db_path.resolve(),
    settings.memory_db_path.resolve(),
}
if len(paths) != 1:
    raise SystemExit(
        "migrate-to-vps requires MURMUR_DB, MURMUR_APP_DB and "
        "MURMUR_APP_MEMORY_DB to resolve to the same SQLite file"
    )
database = paths.pop()
data_root = settings.data_root.resolve()
if data_root != database.parent:
    raise SystemExit(
        "migrate-to-vps requires MURMUR_APP_DATA_ROOT to be the database directory"
    )
for value in (
    database,
    data_root,
    settings.upload_dir.resolve(),
    settings.attest_mode,
    settings.public_base_url,
    settings.apns_key_path or "",
):
    print(value)
PY
)
DB_PATH=$(printf '%s\n' "$CONFIG_INFO" | sed -n '1p')
STATE_ROOT=$(printf '%s\n' "$CONFIG_INFO" | sed -n '2p')
UPLOAD_DIR=$(printf '%s\n' "$CONFIG_INFO" | sed -n '3p')
APP_MODE=$(printf '%s\n' "$CONFIG_INFO" | sed -n '4p')
PUBLIC_BASE_URL=$(printf '%s\n' "$CONFIG_INFO" | sed -n '5p')
APNS_KEY_PATH=$(printf '%s\n' "$CONFIG_INFO" | sed -n '6p')
[[ -f "$DB_PATH" ]] || { echo "Missing configured database: $DB_PATH" >&2; exit 2; }

assert_queue_drained() {
  "$PYTHON_BIN" - "$DB_PATH" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_jobs'"
    ).fetchone()
    pending = 0 if not exists else db.execute(
        "SELECT COUNT(*) FROM app_jobs WHERE status IN ('queued','processing')"
    ).fetchone()[0]
if pending:
    raise SystemExit(
        f"refusing cutover with {pending} queued/processing App moment(s); "
        "wait for the queue to drain before retrying"
    )
PY
}

# The migration deliberately does not copy raw uploads.  A drained queue means
# every durable moment is already terminal and its original has been removed.
assert_queue_drained

for command in tar; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing command: $command" >&2; exit 2; }
done

remote_run() {
  if [[ "$MODE" == "ssh" ]]; then
    ssh "$SSH_TARGET" "$@"
  else
    gcloud compute ssh "$GCE_INSTANCE" --zone "$GCE_ZONE" --project "$GCE_PROJECT" --command "$*"
  fi
}

remote_copy() {
  local local_file="$1" remote_file="$2"
  if [[ "$MODE" == "ssh" ]]; then
    scp "$local_file" "$SSH_TARGET:$remote_file"
  else
    gcloud compute scp "$local_file" "$GCE_INSTANCE:$remote_file" \
      --zone "$GCE_ZONE" --project "$GCE_PROJECT"
  fi
}

pids_to_csv() {
  # macOS ps accepts a comma-separated PID list; pgrep returns one PID per line.
  printf '%s\n' "$1" | tr '\n' ',' | sed 's/,$//'
}

STAMP=$(date +%Y%m%d-%H%M%S)
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/murmur-migrate.XXXXXX")
cleanup() { rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT

SOURCE_ARCHIVE="$WORK_DIR/murmur-source-$STAMP.tar.gz"
STATE_DIR="$WORK_DIR/state"
STATE_ARCHIVE="$WORK_DIR/murmur-state-$STAMP.tar.gz"
PREPARE_SCRIPT="$WORK_DIR/murmur-prepare-$STAMP.sh"
ACTIVATE_SCRIPT="$WORK_DIR/murmur-activate-$STAMP.sh"
REMOTE_SOURCE="/tmp/murmur-source-$STAMP.tar.gz"
REMOTE_STATE="/tmp/murmur-state-$STAMP.tar.gz"
REMOTE_PREPARE="/tmp/murmur-prepare-$STAMP.sh"
REMOTE_ACTIVATE="/tmp/murmur-activate-$STAMP.sh"
RELEASE_DIR="/opt/murmur.release-$STAMP"

echo "==> Checking remote connection"
remote_run "true"

echo "==> Packaging source code (secrets and personal data excluded)"
COPYFILE_DISABLE=1 tar -czf "$SOURCE_ARCHIVE" \
  --exclude='Murmur/.git' \
  --exclude='Murmur/.venv' \
  --exclude='Murmur/.env' \
  --exclude='Murmur/.env.test-bots' \
  --exclude='Murmur/*.local.xcconfig' \
  --exclude='Murmur/*.p8' \
  --exclude='Murmur/*.p12' \
  --exclude='Murmur/*.mobileprovision' \
  --exclude='*.local.xcconfig' \
  --exclude='*.p8' \
  --exclude='*.p12' \
  --exclude='*.mobileprovision' \
  --exclude='Murmur/*.db' \
  --exclude='Murmur/*.db-*' \
  --exclude='Murmur/logs' \
  --exclude='Murmur/dossiers' \
  --exclude='Murmur/photos' \
  --exclude='Murmur/app-uploads' \
  --exclude='Murmur/app-locks' \
  --exclude='Murmur/openclaw' \
  --exclude='Murmur/wechat' \
  --exclude='Murmur/test' \
  --exclude='Murmur/**/__pycache__' \
  --exclude='Murmur/**/.DS_Store' \
  --exclude='Murmur/**/._*' \
  -C "$(dirname "$ROOT_DIR")" "$(basename "$ROOT_DIR")"

cat > "$PREPARE_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
id -u murmur >/dev/null 2>&1 || useradd -r -m -d /opt/murmur -s /bin/bash murmur
apt-get update
apt-get install -y python3-venv curl
install -d -o murmur -g murmur -m 0750 "$RELEASE_DIR"
tar -xzf "$REMOTE_SOURCE" -C "$RELEASE_DIR" --strip-components=1 --no-same-owner
chown -R murmur:murmur "$RELEASE_DIR"
install -d -o murmur -g murmur -m 0750 "$RELEASE_DIR/logs"
sudo -u murmur -H bash -c '
  cd "'$RELEASE_DIR'"
  python3 -m venv .venv
  .venv/bin/pip install -e .
  .venv/bin/python -m compileall -q murmur
  for test in tests/test_*.py; do .venv/bin/python "\$test"; done
'
cp "$RELEASE_DIR/deploy/"*.service /etc/systemd/system/
cp "$RELEASE_DIR/deploy/murmur-logrotate" /etc/logrotate.d/murmur
systemctl daemon-reload
rm -f "$REMOTE_SOURCE" "$REMOTE_PREPARE"
EOF
chmod 700 "$PREPARE_SCRIPT"

remote_copy "$SOURCE_ARCHIVE" "$REMOTE_SOURCE"
remote_copy "$PREPARE_SCRIPT" "$REMOTE_PREPARE"
echo "==> Preparing remote release and running its tests (bots remain stopped)"
remote_run "sudo bash $REMOTE_PREPARE"

echo "==> Verifying remote HTTPS and Apple secret prerequisites before downtime"
remote_run "command -v caddy >/dev/null 2>&1 && sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null && sudo grep -q '127.0.0.1:8766' /etc/caddy/Caddyfile"
if [[ "$APP_MODE" == "production" ]]; then
  [[ "$APNS_KEY_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]] || {
    echo "Production APNs key path must be an absolute, shell-safe remote path (recommended: /etc/murmur/AuthKey_<KEYID>.p8)." >&2
    exit 2
  }
  [[ "$APNS_KEY_PATH" != /opt/murmur/* ]] || {
    echo "Keep the APNs key outside /opt/murmur so release directory swaps cannot move or overwrite it; use /etc/murmur/." >&2
    exit 2
  }
  APNS_KEY_QUOTED=$(printf '%q' "$APNS_KEY_PATH")
  remote_run "sudo test -f $APNS_KEY_QUOTED && test \"\$(sudo stat -c %a $APNS_KEY_QUOTED)\" = 600 && sudo -u murmur test -r $APNS_KEY_QUOTED"
fi

MURMUR_PROCESS_PATTERN='murmur (bot|dingtalk|wechat|qq|poke|reply|app-invite|app-device-code|app-api|app-worker|web)( |$)|(^|/)run\.sh( |$)'
if (( ! LOCAL_ALREADY_STOPPED )); then
  command -v pgrep >/dev/null 2>&1 || { echo "pgrep is required to stop local bots safely." >&2; exit 2; }
  LOCAL_PIDS=$(pgrep -f "$MURMUR_PROCESS_PATTERN" || true)
  if [[ -z "$LOCAL_PIDS" ]]; then
    echo "No local Murmur process was found. Stop it manually, then re-run with --local-stopped." >&2
    exit 2
  fi
  echo "==> Local Murmur processes that will be stopped:"
  ps -p "$(pids_to_csv "$LOCAL_PIDS")" -o pid=,etime=,comm=
  printf 'Type CUTOVER to stop only these processes and hand over to the VPS: '
  read -r CONFIRMATION
  [[ "$CONFIRMATION" == "CUTOVER" ]] || { echo "Cutover cancelled; remote release remains staged and stopped."; exit 0; }
  kill -TERM $LOCAL_PIDS
  sleep 3
  REMAINING_PIDS=$(pgrep -f "$MURMUR_PROCESS_PATTERN" || true)
  if [[ -n "$REMAINING_PIDS" ]]; then
    echo "Some local Murmur processes are still running; refusing to snapshot or start remote services:" >&2
    ps -p "$(pids_to_csv "$REMAINING_PIDS")" -o pid=,etime=,comm= >&2 || true
    exit 1
  fi
else
  printf 'Type CUTOVER to confirm that local Murmur processes are already stopped: '
  read -r CONFIRMATION
  [[ "$CONFIRMATION" == "CUTOVER" ]] || { echo "Cutover cancelled; remote release remains staged and stopped."; exit 0; }
  command -v pgrep >/dev/null 2>&1 || { echo "pgrep is required to verify local services are stopped." >&2; exit 2; }
  REMAINING_PIDS=$(pgrep -f "$MURMUR_PROCESS_PATTERN" || true)
  if [[ -n "$REMAINING_PIDS" ]]; then
    echo "--local-stopped was supplied, but Murmur processes are still running:" >&2
    ps -p "$(pids_to_csv "$REMAINING_PIDS")" -o pid=,etime=,comm= >&2 || true
    exit 1
  fi
fi

assert_queue_drained

echo "==> Creating a consistent SQLite snapshot and private state archive"
install -d -m 700 "$STATE_DIR"
"$PYTHON_BIN" - "$DB_PATH" "$STATE_DIR/murmur.db" <<'PY'
import sqlite3
import sys

source, destination = sys.argv[1:]
with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
    src.backup(dst)
PY
for directory in dossiers photos; do
  if [[ -d "$STATE_ROOT/$directory" ]]; then
    cp -R "$STATE_ROOT/$directory" "$STATE_DIR/$directory"
  fi
done
cp "$ROOT_DIR/.env" "$STATE_DIR/.env"
"$PYTHON_BIN" "$ROOT_DIR/scripts/sanitize_production_env.py" \
  "$STATE_DIR/.env" --remote-paths
chmod 600 "$STATE_DIR/.env" "$STATE_DIR/murmur.db"
for directory in dossiers photos; do
  if [[ -d "$STATE_DIR/$directory" ]]; then
    find "$STATE_DIR/$directory" -type d -exec chmod 700 {} +
    find "$STATE_DIR/$directory" -type f -exec chmod 600 {} +
  fi
done
printf 'created_at=%s\nsource=%s\n' "$(date -u +%FT%TZ)" "$ROOT_DIR" > "$STATE_DIR/manifest.txt"
COPYFILE_DISABLE=1 tar -czf "$STATE_ARCHIVE" -C "$STATE_DIR" .

PUBLIC_BASE_URL_QUOTED=$(printf '%q' "$PUBLIC_BASE_URL")
cat > "$ACTIVATE_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
OLD_DIR="/opt/murmur"
RELEASE_DIR="$RELEASE_DIR"
PUBLIC_BASE_URL=$PUBLIC_BASE_URL_QUOTED
OLD_TOKEN=""
if [[ -f "\$OLD_DIR/.env" ]]; then
  OLD_TOKEN=\$(sed -n 's/^MURMUR_WEB_TOKEN=//p' "\$OLD_DIR/.env" | tail -n1)
fi
for service in murmur-telegram murmur-dingtalk murmur-wechat murmur-qq murmur-app-api murmur-app-worker murmur-web; do
  systemctl stop "\$service" 2>/dev/null || true
done
if [[ -d "\$OLD_DIR" ]]; then
  mv "\$OLD_DIR" "/opt/murmur.previous-$STAMP"
fi
mv "\$RELEASE_DIR" "\$OLD_DIR"
tar -xzf "$REMOTE_STATE" -C "\$OLD_DIR"
if [[ -n "\$OLD_TOKEN" ]] && ! grep -q '^MURMUR_WEB_TOKEN=.' "\$OLD_DIR/.env"; then
  tmp=\$(mktemp)
  grep -v '^MURMUR_WEB_TOKEN=' "\$OLD_DIR/.env" > "\$tmp" || true
  printf 'MURMUR_WEB_TOKEN=%s\\n' "\$OLD_TOKEN" >> "\$tmp"
  mv "\$tmp" "\$OLD_DIR/.env"
fi
chown -R murmur:murmur "\$OLD_DIR"
chmod 750 "\$OLD_DIR"
chmod 600 "\$OLD_DIR/.env"
find "\$OLD_DIR" -maxdepth 1 -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) -exec chmod 600 {} +
for private_dir in dossiers photos; do
  if [[ -d "\$OLD_DIR/\$private_dir" ]]; then
    find "\$OLD_DIR/\$private_dir" -type d -exec chmod 700 {} +
    find "\$OLD_DIR/\$private_dir" -type f -exec chmod 600 {} +
  fi
done
install -d -o murmur -g murmur -m 0700 "\$OLD_DIR/logs"
install -d -o murmur -g murmur -m 0700 "\$OLD_DIR/test/logs"
install -d -o murmur -g murmur -m 0700 "\$OLD_DIR/app-uploads" "\$OLD_DIR/app-locks"
# Console entry points embed the absolute virtualenv path.  The release was
# built under /opt/murmur.release-* and has just moved, so rebuild it here.
sudo -u murmur -H bash -c '
  cd /opt/murmur
  rm -rf .venv
  python3 -m venv .venv
  .venv/bin/pip install -e .
'
cp "\$OLD_DIR/deploy/"*.service /etc/systemd/system/
cp "\$OLD_DIR/deploy/murmur-logrotate" /etc/logrotate.d/murmur
systemctl daemon-reload
caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl reload caddy
# A credential is not authorization to run a test channel. Keep every legacy
# bot disabled; an operator can later enable one with .env.test-bots on an
# isolated host. Official services are the only automatic activation here.
systemctl disable murmur-telegram murmur-dingtalk murmur-wechat murmur-qq 2>/dev/null || true
systemctl enable --now murmur-app-worker murmur-app-api murmur-web
sleep 6
systemctl is-active --quiet murmur-app-worker
systemctl is-active --quiet murmur-app-api
systemctl is-active --quiet murmur-web
curl --fail --silent --show-error --max-time 20 --output /dev/null \
  --header 'Content-Type: application/json' \
  --data '{"purpose":"enrollment"}' \
  "\$PUBLIC_BASE_URL/v1/auth/challenges"
systemctl --no-pager --full status murmur-app-worker murmur-app-api murmur-web || true
rm -f "$REMOTE_STATE" "$REMOTE_ACTIVATE"
EOF
chmod 700 "$ACTIVATE_SCRIPT"

remote_copy "$STATE_ARCHIVE" "$REMOTE_STATE"
remote_copy "$ACTIVATE_SCRIPT" "$REMOTE_ACTIVATE"
echo "==> Activating remote release and starting official App services"
remote_run "sudo bash $REMOTE_ACTIVATE"

echo "==> Cutover complete"
echo "App API passed its HTTPS challenge check at $PUBLIC_BASE_URL; dashboard remains on 127.0.0.1:8765."
echo "Test bots remain disabled regardless of copied credentials. Keep /opt/murmur.previous-$STAMP until App acceptance passes."
