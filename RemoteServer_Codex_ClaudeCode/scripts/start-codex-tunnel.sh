#!/usr/bin/env bash
# Mac 一键建立 SSH 反向隧道：服务器 127.0.0.1:7890 → Mac 127.0.0.1:7897
#
# 安装：
#   mkdir -p ~/bin
#   cp start-codex-tunnel.sh ~/bin/start-codex-tunnel
#   chmod 755 ~/bin/start-codex-tunnel
#
# 前提：本地代理软件已打开（7897 在监听）。
# 停止：pkill -f '127.0.0.1:7890:127.0.0.1:7897'

set -euo pipefail

REMOTE_HOST="sunyun@10.11.154.192"
SSH_PORT="20064"

REMOTE_PROXY="127.0.0.1:7890"
LOCAL_PROXY="127.0.0.1:7897"

PATTERN="${REMOTE_PROXY}:${LOCAL_PROXY}"

# 幂等：隧道已存在则直接退出
if pgrep -f "$PATTERN" >/dev/null; then
    echo "SSH reverse tunnel already running."
    exit 0
fi

# 防线：本地代理没开就不建隧道
if ! lsof -nP -iTCP:7897 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR: Mac proxy 127.0.0.1:7897 is not listening." >&2
    exit 1
fi

ssh -f -p "$SSH_PORT" -N \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -R "${REMOTE_PROXY}:${LOCAL_PROXY}" \
  "$REMOTE_HOST"

echo "Tunnel started:"
echo "server ${REMOTE_PROXY} -> Mac ${LOCAL_PROXY}"
