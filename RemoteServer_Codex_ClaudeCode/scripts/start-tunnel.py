#!/usr/bin/env python3
"""跨平台 SSH 反向隧道一键脚本（macOS / Linux / Windows 通用）。

把本地代理端口反向转发到远程服务器的回环地址，供远程 CLI 出网：

    远程 127.0.0.1:REMOTE_PORT → SSH -R → 本地 127.0.0.1:LOCAL_PORT → 本地代理软件

仅依赖 Python 3 标准库与系统 ssh 客户端（Windows 需在「可选功能」中
启用 OpenSSH 客户端）。所有平台差异都收敛在本文件内：端口探测用 TCP
连接（不依赖 lsof/ss/netstat），进程存活检测自动区分平台。

用法示例：

    python3 start-tunnel.py                  # 按 默认/环境变量 配置，后台建立隧道
    python3 start-tunnel.py --foreground     # 前台运行，Ctrl+C 停止
    python3 start-tunnel.py --watch          # 守护模式：断线自动重连（前台）
    python3 start-tunnel.py --status         # 查看后台隧道状态
    python3 start-tunnel.py --stop           # 停止后台隧道
    python3 start-tunnel.py --host 1.2.3.4 --port 22 --user me \\
        --remote-port 7890 --local-port 7897 # 适配其它服务器 / 端口

环境变量（均可被命令行参数覆盖）：

    TUNNEL_SSH_HOST / TUNNEL_SSH_PORT / TUNNEL_SSH_USER
    TUNNEL_REMOTE_PORT / TUNNEL_LOCAL_PORT / TUNNEL_PIDFILE / TUNNEL_LOGFILE
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 默认值与 RemoteServer_Codex_ClaudeCode 现网环境一致，均可用环境变量或参数覆盖
DEFAULT_SSH_HOST = "10.11.154.192"
DEFAULT_SSH_PORT = 20064
DEFAULT_SSH_USER = "sunyun"
DEFAULT_REMOTE_PORT = 7890
DEFAULT_LOCAL_PORT = 7897
KEEPALIVE_INTERVAL = 30
KEEPALIVE_COUNT_MAX = 3
LOCAL_CHECK_TIMEOUT = 2.0
WATCH_RETRY_DELAY = 5.0
STARTUP_SETTLE_SECONDS = 0.8

DEFAULT_PIDFILE = Path.home() / ".ssh-reverse-tunnel.pid"
DEFAULT_LOGFILE = Path.home() / ".ssh-reverse-tunnel.log"

IS_WINDOWS = platform.system() == "Windows"


def env_int(name: str, default: int) -> int:
    """读整型环境变量；缺失或非法时用默认值。"""
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class TunnelConfig:
    """隧道的不可变配置。"""

    ssh_host: str
    ssh_port: int
    ssh_user: str
    remote_port: int
    local_port: int

    @property
    def remote_forward(self) -> str:
        """-R 参数值：远程回环地址 → 本地回环地址。"""
        return f"127.0.0.1:{self.remote_port}:127.0.0.1:{self.local_port}"

    def ssh_command(self) -> list[str]:
        """构造 ssh 命令；保活与转发失败即退出的语义与文档一致。"""
        return [
            "ssh",
            "-p", str(self.ssh_port),
            "-N",
            "-o", f"ServerAliveInterval={KEEPALIVE_INTERVAL}",
            "-o", f"ServerAliveCountMax={KEEPALIVE_COUNT_MAX}",
            "-o", "ExitOnForwardFailure=yes",
            "-R", self.remote_forward,
            f"{self.ssh_user}@{self.ssh_host}",
        ]


def ensure_ssh_available() -> None:
    """系统缺少 ssh 客户端时给出平台对应的解决提示。"""
    if shutil.which("ssh"):
        return
    hint = "请在「设置 → 应用 → 可选功能」中安装 OpenSSH 客户端。" if IS_WINDOWS else "请安装 openssh-client。"
    print(f"错误：未找到 ssh 客户端。{hint}", file=sys.stderr)
    raise SystemExit(1)


def check_local_proxy(port: int) -> str | None:
    """用 TCP 连接探测本地代理端口；返回错误信息或 None（正常）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=LOCAL_CHECK_TIMEOUT):
            return None
    except OSError as exc:
        return f"错误：本地代理 127.0.0.1:{port} 未监听（{exc}）。请先启动本地代理软件。"


def pid_alive(pid: int) -> bool:
    """探测进程是否存活；Windows 用 tasklist，POSIX 用 kill 0。"""
    if IS_WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(pidfile: Path) -> int | None:
    """读取 pid 文件；缺失或内容非法返回 None。"""
    try:
        return int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def detach_kwargs() -> dict:
    """后台分离启动参数：Windows 与 POSIX 各自的正确姿势。"""
    if IS_WINDOWS:
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def cmd_start_detached(cfg: TunnelConfig, pidfile: Path, logfile: Path) -> int:
    """后台建立隧道：分离进程 + pid 文件 + stderr 日志，随后确认存活。"""
    with logfile.open("ab") as log:
        process = subprocess.Popen(
            cfg.ssh_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            **detach_kwargs(),
        )
    pidfile.write_text(f"{process.pid}\n", encoding="utf-8")

    # 给 ssh 一点时间失败（端口占用 / 认证失败会很快退出）
    time.sleep(STARTUP_SETTLE_SECONDS)
    if not pid_alive(process.pid):
        pidfile.unlink(missing_ok=True)
        print("错误：隧道建立失败。常见原因：", file=sys.stderr)
        print("  1. 远程端口已被占用（隧道可能已在服务器上运行）；", file=sys.stderr)
        print("  2. SSH 认证失败或网络不可达。", file=sys.stderr)
        print(f"详情查看日志：{logfile}", file=sys.stderr)
        return 1

    print("隧道已建立（后台运行）：")
    print(f"  远程 127.0.0.1:{cfg.remote_port} → 本地 127.0.0.1:{cfg.local_port}")
    print(f"  pid={process.pid}，pid 文件：{pidfile}")
    print(f"  停止：{Path(sys.argv[0]).name} --stop")
    return 0


