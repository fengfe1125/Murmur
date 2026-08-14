#!/usr/bin/env bash
# 本地跑 Murmur。挂了自动拉起——看门狗判定卡死时会 exit 75，靠这里重启。
# 服务器上不用这个，systemd 的 Restart=always 管这件事。
#
#   ./run.sh bot        只跑 Telegram
#   ./run.sh dingtalk   只跑钉钉
#   ./run.sh wechat     只跑微信（要先扫码登录，见 .env.example）
#   ./run.sh qq         只跑 QQ（要先在 q.qq.com 建机器人）
#   ./run.sh all        四个都跑（默认；没配的会自己退出，不影响别的）
set -uo pipefail
cd "$(dirname "$0")"

BIN=".venv/bin/murmur"
set -a
[ -f .env ] && . ./.env
set +a
# 日志默认放项目里的 logs/，不放在 /tmp——macOS 会定期清 /tmp，
# 看板"24h 内重启次数"这类历史会跟着消失。.env 里 MURMUR_LOGDIR 可覆盖。
# 注意必须在 source .env 之后再算，否则 .env 里的配置不生效，
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
    # 想真正停下就杀 run.sh 本身（下面 all 分支里打印了命令）。
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
    [ -n "${QQ_APP_ID:-}" ] || { echo "QQ 还没配，先在 q.qq.com 建机器人，把 QQ_APP_ID / QQ_CLIENT_SECRET 填进 .env"; exit 2; }
    supervise qq ;;
  all)
    supervise bot &
    supervise dingtalk &
    running="Telegram + 钉钉"
    if wechat_ready; then
      supervise wechat &
      running="$running + 微信"
    else
      echo "（微信未登录，跳过。要接：见 .env.example 里的 WECHAT_ 那几行）"
    fi
    if [ -n "${QQ_APP_ID:-}" ]; then
      supervise qq &
      running="$running + QQ"
    else
      echo "（QQ 未配置，跳过。要接：见 .env.example 里的 QQ_ 那几行）"
    fi
    echo "$running 在跑。日志：$LOGDIR/murmur_*.log"
    echo "停止：pkill -f 'run.sh|murmur bot|murmur dingtalk|murmur wechat|murmur qq'"
    wait
    ;;
  *) echo "用法: $0 [bot|dingtalk|wechat|qq|all]"; exit 2 ;;
esac
