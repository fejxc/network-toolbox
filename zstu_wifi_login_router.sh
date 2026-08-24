#!/bin/sh
# 浙江理工大学校园网认证：BusyBox ash + curl 版
#
# 适用：路由器已有 /usr/sbin/curl，当前 ePortal pageInfo 返回
# passwordEncrypt=false 的情况。账号密码应放在路由器配置文件中，不要写进本脚本。

set -eu

CURL=${ZSTU_CURL:-/usr/sbin/curl}
CONFIG=${ZSTU_ROUTER_CONFIG:-/etc/storage/zstu_wifi.conf}
TMP_DIR=${TMPDIR:-/tmp}
TIMEOUT=${ZSTU_WIFI_TIMEOUT:-30}
DISCOVERY_URL=${ZSTU_WIFI_DISCOVERY_URL:-http://connectivitycheck.gstatic.com/generate_204}

ZSTU_WIFI_USERNAME=${ZSTU_WIFI_USERNAME:-}
ZSTU_WIFI_PASSWORD=${ZSTU_WIFI_PASSWORD:-}
ZSTU_WIFI_URL=${ZSTU_WIFI_URL:-}
ZSTU_WIFI_SERVICE=${ZSTU_WIFI_SERVICE:-}
ZSTU_WIFI_OPERATOR_USER_ID=${ZSTU_WIFI_OPERATOR_USER_ID:-}
ZSTU_WIFI_OPERATOR_PASSWORD=${ZSTU_WIFI_OPERATOR_PASSWORD:-}
ZSTU_WIFI_VALIDCODE=${ZSTU_WIFI_VALIDCODE:-}

if [ -f "$CONFIG" ]; then
  # 配置文件是用户自己创建的 shell 变量文件，示例见 README_ZSTU.md。
  . "$CONFIG"
fi

STATUS_ONLY=0
DRY_RUN=0

die() {
  echo "错误：$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
用法：zstu_wifi_login_router.sh [选项]

  --status          只检查门户，不提交认证
  --dry-run         构造请求但不提交认证
  --url URL         当前未认证时的完整 ePortal URL
  --username NAME   覆盖配置中的账号
  --password PASS   覆盖配置中的密码（可能出现在进程列表中）
  --service NAME    覆盖门户服务值
  --validcode CODE  传入验证码
  -h, --help        显示帮助
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --status) STATUS_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --url) [ "$#" -ge 2 ] || die "--url 缺少参数"; ZSTU_WIFI_URL=$2; shift 2 ;;
    --username) [ "$#" -ge 2 ] || die "--username 缺少参数"; ZSTU_WIFI_USERNAME=$2; shift 2 ;;
    --password) [ "$#" -ge 2 ] || die "--password 缺少参数"; ZSTU_WIFI_PASSWORD=$2; shift 2 ;;
    --service) [ "$#" -ge 2 ] || die "--service 缺少参数"; ZSTU_WIFI_SERVICE=$2; shift 2 ;;
    --validcode) [ "$#" -ge 2 ] || die "--validcode 缺少参数"; ZSTU_WIFI_VALIDCODE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

[ -x "$CURL" ] || die "找不到 curl：$CURL"

COOKIE_FILE="$TMP_DIR/zstu_wifi_cookie.$$"
ENTRY_HTML="$TMP_DIR/zstu_wifi_entry.$$"
PAGE_INFO="$TMP_DIR/zstu_wifi_page_info.$$"
SERVICES="$TMP_DIR/zstu_wifi_services.$$"
LOGIN_RESULT="$TMP_DIR/zstu_wifi_login.$$"
ERROR_LOG="$TMP_DIR/zstu_wifi_error.$$"
trap 'rm -f "$COOKIE_FILE" "$ENTRY_HTML" "$PAGE_INFO" "$SERVICES" "$LOGIN_RESULT" "$ERROR_LOG"' EXIT HUP INT TERM

# 仅实现 encodeURIComponent 的 ASCII/UTF-8 字节路径。账号、密码和本门户
# 查询参数通常都是 ASCII；如果配置里出现非 ASCII 字符，建议使用 Mac 版。
uri_encode() {
  local input="$1" output="" char code
  LC_ALL=C
  while [ -n "$input" ]; do
    char=${input%"${input#?}"}
    input=${input#?}
    case "$char" in
      [A-Za-z0-9._~-]|'!'|'~'|'*'|"'"|'('|')')
        output=$output$char
        ;;
      *)
        code=$(printf '%d' "'$char") || return 1
        output=$output$(printf '%%%02X' "$code")
        ;;
    esac
  done
  printf '%s' "$output"
}

json_value() {
  # 仅提取简单的 JSON 字符串字段；不用于解析 serviceJson/HTML 字段。
  key=$1
  file=$2
  sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -n 1
}

ENTRY_URL=$ZSTU_WIFI_URL
[ -n "$ENTRY_URL" ] || ENTRY_URL=$DISCOVERY_URL

META=$("$CURL" --noproxy '*' -sS -L --max-time "$TIMEOUT" \
  -c "$COOKIE_FILE" -b "$COOKIE_FILE" -o "$ENTRY_HTML" \
  -w '%{http_code}|%{url_effective}' "$ENTRY_URL" 2>"$ERROR_LOG") \
  || die "无法访问认证入口：$(sed -n '1p' "$ERROR_LOG")"

