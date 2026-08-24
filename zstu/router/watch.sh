#!/bin/sh
# 路由器常驻监测：定期运行一次认证脚本，掉线后等待网络恢复并重试。

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG=${ZSTU_ROUTER_CONFIG:-/etc/storage/zstu_wifi.conf}
if [ -f "$CONFIG" ]; then
  . "$CONFIG"
fi
LOGIN_SCRIPT=${ZSTU_ROUTER_LOGIN_SCRIPT:-$SCRIPT_DIR/login.sh}
INTERVAL=${ZSTU_WIFI_CHECK_INTERVAL:-60}

[ -x "$LOGIN_SCRIPT" ] || {
  echo "错误：找不到可执行认证脚本：$LOGIN_SCRIPT" >&2
  exit 2
}

case "$INTERVAL" in
  ''|*[!0-9]*)
    echo "错误：ZSTU_WIFI_CHECK_INTERVAL 必须是秒数" >&2
    exit 2
    ;;
esac

while :; do
  "$LOGIN_SCRIPT" "$@"
  result=$?
  if [ "$result" -ne 0 ]; then
    echo "认证检查未完成（退出码 $result），${INTERVAL} 秒后重试。" >&2
  fi
  sleep "$INTERVAL"
done