def cmd_foreground(cfg: TunnelConfig) -> int:
    """前台运行隧道，Ctrl+C 停止。"""
    print(f"前台运行：远程 127.0.0.1:{cfg.remote_port} → 本地 127.0.0.1:{cfg.local_port}（Ctrl+C 停止）")
    try:
        result = subprocess.run(cfg.ssh_command())
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0
    if result.returncode != 0:
        print(f"ssh 异常退出（码 {result.returncode}）；远程端口被占用说明隧道可能已在运行。", file=sys.stderr)
        return 1
    return 0


def cmd_watch(cfg: TunnelConfig) -> int:
    """守护模式：ssh 意外退出后自动重连。"""
    print(f"守护模式：ssh 退出后 {WATCH_RETRY_DELAY:g}s 内自动重连，Ctrl+C 退出。")
    while True:
        print("建立隧道 ...")
        try:
            result = subprocess.run(cfg.ssh_command())
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0
        if result.returncode == 0:
            print("ssh 正常退出。")
            return 0
        print(
            f"ssh 异常退出（码 {result.returncode}），{WATCH_RETRY_DELAY:g}s 后重连；"
            "若提示远程端口占用，请先确认服务器上旧隧道是否残留。",
            file=sys.stderr,
        )
        try:
            time.sleep(WATCH_RETRY_DELAY)
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0


def cmd_status(pidfile: Path) -> int:
    """查看后台隧道状态。"""
    pid = read_pid(pidfile)
    if pid is None:
        print("未运行（没有 pid 文件）。")
        return 1
    if pid_alive(pid):
        print(f"运行中：pid={pid}（pid 文件：{pidfile}）")
        return 0
    pidfile.unlink(missing_ok=True)
    print("pid 文件存在但进程已退出，已清理。")
    return 1


def cmd_stop(pidfile: Path) -> int:
    """停止后台隧道并清理 pid 文件。"""
    pid = read_pid(pidfile)
    if pid is None:
        print("没有正在运行的后台隧道（未找到 pid 文件）。")
        return 0
    if not pid_alive(pid):
        pidfile.unlink(missing_ok=True)
        print(f"pid={pid} 的进程已不存在，已清理 pid 文件。")
        return 0

    os.kill(pid, signal.SIGTERM)  # Windows 上等价于终止进程
    if not IS_WINDOWS:
        for _ in range(20):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
    pidfile.unlink(missing_ok=True)
    print(f"已停止后台隧道（pid={pid}）。")
    return 0


def parse_args() -> argparse.Namespace:
    """解析参数；默认值来自常量，环境变量可覆盖，命令行参数优先级最高。"""
    parser = argparse.ArgumentParser(
        description="跨平台 SSH 反向隧道：远程 127.0.0.1:REMOTE_PORT → 本地 127.0.0.1:LOCAL_PORT",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("TUNNEL_SSH_HOST", DEFAULT_SSH_HOST), help="远程服务器地址")
    parser.add_argument("--port", type=int, default=env_int("TUNNEL_SSH_PORT", DEFAULT_SSH_PORT), help="SSH 端口")
    parser.add_argument("--user", default=os.environ.get("TUNNEL_SSH_USER", DEFAULT_SSH_USER), help="SSH 用户")
    parser.add_argument("--remote-port", type=int, default=env_int("TUNNEL_REMOTE_PORT", DEFAULT_REMOTE_PORT),
                        help="远程服务器上监听的端口")
    parser.add_argument("--local-port", type=int, default=env_int("TUNNEL_LOCAL_PORT", DEFAULT_LOCAL_PORT),
                        help="本地代理端口")
    parser.add_argument("--pidfile", type=Path,
                        default=Path(os.environ.get("TUNNEL_PIDFILE", DEFAULT_PIDFILE)), help="pid 文件路径")
    parser.add_argument("--logfile", type=Path,
                        default=Path(os.environ.get("TUNNEL_LOGFILE", DEFAULT_LOGFILE)),
                        help="后台模式 ssh stderr 日志")
    parser.add_argument("--no-check-local", action="store_true", help="跳过本地代理端口预检")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--foreground", action="store_true", help="前台运行（Ctrl+C 停止）")
    mode.add_argument("--watch", action="store_true", help="守护模式：断线自动重连（前台）")
    mode.add_argument("--stop", action="store_true", help="停止后台隧道")
    mode.add_argument("--status", action="store_true", help="查看后台隧道状态")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.status:
        return cmd_status(args.pidfile)
    if args.stop:
        return cmd_stop(args.pidfile)

    cfg = TunnelConfig(
        ssh_host=args.host,
        ssh_port=args.port,
        ssh_user=args.user,
        remote_port=args.remote_port,
        local_port=args.local_port,
    )
    ensure_ssh_available()
    if not args.no_check_local:
        error = check_local_proxy(cfg.local_port)
        if error:
            print(error, file=sys.stderr)
            return 1

    if args.watch:
        return cmd_watch(cfg)
    if args.foreground:
        return cmd_foreground(cfg)
    return cmd_start_detached(cfg, args.pidfile, args.logfile)


if __name__ == "__main__":
    raise SystemExit(main())
