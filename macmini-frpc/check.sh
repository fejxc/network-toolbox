#!/bin/sh
# FRPC 一键体检：目录 / 本体 / 配置 / LaunchAgent / launchd 状态 / 进程 / 日志尾部
#
# 用法：
#   ./check.sh                                   # 默认路径与 Label
#   FRP_DIR=/opt/frp LABEL=com.my.frpc ./check.sh  # 自定义

set -u

FRP_DIR=${FRP_DIR:-"$HOME/frp_0.59.0_darwin_arm64"}
LABEL=${LABEL:-com.sunyun.frpc}
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "=== FRP DIRECTORY ==="
ls -ld "$FRP_DIR" 2>/dev/null || echo "缺失：$FRP_DIR"

echo
echo "=== FRPC FILE ==="
ls -lh "$FRP_DIR/frpc" 2>/dev/null || echo "缺失：frpc"

echo
echo "=== FRPC CONFIG ==="
ls -lh "$FRP_DIR/frpc.toml" 2>/dev/null || echo "缺失：frpc.toml"

echo
echo "=== LAUNCHAGENT ==="
ls -lh "$PLIST" 2>/dev/null || echo "缺失：$PLIST"

echo
echo "=== PLIST CHECK ==="
plutil -lint "$PLIST" 2>/dev/null || echo "plist 语法检查失败"

echo
echo "=== LAUNCHCTL ==="
launchctl list | grep -i frp || echo "没有已加载的 frp 服务"

echo
echo "=== FRPC PROCESS ==="
ps aux | grep '[f]rpc' || echo "frpc 进程不存在"

echo
echo "=== FRPC LOG (tail 20) ==="
tail -20 "$FRP_DIR/frpc.log" 2>/dev/null

echo
echo "=== FRPC ERROR LOG (tail 20) ==="
tail -20 "$FRP_DIR/frpc.err.log" 2>/dev/null
