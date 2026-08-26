#!/bin/sh
# 路由器常驻监测：定期运行一次认证脚本，掉线后等待网络恢复并重试。

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG=${ZSTU_ROUTER_CONFIG:-/etc/storage/zstu_wifi.conf}
if [ -f "$CONFIG" ]; then
  . "$CONFIG"
fi
if [ -n "${ZSTU_ROUTER_LOGIN_SCRIPT:-}" ]; then
  LOGIN_SCRIPT=$ZSTU_ROUTER_LOGIN_SCRIPT
elif [ -x "$SCRIPT_DIR/login.sh" ]; then
  # 本地仓库目录中的默认名称。
  LOGIN_SCRIPT=$SCRIPT_DIR/login.sh
else
  # 路由器部署时 login.sh 会被重命名为这个持久化文件名。
  LOGIN_SCRIPT=$SCRIPT_DIR/zstu_wifi_login_router.sh
fi
INTERVAL=${ZSTU_WIFI_CHECK_INTERVAL:-60}
LOG_FILE=${ZSTU_WIFI_LOG_FILE:-/tmp/zstu_wifi_watch.log}
LOG_TAG=${ZSTU_WIFI_LOG_TAG:-zstu_wifi}

log() {
  message=$*
  timestamp=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo 'unknown-time')
  printf '[%s] %s\n' "$timestamp" "$message" >>"$LOG_FILE" 2>/dev/null || true
  # 该 RM2100 的 BusyBox ash 没有 command -v，但 logger applet 可直接调用。
  logger -t "$LOG_TAG" "$message" 2>/dev/null || true
}

log_output() {
  printf '%s\n' "$1" | while IFS= read -r line; do
    [ -n "$line" ] || continue
    log "$line"
  done
}

LOG_DIR=${LOG_FILE%/*}
[ -n "$LOG_DIR" ] || LOG_DIR=.
mkdir -p "$LOG_DIR" 2>/dev/null || true

if [ ! -x "$LOGIN_SCRIPT" ]; then
  log "错误：找不到可执行认证脚本：$LOGIN_SCRIPT"
  exit 2
fi

case "$INTERVAL" in
  ''|*[!0-9]*)
    log "错误：ZSTU_WIFI_CHECK_INTERVAL 必须是秒数"
    exit 2
    ;;
esac

log "watcher 启动：login=$LOGIN_SCRIPT，检查间隔=${INTERVAL}秒，日志=$LOG_FILE"

last_result=
while :; do
  output=$("$LOGIN_SCRIPT" "$@" 2>&1)
  result=$?

  [ -n "$output" ] && log_output "$output"

  if [ "$result" -eq 0 ]; then
    if [ "$last_result" != "0" ]; then
      log "认证监测状态：正常。"
    fi
  else
    log "认证检查失败（退出码 $result），${INTERVAL} 秒后重试。"
  fi
  last_result=$result
  sleep "$INTERVAL"
done
