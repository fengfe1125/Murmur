#!/usr/bin/env bash
#
# Murmur interactive setup.  It never starts a bot or exposes a port.
# Run from the repository root: ./scripts/setup-murmur.sh
#

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# Wizard library — identical to the shared wizard template. Do not hand-edit.
# ──────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
  BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RED=$(tput setaf 1)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

TOTAL_STAGES=0
_STAGE_INDEX=0
ENV_FILE="${ENV_FILE:-.env}"
WRITTEN_ENV=()
WRITTEN_SECRET=()
SKIPPED=()

_clear() {
  [[ -t 1 ]] || return 0
  if command -v tput >/dev/null 2>&1; then tput clear; else printf '\033[2J\033[3J\033[H'; fi
}

banner() {
  _clear
  printf '\n%s%s  %s%s\n' "$BOLD" "$BLUE" "$1" "$RESET"
  printf '%s  %s stages%s\n\n' "$DIM" "$TOTAL_STAGES" "$RESET"
  printf '%s  You drive the browser; this wizard tells you exactly what to do and\n' "$DIM"
  printf '  captures the values you copy back. Stop any time with Ctrl-C and re-run\n'
  printf '  later — it remembers values already saved.%s\n' "$RESET"
  pause "Ready to start?"
}

stage() {
  _clear
  _STAGE_INDEX=$((_STAGE_INDEX + 1))
  printf '\n%s%s▸ Stage %s/%s · %s%s\n' "$BOLD" "$BLUE" "$_STAGE_INDEX" "$TOTAL_STAGES" "$1" "$RESET"
}

say() { printf '  %s\n' "$1"; }
step() { printf '  %s•%s %s\n' "$BLUE" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

open_url() {
  local url="$1"
  printf '  %s↗ opening%s %s\n' "$GREEN" "$RESET" "$url"
  { if command -v wslview >/dev/null 2>&1; then wslview "$url"
    elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url"
    elif command -v open >/dev/null 2>&1; then open "$url"
    else warn "couldn't open a browser — visit it manually: $url"; fi
  } >/dev/null 2>&1 || warn "couldn't open a browser — visit it manually: $url"
}

pause() {
  printf '  %s%s%s ' "$DIM" "${1:-Press Enter to continue}" "$RESET"
  read -r _ || true
}

confirm() {
  local reply=""
  printf '  %s? %s [y/N] ' "$YELLOW" "$1"
  read -r reply || true
  [[ "$reply" =~ ^[Yy] ]]
}

_existing() {
  [[ -f "$ENV_FILE" ]] || return 1
  local line; line=$(grep -E "^${1}=" "$ENV_FILE" | tail -n1) || return 1
  printf '%s' "${line#*=}"
}

_existing_from() {
  local file="$1" key="$2" line
  [[ -f "$file" ]] || return 1
  line=$(grep -E "^${key}=" "$file" | tail -n1) || return 1
  printf '%s' "${line#*=}"
}

ask() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -r input || true
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

ask_secret() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -rs input || true
  printf '\n'
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

write_env() {
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  tmp=$(mktemp)
  grep -vE "^${key}=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  WRITTEN_ENV+=("$key")
  printf '  %s✓ wrote%s %s → %s\n' "$GREEN" "$RESET" "$key" "$ENV_FILE"
}

set_secret() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if printf '%s' "$value" | gh secret set "$name" >/dev/null 2>&1; then
      WRITTEN_SECRET+=("$name")
      printf '  %s✓ set%s GitHub secret %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub secret $name (set it manually: gh secret set $name)")
  warn "skipped GitHub secret $name — gh not ready; set it later"
}

set_var() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if gh variable set "$name" --body "$value" >/dev/null 2>&1; then
      printf '  %s✓ set%s GitHub variable %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub variable $name")
  warn "skipped GitHub variable $name — gh not ready; set it later"
}

