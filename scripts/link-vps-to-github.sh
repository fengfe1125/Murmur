#!/usr/bin/env bash
# Give one VPS a read-only GitHub Deploy Key and install the verified updater.
# Run locally once; it never prints or stores the generated private key locally.

set -euo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=""
SSH_TARGET=""
GCE_INSTANCE=""
GCE_ZONE=""
GCE_PROJECT=""
REPOSITORY=""

usage() {
  cat <<'EOF'
Usage:
  ./scripts/link-vps-to-github.sh --repo OWNER/REPO --ssh user@host
  ./scripts/link-vps-to-github.sh --repo OWNER/REPO --gcloud INSTANCE --zone ZONE --project PROJECT

Creates a read-only GitHub Deploy Key for one VPS. The VPS keeps .env, SQLite,
logs, dossiers, photos and WeChat state; only tracked source code is reset.
EOF
}

while (($#)); do
  case "$1" in
    --repo) REPOSITORY="${2:?--repo needs OWNER/REPO}"; shift 2 ;;
    --ssh) MODE=ssh; SSH_TARGET="${2:?--ssh needs user@host}"; shift 2 ;;
    --gcloud) MODE=gcloud; GCE_INSTANCE="${2:?--gcloud needs an instance}"; shift 2 ;;
    --zone) GCE_ZONE="${2:?--zone needs a value}"; shift 2 ;;
    --project) GCE_PROJECT="${2:?--project needs a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo "--repo must be OWNER/REPO" >&2; exit 2;
}
if [[ "$MODE" == ssh && -n "$SSH_TARGET" ]]; then :
elif [[ "$MODE" == gcloud && -n "$GCE_INSTANCE" && -n "$GCE_ZONE" && -n "$GCE_PROJECT" ]]; then :
else usage >&2; exit 2
fi

command -v gh >/dev/null 2>&1 || { echo "GitHub CLI (gh) is required." >&2; exit 2; }
gh auth status >/dev/null

remote_run() {
  if [[ "$MODE" == ssh ]]; then ssh "$SSH_TARGET" "$@"
  else gcloud compute ssh "$GCE_INSTANCE" --zone "$GCE_ZONE" --project "$GCE_PROJECT" --command "$*"
  fi
}

remote_copy() {
  local local_file="$1" remote_file="$2" attempt
  for attempt in 1 2 3; do
    if [[ "$MODE" == ssh ]]; then
      scp "$local_file" "$SSH_TARGET:$remote_file" && return 0
    elif gcloud compute scp "$local_file" "$GCE_INSTANCE:$remote_file" \
      --zone "$GCE_ZONE" --project "$GCE_PROJECT"; then
      return 0
    fi
    if (( attempt < 3 )); then
      echo "Upload interrupted; retrying ($attempt/3)…" >&2
      sleep 3
    fi
  done
  return 1
}

STAMP=$(date +%Y%m%d-%H%M%S)
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/murmur-github.XXXXXX")
KEY_FILE="$WORK_DIR/deploy_key"
BOOTSTRAP="$WORK_DIR/bootstrap.sh"
REMOTE_KEY="/tmp/murmur-github-key-$STAMP"
REMOTE_BOOTSTRAP="/tmp/murmur-github-bootstrap-$STAMP.sh"
KEY_ID=""
COMPLETE=0

cleanup() {
  rm -rf -- "$WORK_DIR"
  if (( ! COMPLETE )) && [[ -n "$KEY_ID" ]]; then
    gh api --method DELETE "repos/$REPOSITORY/keys/$KEY_ID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "==> Checking VPS connection"
remote_run true

echo "==> Creating a read-only Deploy Key"
ssh-keygen -q -t ed25519 -N '' -f "$KEY_FILE" -C "murmur-vps-$STAMP"
KEY_ID=$(gh api --method POST "repos/$REPOSITORY/keys" \
  -f "title=Murmur VPS $STAMP" \
  -f "key=$(<"$KEY_FILE.pub")" \
  -F read_only=true --jq .id)

cat > "$BOOTSTRAP" <<EOF
#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/murmur
REPOSITORY=$REPOSITORY
BRANCH=main
KEY_FILE=$REMOTE_KEY

install -d -o murmur -g murmur -m 0700 "\$APP_DIR/.ssh"
install -o murmur -g murmur -m 0600 "\$KEY_FILE" "\$APP_DIR/.ssh/id_ed25519"
cat > "\$APP_DIR/.ssh/config" <<'CONFIG'
Host github.com
  HostName github.com
  User git
  IdentityFile /opt/murmur/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
CONFIG
chown murmur:murmur "\$APP_DIR/.ssh/config"
chmod 600 "\$APP_DIR/.ssh/config"

sudo -u murmur -H git -C "\$APP_DIR" init
sudo -u murmur -H git -C "\$APP_DIR" remote remove origin 2>/dev/null || true
sudo -u murmur -H git -C "\$APP_DIR" remote add origin "git@github.com:\$REPOSITORY.git"
sudo -u murmur -H git -C "\$APP_DIR" fetch --prune origin "\$BRANCH"

for service in murmur-web murmur-telegram murmur-dingtalk murmur-wechat murmur-qq; do
  systemctl stop "\$service" 2>/dev/null || true
done
sudo -u murmur -H git -C "\$APP_DIR" reset --hard "origin/\$BRANCH"
chown -R murmur:murmur "\$APP_DIR"
chmod 600 "\$APP_DIR/.env"
sudo -u murmur -H bash -c '
  cd /opt/murmur
  .venv/bin/pip install -e .
  for test in tests/test_*.py; do .venv/bin/python "\$test"; done
'
install -m 0755 "\$APP_DIR/deploy/murmur-update" /usr/local/sbin/murmur-update
install -m 0644 "\$APP_DIR/deploy/murmur-update.service" /etc/systemd/system/murmur-update.service
install -m 0644 "\$APP_DIR/deploy/"*.service /etc/systemd/system/
install -m 0644 "\$APP_DIR/deploy/murmur-logrotate" /etc/logrotate.d/murmur
systemctl daemon-reload
for service in murmur-web murmur-telegram murmur-dingtalk murmur-wechat murmur-qq; do
  if systemctl is-enabled --quiet "\$service"; then
    systemctl restart "\$service"
    systemctl is-active --quiet "\$service"
  fi
done
rm -f "\$KEY_FILE" "$REMOTE_BOOTSTRAP"
echo "GitHub updates enabled: sudo systemctl start murmur-update"
EOF
chmod 700 "$BOOTSTRAP"

remote_copy "$KEY_FILE" "$REMOTE_KEY"
remote_copy "$BOOTSTRAP" "$REMOTE_BOOTSTRAP"
echo "==> Linking VPS to GitHub and validating the checked-out code"
remote_run "sudo bash $REMOTE_BOOTSTRAP"

COMPLETE=1
echo "==> Done. VPS pulls from git@github.com:$REPOSITORY.git"