HTTP_CODE=${META%%|*}
FINAL_URL=${META#*|}

if [ "$HTTP_CODE" = "204" ]; then
  echo "连通性检测返回 HTTP 204，当前网络已经可以上网，无需重复认证。"
  exit 0
fi
[ "$HTTP_CODE" = "200" ] || die "认证入口返回 HTTP $HTTP_CODE"

case "$FINAL_URL" in
  *\?*) ;;
  *) die "认证入口没有查询参数；请使用当前未认证时浏览器跳转后的完整 URL" ;;
esac

QUERY=${FINAL_URL#*\?}
ENC_QUERY=$(uri_encode "$QUERY") || die "查询参数编码失败"
ENC2_QUERY=$(uri_encode "$ENC_QUERY") || die "查询参数二次编码失败"

API_BASE=${FINAL_URL%%/index.jsp*}
API_BASE="$API_BASE/InterFace.do"
ORIGIN=$(printf '%s' "$FINAL_URL" | sed -n 's#^\(https\{0,1\}://[^/]*\).*#\1#p')
[ -n "$ORIGIN" ] || ORIGIN=http://192.168.102.130

"$CURL" --noproxy '*' -sS --max-time "$TIMEOUT" -X POST \
  -b "$COOKIE_FILE" -c "$COOKIE_FILE" \
  -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
  -H "Origin: $ORIGIN" -H "Referer: $FINAL_URL" \
  --data-raw "queryString=$ENC_QUERY" \
  "$API_BASE?method=pageInfo" -o "$PAGE_INFO" \
  || die "pageInfo 请求失败"

PASSWORD_ENCRYPT=$(json_value passwordEncrypt "$PAGE_INFO")
[ -n "$PASSWORD_ENCRYPT" ] || die "无法从 pageInfo 读取 passwordEncrypt"

"$CURL" --noproxy '*' -sS --max-time "$TIMEOUT" -X POST \
  -b "$COOKIE_FILE" -c "$COOKIE_FILE" \
  -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
  -H "Origin: $ORIGIN" -H "Referer: $FINAL_URL" \
  "$API_BASE?method=getServices&queryString=$ENC_QUERY" -o "$SERVICES" \
  || die "getServices 请求失败"

SERVICE_REQUIRED=$(json_value isService "$SERVICES")
if [ "$SERVICE_REQUIRED" = "true" ] && [ -z "$ZSTU_WIFI_SERVICE" ]; then
  die "门户要求选择服务，请在配置文件中设置 ZSTU_WIFI_SERVICE"
fi

echo "认证入口：$ORIGIN"
echo "密码加密：$PASSWORD_ENCRYPT"
echo "服务：${ZSTU_WIFI_SERVICE:-空/默认服务}"

if [ "$STATUS_ONLY" = "1" ]; then
  echo "仅检查模式：未提交认证请求。"
  exit 0
fi

if [ "$PASSWORD_ENCRYPT" = "true" ]; then
  die "当前路由器版没有 RSA 大整数实现；请在 Mac 版认证，或使用带 Python/Node 的旁路设备"
fi

[ -n "$ZSTU_WIFI_USERNAME" ] || die "缺少 ZSTU_WIFI_USERNAME"
[ -n "$ZSTU_WIFI_PASSWORD" ] || die "缺少 ZSTU_WIFI_PASSWORD"

ENC_USER=$(uri_encode "$ZSTU_WIFI_USERNAME")
ENC_USER=$(uri_encode "$ENC_USER")
ENC_PASSWORD=$(uri_encode "$ZSTU_WIFI_PASSWORD")
ENC_PASSWORD=$(uri_encode "$ENC_PASSWORD")
ENC_SERVICE=$(uri_encode "$ZSTU_WIFI_SERVICE")
ENC_SERVICE=$(uri_encode "$ENC_SERVICE")
ENC_OPERATOR_USER=$(uri_encode "$ZSTU_WIFI_OPERATOR_USER_ID")
ENC_OPERATOR_USER=$(uri_encode "$ENC_OPERATOR_USER")
ENC_OPERATOR_PASSWORD=$(uri_encode "$ZSTU_WIFI_OPERATOR_PASSWORD")
ENC_OPERATOR_PASSWORD=$(uri_encode "$ENC_OPERATOR_PASSWORD")
ENC_ENCRYPT=$(uri_encode "$PASSWORD_ENCRYPT")
ENC_ENCRYPT=$(uri_encode "$ENC_ENCRYPT")

BODY="userId=$ENC_USER&password=$ENC_PASSWORD&service=$ENC_SERVICE&queryString=$ENC2_QUERY&operatorPwd=$ENC_OPERATOR_PASSWORD&operatorUserId=$ENC_OPERATOR_USER&validcode=$ZSTU_WIFI_VALIDCODE&passwordEncrypt=$ENC_ENCRYPT"

if [ "$DRY_RUN" = "1" ]; then
  echo "试运行：已构造登录字段，但没有提交账号密码。"
  exit 0
fi

"$CURL" --noproxy '*' -sS --max-time "$TIMEOUT" -X POST \
  -b "$COOKIE_FILE" -c "$COOKIE_FILE" \
  -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
  -H "Origin: $ORIGIN" -H "Referer: $FINAL_URL" \
  --data-raw "$BODY" \
  "$API_BASE?method=login" -o "$LOGIN_RESULT" \
  || die "login 请求失败"

if grep -Eq '"result"[[:space:]]*:[[:space:]]*"success"' "$LOGIN_RESULT"; then
  echo "认证成功。"
  exit 0
fi

MESSAGE=$(json_value message "$LOGIN_RESULT")
[ -n "$MESSAGE" ] || MESSAGE=门户未返回成功结果
echo "认证失败：$MESSAGE" >&2
exit 1
