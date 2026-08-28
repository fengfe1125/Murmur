#!/usr/bin/env bash
# 本地跑 Murmur。挂了自动拉起——看门狗判定卡死时会 exit 75，靠这里重启。
# 服务器上不用这个，systemd 的 Restart=always 管这件事。
#
#   cp infra/deploy/test-bots.env.example .env.test-bots
#   ./scripts/ops/run-test-bots.sh bot        只跑 Telegram 测试通道
#   ./scripts/ops/run-test-bots.sh dingtalk   只跑钉钉测试通道
#   ./scripts/ops/run-test-bots.sh wechat     只跑微信测试通道
#   ./scripts/ops/run-test-bots.sh qq         只跑 QQ 测试通道
#   ./scripts/ops/run-test-bots.sh all        四个都跑（默认；没配的会自己退出，不影响别的）
set -uo pipefail
cd "$(dirname "$0")/../.."

BIN=".venv/bin/murmur"

# 旧平台不再读生产 .env。测试凭据和测试 DB 必须放在独立文件中，
# 否则一次性退出；门禁失败不是可恢复崩溃，绝不进 supervise 循环。
[ -f .env.test-bots ] || {
  echo "旧平台只能在隔离测试环境启动：先复制 infra/deploy/test-bots.env.example 为 .env.test-bots" >&2
  exit 2
}
set -a
. ./.env.test-bots
set +a

[ "${MURMUR_CHANNEL_MODE:-}" = "transition" ] || {
  echo "拒绝启动：MURMUR_CHANNEL_MODE 必须是 transition" >&2
  exit 2
}
case "${MURMUR_ENABLE_TEST_BOTS:-}" in
  1|true|TRUE|yes|YES|on|ON) ;;
  *)
    echo "拒绝启动：隔离测试文件必须显式设置 MURMUR_ENABLE_TEST_BOTS=1" >&2
    exit 2
    ;;
esac
# 日志默认放项目里的 logs/，不放在 /tmp——macOS 会定期清 /tmp，
# 看板"24h 内重启次数"这类历史会跟着消失。.env.test-bots 里 MURMUR_LOGDIR 可覆盖。
# 注意必须在 source .env.test-bots 之后再算，否则隔离测试配置不生效，
# 和看板（load_dotenv）读到的值就对不上了。
LOGDIR="${MURMUR_LOGDIR:-./logs}"
mkdir -p "$LOGDIR"

supervise() {
  local what="$1" log="$LOGDIR/murmur_$1.log"
  local delay=5
  while true; do
    echo "=== $(date '+%F %T') 启动 $what ===" >> "$log"
    local started=$SECONDS
    "$BIN" "$what" >> "$log" 2>&1
    local code=$? ran=$(( SECONDS - started ))
    # 跑够 2 分钟就算"起来过"，把退避清零。
    # 不重置的话反复重启几次就永远是 60 秒起步，哪怕之后一切正常。
    [ $ran -ge 120 ] && delay=5
    # 不靠退出码判断该不该重启：PTB 收到 SIGTERM 会**优雅退出并返回 0**，
    # 跟"我不想跑了"长得一模一样。曾经因此 pkill 之后 Telegram 再也没起来。
    # 想真正停下就杀 run-test-bots.sh 本身（下面 all 分支里打印了命令）。
    echo "=== $(date '+%F %T') $what 退出码 $code，${delay}s 后重启 ===" >> "$log"
    sleep "$delay"
    # 退避到 60 秒封顶：网络长时间不通时别把日志刷爆
    delay=$(( delay * 2 )); [ $delay -gt 60 ] && delay=60
  done
}

# 微信要先扫码才有 token。没登录就别拉起来——supervise 是无条件重启的，
# 一个必然失败的进程会一直刷日志。
wechat_ready() {
  [ -n "${WECHAT_TOKEN:-}" ] && return 0
  # 扫码后的凭据在 {state}/openclaw-weixin/accounts/，
  # credentials/ 底下只有老版本的单文件 token。
  local s="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
  compgen -G "$s/openclaw-weixin/accounts/*.json" > /dev/null 2>&1 \
    || [ -f "$s/credentials/openclaw-weixin/credentials.json" ]
}

case "${1:-all}" in
  bot)      supervise bot ;;
  dingtalk) supervise dingtalk ;;
  wechat)
    wechat_ready || { echo "微信还没登录，先跑：openclaw channels login --channel openclaw-weixin"; exit 2; }
    supervise wechat ;;
  qq)
    [ -n "${QQ_APP_ID:-}" ] || { echo "QQ 还没配，先在 q.qq.com 建机器人，把 QQ_APP_ID / QQ_CLIENT_SECRET 填进 .env.test-bots"; exit 2; }
    supervise qq ;;
  all)
    supervise bot &
    supervise dingtalk &
    running="Telegram + 钉钉"
    if wechat_ready; then
      supervise wechat &
      running="$running + 微信"
    else
      echo "（微信未登录，跳过。要接：见 infra/deploy/test-bots.env.example 的 WECHAT_ 配置）"
    fi
    if [ -n "${QQ_APP_ID:-}" ]; then
      supervise qq &
      running="$running + QQ"
    else
      echo "（QQ 未配置，跳过。要接：见 infra/deploy/test-bots.env.example 的 QQ_ 配置）"
    fi
    echo "$running 在跑。日志：$LOGDIR/murmur_*.log"
    echo "停止：pkill -f 'run-test-bots.sh|murmur bot|murmur dingtalk|murmur wechat|murmur qq'"
    wait
    ;;
  *) echo "用法: $0 [bot|dingtalk|wechat|qq|all]"; exit 2 ;;
esac
