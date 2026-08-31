#!/usr/bin/env bash
# 容器内 Claude Code 一键启动：设代理变量 → 检查配置与 GLM API 连通 → 进入项目启动
#
# 安装：
#   cp start-claude-full.sh /usr/local/bin/start-claude-full
#   chmod 755 /usr/local/bin/start-claude-full
#
# 用法：
#   start-claude-full                                  # 默认项目目录
#   start-claude-full /remote-home/cgrr_train/KELLER_repro
#
# 前提：Mac 已运行本地代理，且 ~/bin/start-codex-tunnel 已建好隧道。

set -euo pipefail

export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890

export NO_PROXY=localhost,127.0.0.1,::1
export no_proxy=localhost,127.0.0.1,::1

unset ALL_PROXY all_proxy

PROJECT_DIR="${1:-/remote-home/cgrr_train/cgrr}"
SETTINGS="$HOME/.claude/settings.json"

if [[ ! -f "$SETTINGS" ]]; then
    echo "ERROR: Claude settings not found: $SETTINGS" >&2
    exit 1
fi

if ! ss -lnt 2>/dev/null | grep -q ':7890'; then
    echo "ERROR: 127.0.0.1:7890 is not listening." >&2
    echo "Start the Mac SSH reverse tunnel first." >&2
    exit 1
fi

if ! curl -x http://127.0.0.1:7890 \
    -sS -o /dev/null \
    --connect-timeout 10 \
    --max-time 30 \
    https://open.bigmodel.cn/api/anthropic; then

    echo "ERROR: Claude API network test failed." >&2
    exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: project directory not found: $PROJECT_DIR" >&2
    exit 1
fi

cd "$PROJECT_DIR"

echo "Claude: $(claude --version)"
echo "Project: $PWD"
echo "Settings: $SETTINGS"

exec claude