finish() {
  _clear
  printf '\n%s%s  ✓ Setup complete%s\n' "$BOLD" "$GREEN" "$RESET"
  (( ${#WRITTEN_ENV[@]} )) && note "wrote ${#WRITTEN_ENV[@]} value(s) to $ENV_FILE: ${WRITTEN_ENV[*]}"
  (( ${#WRITTEN_SECRET[@]} )) && note "set ${#WRITTEN_SECRET[@]} GitHub secret(s): ${WRITTEN_SECRET[*]}"
  if (( ${#SKIPPED[@]} )); then
    printf '\n'; warn "still to do by hand:"
    for s in "${SKIPPED[@]}"; do note "  - $s"; done
  fi
  printf '\n'
}

# ──────────────────────────────────────────────────────────────────────────
# STAGES — Murmur setup.
# ──────────────────────────────────────────────────────────────────────────

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"
ENV_FILE="$ROOT_DIR/.env"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TOTAL_STAGES=9

has_platform() {
  [[ ",$SELECTED_PLATFORMS," == *",$1,"* ]]
}

require_value() {
  local value="$1" label="$2"
  if [[ -z "$value" || "$value" == "sk-..." ]]; then
    warn "$label 不能为空；未写入配置。重新运行此向导后可以继续。"
    exit 2
  fi
}

banner "Murmur installation & initialization"

stage "Environment and isolated Python runtime"
say "Project: $ROOT_DIR"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  warn "找不到 $PYTHON_BIN。请安装 Python 3.11 或更高版本后重新运行。"
  exit 2
fi
PYTHON_VERSION=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! $PYTHON_BIN -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  warn "当前 Python $PYTHON_VERSION，Murmur 需要 Python 3.11 或更高版本。"
  exit 2
fi
say "检测到 Python $PYTHON_VERSION。"
# `venv --help` succeeds even on Ubuntu's minimized Python where ensurepip is
# absent, so test ensurepip itself before we try to create a real environment.
if ! $PYTHON_BIN -m ensurepip --version >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    VENV_PACKAGE="python${PYTHON_VERSION}-venv"
    warn "缺少 venv 组件：$VENV_PACKAGE。"
    if confirm "安装这个系统包吗？（会执行 sudo apt-get install）"; then
      sudo apt-get install -y "$VENV_PACKAGE"
    else
      exit 2
    fi
  else
    warn "Python 缺少 venv。请为 Python $PYTHON_VERSION 安装 venv 组件后重试。"
    exit 2
  fi
fi
if confirm "创建或更新 $ROOT_DIR/.venv 并安装项目依赖吗？"; then
  if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    $PYTHON_BIN -m venv .venv
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e .
else
  SKIPPED+=("Python 依赖安装")
fi

stage "Model gateway API"
say "所有平台共用一个支持图片输入的 OpenAI 兼容模型网关。"
open_url "https://opencode.ai/zen"
ask MODEL_GATEWAY "输入 1 使用 OpenCode Zen（默认），2 使用自定义 OpenAI 兼容网关："
MODEL_GATEWAY=${MODEL_GATEWAY:-1}
case "$MODEL_GATEWAY" in
  1)
    ask_secret OPENCODE_API_KEY "粘贴 OpenCode Zen API Key："
    require_value "$OPENCODE_API_KEY" "OPENCODE_API_KEY"
    write_env OPENCODE_API_KEY "$OPENCODE_API_KEY"
    write_env MURMUR_API_KEY ""
    write_env MURMUR_BASE_URL "https://opencode.ai/zen/go/v1"
    ;;
  2)
    ask MURMUR_BASE_URL "粘贴兼容网关的 Base URL（例如 https://api.example.com/v1）："
    ask_secret MURMUR_API_KEY "粘贴该网关的 API Key："
    require_value "$MURMUR_BASE_URL" "MURMUR_BASE_URL"
    require_value "$MURMUR_API_KEY" "MURMUR_API_KEY"
    write_env MURMUR_BASE_URL "$MURMUR_BASE_URL"
    write_env MURMUR_API_KEY "$MURMUR_API_KEY"
    write_env OPENCODE_API_KEY ""
    ;;
  *) warn "只支持 1 或 2。"; exit 2 ;;
esac
ask MURMUR_MODEL "模型名（默认 qwen3.7-plus）："
write_env MURMUR_MODEL "${MURMUR_MODEL:-qwen3.7-plus}"
ask MURMUR_MEMORY_MODEL "记忆整理模型（留空则跟随主模型；建议 mimo-v2.5）："
write_env MURMUR_MEMORY_MODEL "$MURMUR_MEMORY_MODEL"

# .env is the official App environment. Platform credentials alone must never
# make a bot runnable, so keep its test switch off here even during transition.
CURRENT_CHANNEL_MODE=$(_existing MURMUR_CHANNEL_MODE || true)
case "$CURRENT_CHANNEL_MODE" in
  transition|app_only) ;;
  *) CURRENT_CHANNEL_MODE=transition ;;
esac
write_env MURMUR_CHANNEL_MODE "$CURRENT_CHANNEL_MODE"
write_env MURMUR_ENABLE_TEST_BOTS "0"
write_env MURMUR_AUTO_ENROLL "0"

stage "Choose messaging platforms"
say "这些平台只作为隔离测试 Bot：1 Telegram  2 钉钉  3 个人微信  4 QQ。"
say "示例：1,4。留空表示只配置正式 App；凭据不会写入生产 .env。"
ask SELECTED_PLATFORMS "输入编号："
SELECTED_PLATFORMS=${SELECTED_PLATFORMS// /}
if [[ -n "$SELECTED_PLATFORMS" && ! "$SELECTED_PLATFORMS" =~ ^[1-4](,[1-4])*$ ]]; then
  warn "平台编号格式不正确。请使用如 1,3 或留空。"
  exit 2
fi
PRODUCTION_ENV_FILE="$ENV_FILE"
TEST_BOT_ENV_FILE="$ROOT_DIR/.env.test-bots"
PLATFORM_ENV_KEYS=(
  TELEGRAM_BOT_TOKEN MURMUR_ALLOWED_CHAT_IDS
  DINGTALK_CLIENT_ID DINGTALK_CLIENT_SECRET DINGTALK_ALLOWED_USERS
  DINGTALK_INITIATIVE_USERS
  WECHAT_TOKEN WECHAT_ACCOUNT_ID WECHAT_ALLOWED_USERS WECHAT_INITIATIVE
  QQ_APP_ID QQ_CLIENT_SECRET QQ_ALLOWED_USERS QQ_INITIATIVE QQ_SANDBOX
)

# Older installations kept live platform credentials in the production file.
# Move them to the isolated test file before asking any questions, without ever
# printing their values.  The test switch stays closed unless the operator
# explicitly selects at least one platform below.
MIGRATED_PLATFORM_VALUES=0
for key in "${PLATFORM_ENV_KEYS[@]}"; do
  value=$(_existing_from "$PRODUCTION_ENV_FILE" "$key" || true)
  if [[ -n "$value" ]]; then
    ENV_FILE="$TEST_BOT_ENV_FILE"
    write_env "$key" "$value"
    MIGRATED_PLATFORM_VALUES=1
  fi
done
if (( MIGRATED_PLATFORM_VALUES )); then
  ENV_FILE="$TEST_BOT_ENV_FILE"
  write_env MURMUR_CHANNEL_MODE "transition"
  write_env MURMUR_ENABLE_TEST_BOTS "0"
  write_env MURMUR_AUTO_ENROLL "0"
  write_env MURMUR_DB "./test/murmur.db"
  write_env MURMUR_LOGDIR "./test/logs"
  chmod 600 "$TEST_BOT_ENV_FILE"
  note "已把旧平台凭据移入隔离测试文件；测试开关仍保持关闭。"
fi
ENV_FILE="$PRODUCTION_ENV_FILE"
# Strip the platform keys from the production file right after copying them
# out: the wizard advertises Ctrl-C resumability, and exiting before the
# final stages must not leave live credentials in production.  Idempotent,
# so a rerun on an already-clean file is a no-op.
"$PYTHON_BIN" "$ROOT_DIR/scripts/sanitize_production_env.py" "$PRODUCTION_ENV_FILE"
note "生产 .env 已移除 Telegram / 钉钉 / 微信 / QQ 凭据。"
if [[ -n "$SELECTED_PLATFORMS" ]]; then
  ENV_FILE="$TEST_BOT_ENV_FILE"
  write_env MURMUR_CHANNEL_MODE "transition"
  write_env MURMUR_ENABLE_TEST_BOTS "1"
  write_env MURMUR_AUTO_ENROLL "0"
  write_env MURMUR_DB "./test/murmur.db"
  write_env MURMUR_LOGDIR "./test/logs"
  write_env MURMUR_TZ "Asia/Shanghai"
  for key in OPENCODE_API_KEY MURMUR_API_KEY MURMUR_BASE_URL MURMUR_MODEL MURMUR_MEMORY_MODEL; do
    write_env "$key" "$(_existing_from "$PRODUCTION_ENV_FILE" "$key" || true)"
  done
  chmod 600 "$TEST_BOT_ENV_FILE"
  note "测试凭据写入独立的 $TEST_BOT_ENV_FILE；数据库和日志位于 ./test/。"
fi

stage "Telegram"
if has_platform 1; then
  open_url "https://t.me/BotFather"
  step "在 BotFather 发送 /newbot，按提示创建机器人并复制 Token。"
  ask_secret TELEGRAM_BOT_TOKEN "粘贴 Telegram Bot Token："
  require_value "$TELEGRAM_BOT_TOKEN" "TELEGRAM_BOT_TOKEN"
  write_env TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
  ask MURMUR_ALLOWED_CHAT_IDS "允许的 Chat ID（逗号分隔；暂不知道可留空）："
  write_env MURMUR_ALLOWED_CHAT_IDS "$MURMUR_ALLOWED_CHAT_IDS"
  [[ -n "$MURMUR_ALLOWED_CHAT_IDS" ]] || warn "白名单为空时会拒绝所有消息（/start 只回显 chat id）；获取 id 后补填并重启。"
else
  say "未选择 Telegram，跳过。"
fi

stage "DingTalk"
if has_platform 2; then
  open_url "https://open-dev.dingtalk.com/"
  step "创建企业内部应用，在“凭据与基础信息”复制 Client ID 和 Client Secret。"
  ask DINGTALK_CLIENT_ID "粘贴 DingTalk Client ID："
  ask_secret DINGTALK_CLIENT_SECRET "粘贴 DingTalk Client Secret："
  require_value "$DINGTALK_CLIENT_ID" "DINGTALK_CLIENT_ID"
  require_value "$DINGTALK_CLIENT_SECRET" "DINGTALK_CLIENT_SECRET"
  write_env DINGTALK_CLIENT_ID "$DINGTALK_CLIENT_ID"
  write_env DINGTALK_CLIENT_SECRET "$DINGTALK_CLIENT_SECRET"
  ask DINGTALK_ALLOWED_USERS "允许的 userId（逗号分隔；同一人多身份用 | 连接）："
  write_env DINGTALK_ALLOWED_USERS "$DINGTALK_ALLOWED_USERS"
  ask DINGTALK_INITIATIVE_USERS "允许被主动联系的 userId（留空=默认取白名单第一个）："
  write_env DINGTALK_INITIATIVE_USERS "$DINGTALK_INITIATIVE_USERS"
  [[ -n "$DINGTALK_ALLOWED_USERS" ]] || warn "白名单为空时会拒绝所有消息；从忽略日志取 userId 后补填并重启。"
else
  say "未选择 DingTalk，跳过。"
fi

stage "Personal WeChat (Tencent ClawBot)"
if has_platform 3; then
  open_url "https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin-cli"
  say "微信授权凭据保存在 ~/.openclaw，不会写入 .env。"
  if ! command -v openclaw >/dev/null 2>&1; then
    warn "尚未检测到 openclaw CLI。请先按 OpenClaw 官方安装说明安装它，再重新运行本阶段。"
    SKIPPED+=("微信 OpenClaw 安装与扫码授权")
  elif ! command -v node >/dev/null 2>&1 || ! command -v npx >/dev/null 2>&1; then
    warn "尚未检测到 Node.js / npx。请安装 Node.js 22 或更高版本后重新运行本阶段。"
    SKIPPED+=("微信 Node.js / npx 环境")
  else
    NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
    if (( NODE_MAJOR < 22 )); then
      warn "当前 Node.js 为 $NODE_MAJOR；微信插件要求 Node.js 22 或更高版本。"
      SKIPPED+=("微信 Node.js 22+ 环境")
    else
      step "运行：npx -y @tencent-weixin/openclaw-weixin-cli install"
      step "运行：openclaw channels login --channel openclaw-weixin，然后用手机扫描终端二维码。"
      step "授权完成后运行：openclaw config set plugins.entries.openclaw-weixin.enabled false"
      step "再运行：openclaw gateway restart（避免 OpenClaw 和 Murmur 同时取消息）。"
      pause "完成扫码和关闭插件后按 Enter 继续"
    fi
  fi
  ask WECHAT_ALLOWED_USERS "允许的微信 openId（暂不知道可留空）："
  write_env WECHAT_ALLOWED_USERS "$WECHAT_ALLOWED_USERS"
  write_env WECHAT_INITIATIVE "0"
  [[ -n "$WECHAT_ALLOWED_USERS" ]] || warn "白名单为空时会拒绝所有消息；从忽略日志取 openId 后补填并重启。"
else
  say "未选择微信，跳过。"
fi

stage "QQ bot"
if has_platform 4; then
  open_url "https://q.qq.com/"
  step "创建官方 QQ 机器人，复制 AppID 和 AppSecret。"
  ask QQ_APP_ID "粘贴 QQ AppID："
  ask_secret QQ_CLIENT_SECRET "粘贴 QQ AppSecret："
  require_value "$QQ_APP_ID" "QQ_APP_ID"
  require_value "$QQ_CLIENT_SECRET" "QQ_CLIENT_SECRET"
  write_env QQ_APP_ID "$QQ_APP_ID"
  write_env QQ_CLIENT_SECRET "$QQ_CLIENT_SECRET"
  ask QQ_ALLOWED_USERS "允许的 QQ openId（暂不知道可留空）："
  write_env QQ_ALLOWED_USERS "$QQ_ALLOWED_USERS"
  ask QQ_SANDBOX "QQ 机器人尚未过审？输入 1 开启沙箱，否则留空："
  write_env QQ_SANDBOX "$QQ_SANDBOX"
  [[ -n "$QQ_ALLOWED_USERS" ]] || warn "白名单为空时会拒绝所有消息；从忽略日志取 openId 后补填并重启。"
else
  say "未选择 QQ，跳过。"
fi

# The remaining settings belong to the official App/dashboard environment.
ENV_FILE="$PRODUCTION_ENV_FILE"

stage "Official App API, dashboard and local data"
ask MURMUR_TZ "时区（默认 Asia/Shanghai）："
write_env MURMUR_TZ "${MURMUR_TZ:-Asia/Shanghai}"
write_env MURMUR_APP_TIMEZONE "${MURMUR_TZ:-Asia/Shanghai}"

say "App Attest 正式模式需要 Apple Developer 账号；没有账号时只能显式使用隔离开发模式。"
ask APP_AUTH_CHOICE "输入 1 配置 production，2 配置 simulator development："
case "${APP_AUTH_CHOICE:-1}" in
  1)
    ask MURMUR_APP_BASE_URL "App API 的 HTTPS 正式地址（例如 https://app.example.com）："
    [[ "$MURMUR_APP_BASE_URL" == https://* ]] || {
      warn "production App API 必须使用 HTTPS。"
      exit 2
    }
    ask MURMUR_APP_TEAM_ID "Apple Team ID："
    require_value "$MURMUR_APP_TEAM_ID" "MURMUR_APP_TEAM_ID"
    ask MURMUR_APP_BUNDLE_ID "Bundle ID（默认 com.sakura.Murmur）："
    MURMUR_APP_BUNDLE_ID=${MURMUR_APP_BUNDLE_ID:-com.sakura.Murmur}
    ask MURMUR_APP_ID "完整 App ID（留空则自动使用 TEAM_ID.BUNDLE_ID）："
    write_env MURMUR_APP_ATTEST_MODE "production"
    write_env MURMUR_APP_ALLOW_DEVELOPMENT "0"
    write_env MURMUR_APP_DEVELOPMENT_TOKEN ""
    write_env MURMUR_APP_BASE_URL "$MURMUR_APP_BASE_URL"
    write_env MURMUR_APP_TEAM_ID "$MURMUR_APP_TEAM_ID"
    write_env MURMUR_APP_BUNDLE_ID "$MURMUR_APP_BUNDLE_ID"
    write_env MURMUR_APP_ID "$MURMUR_APP_ID"
    write_env MURMUR_APP_APNS_ENVIRONMENT "production"
    ;;
  2)
    warn "development token 只允许模拟器/本地联调。迁移到 VPS 时 sanitize 会清掉它并强制 production。"
    ask MURMUR_APP_BASE_URL "开发 App API 地址（默认 http://127.0.0.1:8766）："
    MURMUR_APP_BASE_URL=${MURMUR_APP_BASE_URL:-http://127.0.0.1:8766}
    ask MURMUR_APP_BUNDLE_ID "Bundle ID（默认 com.sakura.Murmur）："
    MURMUR_APP_BUNDLE_ID=${MURMUR_APP_BUNDLE_ID:-com.sakura.Murmur}
    MURMUR_APP_DEVELOPMENT_TOKEN=$(_existing MURMUR_APP_DEVELOPMENT_TOKEN || true)
    if [[ ${#MURMUR_APP_DEVELOPMENT_TOKEN} -lt 24 ]]; then
      if command -v openssl >/dev/null 2>&1; then
        MURMUR_APP_DEVELOPMENT_TOKEN=$(openssl rand -hex 32)
      else
        MURMUR_APP_DEVELOPMENT_TOKEN=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(32))')
      fi
    fi
    write_env MURMUR_APP_ATTEST_MODE "development"
    write_env MURMUR_APP_ALLOW_DEVELOPMENT "1"
    write_env MURMUR_APP_DEVELOPMENT_TOKEN "$MURMUR_APP_DEVELOPMENT_TOKEN"
    write_env MURMUR_APP_BASE_URL "$MURMUR_APP_BASE_URL"
    write_env MURMUR_APP_TEAM_ID ""
    write_env MURMUR_APP_BUNDLE_ID "$MURMUR_APP_BUNDLE_ID"
    write_env MURMUR_APP_ID ""
    write_env MURMUR_APP_APNS_ENVIRONMENT "development"
    ;;
  *) warn "只支持 1 或 2。"; exit 2 ;;
esac

MURMUR_APP_DB=$(_existing MURMUR_APP_DB || true)
MURMUR_APP_MEMORY_DB=$(_existing MURMUR_APP_MEMORY_DB || true)
MURMUR_APP_DATA_ROOT=$(_existing MURMUR_APP_DATA_ROOT || true)
MURMUR_APP_TEMP_DIR=$(_existing MURMUR_APP_TEMP_DIR || true)
write_env MURMUR_APP_DB "${MURMUR_APP_DB:-./murmur.db}"
write_env MURMUR_APP_MEMORY_DB "${MURMUR_APP_MEMORY_DB:-./murmur.db}"
write_env MURMUR_APP_DATA_ROOT "${MURMUR_APP_DATA_ROOT:-.}"
write_env MURMUR_APP_TEMP_DIR "${MURMUR_APP_TEMP_DIR:-./app-uploads}"

ask MURMUR_APP_APNS_KEY_PATH "APNs .p8 密钥路径（还没有账号可留空）："
write_env MURMUR_APP_APNS_KEY_PATH "$MURMUR_APP_APNS_KEY_PATH"
ask MURMUR_APP_APNS_KEY_ID "APNs Key ID（可留空）："
write_env MURMUR_APP_APNS_KEY_ID "$MURMUR_APP_APNS_KEY_ID"
ask MURMUR_APP_APNS_TEAM_ID "APNs Team ID（留空则跟随 Apple Team ID）："
write_env MURMUR_APP_APNS_TEAM_ID "$MURMUR_APP_APNS_TEAM_ID"
write_env MURMUR_APP_APNS_TOPIC "${MURMUR_APP_BUNDLE_ID:-com.sakura.Murmur}"

ask MURMUR_LOGDIR "日志目录（留空=项目下 logs/）："
write_env MURMUR_LOGDIR "$MURMUR_LOGDIR"
CURRENT_WEB_TOKEN=$(_existing MURMUR_WEB_TOKEN || true)
GENERATE_WEB_TOKEN=0
if [[ -n "$CURRENT_WEB_TOKEN" ]]; then
  say "已存在看板访问 token。"
  if confirm "重新生成看板 token 吗？"; then
    GENERATE_WEB_TOKEN=1
  else
    MURMUR_WEB_TOKEN=$CURRENT_WEB_TOKEN
  fi
elif confirm "需要从其他机器访问看板吗？（会生成访问 token，不会开放端口）"; then
  GENERATE_WEB_TOKEN=1
else
  MURMUR_WEB_TOKEN=""
fi
if (( GENERATE_WEB_TOKEN )); then
  if command -v openssl >/dev/null 2>&1; then
    MURMUR_WEB_TOKEN=$(openssl rand -hex 32)
  else
    MURMUR_WEB_TOKEN=$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(32))')
  fi
fi
write_env MURMUR_WEB_TOKEN "$MURMUR_WEB_TOKEN"
[[ -n "$MURMUR_WEB_TOKEN" ]] && note "看板仍只绑定 127.0.0.1；远程访问请使用 SSH 隧道。"
chmod 600 "$ENV_FILE"

stage "Validation (does not start bots)"
if [[ -x .venv/bin/murmur ]]; then
  .venv/bin/murmur --help >/dev/null
  .venv/bin/python - "$ENV_FILE" <<'PY'
import sys

from dotenv import load_dotenv

# Config no longer reads an ambient .env at import time. Validate exactly the
# production file the wizard just wrote; do not inherit missing values from a
# test-bot file or another working directory.
load_dotenv(sys.argv[1], override=True)

from murmur.config import Config
from murmur.app_settings import AppSettings

cfg = Config.load()
app = AppSettings.from_env(cfg)
app.validate()
print("  ✓ model API configured" if cfg.api_key else "  ✗ missing model API key")
print(f"  ✓ App API authentication: {app.attest_mode}")
for label, ready in (
    ("Telegram", bool(cfg.telegram_token)),
    ("DingTalk", bool(cfg.dingtalk_client_id and cfg.dingtalk_client_secret)),
    ("WeChat", bool(cfg.wechat_token)),
    ("QQ", bool(cfg.qq_app_id and cfg.qq_client_secret)),
):
    print(f"  {'✓' if ready else '–'} {label}")
print("  ✓ .env permissions set to 600")
PY
else
  SKIPPED+=("Murmur CLI validation（.venv 不存在）")
fi

finish
if [[ "$ROOT_DIR" == "/opt/murmur" ]]; then
  say "Next: install and enable murmur-app-worker, murmur-app-api and murmur-web."
else
  say "Next: run the official App API/worker; old platforms stay disabled by default."
fi
say "Test bots require .env.test-bots plus explicit enablement; see deploy/README.md."
say "The dashboard should stay behind an SSH tunnel."
