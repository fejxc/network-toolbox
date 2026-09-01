#!/usr/bin/env bash
# 容器内 Codex 一键启动：设代理变量 → 检查认证与代理连通 → 进入项目启动
#
# 安装：
#   cp start-codex-full.sh /usr/local/bin/start-codex-full
#   chmod 755 /usr/local/bin/start-codex-full
#
# 用法：
#   start-codex-full                                  # 默认项目目录
#   start-codex-full /remote-home/cgrr_train/KELLER_repro
#
# 前提：Mac 已运行本地代理，且 start-tunnel.py 已建好隧道（python3 scripts/start-tunnel.py）。

set -euo pipefail

export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890

export NO_PROXY=localhost,127.0.0.1,::1
export no_proxy=localhost,127.0.0.1,::1

unset ALL_PROXY all_proxy

PROJECT_DIR="${1:-/remote-home/cgrr_train/cgrr}"
AUTH_FILE="${CODEX_HOME:-$HOME/.codex}/auth.json"

if [[ ! -f "$AUTH_FILE" ]]; then
    echo "ERROR: missing Codex auth file: $AUTH_FILE" >&2
    exit 1
fi

if ! curl --silent --show-error --fail \
    --proxy http://127.0.0.1:7890 \
    --max-time 15 \
    https://api.ipify.org >/dev/null; then

    echo "ERROR: proxy unavailable." >&2
    echo "Start the Mac SSH reverse tunnel first." >&2
    exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: project directory not found: $PROJECT_DIR" >&2
    exit 1
fi

cd "$PROJECT_DIR"

echo "Codex: $(codex --version)"
echo "Project: $PWD"
echo "Auth: $AUTH_FILE"

# 无沙箱 + 无人工审批，仅适合：可信代码仓库 + 已有外层 Linux/Docker 隔离。
exec codex --dangerously-bypass-approvals-and-sandbox
