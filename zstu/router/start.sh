#!/bin/sh
# RM2100 启动包装：避免重复启动 watcher，并把启动失败写入系统日志。

set -u

CONFIG=${ZSTU_ROUTER_CONFIG:-/etc/storage/zstu_wifi.conf}
if [ -f "$CONFIG" ]; then
  . "$CONFIG"
fi

WATCH_SCRIPT=${ZSTU_ROUTER_WATCH_SCRIPT:-/etc/storage/zstu_wifi_router_watch.sh}
WATCH_LOG=${ZSTU_WIFI_LOG_FILE:-/tmp/zstu_wifi_watch.log}
PID_FILE=${ZSTU_WIFI_PID_FILE:-/tmp/zstu_wifi_watch.pid}
LOG_TAG=${ZSTU_WIFI_LOG_TAG:-zstu_wifi}
LOG_DIR=${WATCH_LOG%/*}
[ -n "$LOG_DIR" ] || LOG_DIR=.
mkdir -p "$LOG_DIR" 2>/dev/null || true

syslog() {
  # 该 RM2100 的 BusyBox ash 没有 command -v，但 logger applet 可直接调用。
  logger -t "$LOG_TAG" "$*" 2>/dev/null || true
}

if [ ! -x "$WATCH_SCRIPT" ]; then
  syslog "watcher 不存在或不可执行：$WATCH_SCRIPT"
  exit 2
fi

if [ -f "$PID_FILE" ]; then
  old_pid=$(sed -n '1p' "$PID_FILE")
  case "$old_pid" in
    ''|*[!0-9]*) rm -f "$PID_FILE" ;;
    *)
      if kill -0 "$old_pid" 2>/dev/null; then
        exit 0
      fi
      rm -f "$PID_FILE"
      ;;
  esac
fi

"$WATCH_SCRIPT" >>"$WATCH_LOG" 2>&1 &
watch_pid=$!
echo "$watch_pid" >"$PID_FILE"
syslog "watcher 已启动：pid=$watch_pid"
exit 0
