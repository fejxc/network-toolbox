#!/usr/bin/env python3
"""GPU 平台监控：查看每张卡的利用率 / 显存 / 温度与使用人。

适配学校 GPU 管理平台（Portainer 变体），数据来自两个只读接口：

1. ``/api/endpoints/{id}``  → Snapshots 里每张卡的利用率、温度、显存百分比；
2. ``/api/gpustatReal/{id}`` → 每个进程一条记录（卡号、使用人、实例、显存、命令行）。

认证使用平台 JWT：Authorization: Bearer + jwtKey Cookie 双通道。
Token 只从本地文件或环境变量读取，不写入任何仓库文件。

用法：

    python3 gpu_monitor.py                    # 打印一次当前状态
    python3 gpu_monitor.py --watch            # 持续刷新（默认 60s）
    python3 gpu_monitor.py --watch --alert-free 2 --notify dingtalk
                                              # 空闲卡数达到阈值时推送钉钉（状态变化时只推一次）
    python3 gpu_monitor.py --debug            # 额外打印原始 JSON

环境变量：GPU_PLATFORM_URL / GPU_PLATFORM_ENDPOINT_ID / GPU_PLATFORM_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 同目录的监控共用通知组件（dingtalk.py）
from dingtalk import DingTalkNotifier

DEFAULT_BASE_URL = "http://10.11.154.209:16888"
DEFAULT_ENDPOINT_ID = 116
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "gpu-monitor" / "token"
DEFAULT_INTERVAL = 60
REQUEST_TIMEOUT = 10.0
SYSTEM_PROCESS_PID = 2808  # 平台上每张卡都挂着的 Xorg 等系统进程，不算「有人用」
FREE_ALERT_HYSTERESIS = 0  # 仅做状态沿触发，避免重复轰炸


@dataclass(frozen=True)
class GpuSnapshot:
    """来自 endpoints 快照的单卡聚合数据。"""

    gpu_id: int
    name: str
    util: float
    temperature: float
    mem_percent: float
    snapshot_time: int


@dataclass(frozen=True)
class GpuProcess:
    """来自 gpustatReal 的单进程占用记录。"""

    gpu_id: int
    pid: int
    user: str
    instance: str
    mem_mb: float
    mem_total_mb: float
    util: float
    command: str

    @property
    def is_system(self) -> bool:
        """系统进程（如 Xorg）：无使用人，或属于平台常驻进程。"""
        return not self.user or self.pid == SYSTEM_PROCESS_PID


def read_token(token_file: Path) -> str:
    """按「Token 文件 → 环境变量 GPU_PLATFORM_TOKEN」读取，都没有则报错。"""
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = os.environ.get("GPU_PLATFORM_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError(f"未找到平台 Token。请写入 {token_file} 或设置环境变量 GPU_PLATFORM_TOKEN。")


def fetch_json(url: str, token: str, timeout: float) -> object:
    """带平台认证请求一个只读接口，返回解析后的 JSON。"""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {token}",
            "Cookie": f"jwtKey={token}",
            "Referer": url,
            "User-Agent": "Mozilla/5.0 gpu-monitor/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError("平台返回 401/403，Token 可能已过期，请重新导出。") from exc
        raise RuntimeError(f"平台返回 HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法访问 GPU 平台：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"平台返回了无法解析的 JSON：{url}") from exc


def _to_float(value: object, default: float = 0.0) -> float:
    """平台数值大多以字符串下发，统一转 float。"""
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def parse_snapshots(payload: object) -> list[GpuSnapshot]:
    """从 endpoints 响应中提取每卡聚合数据（取最新一次快照）。"""
    if not isinstance(payload, dict):
        return []
    snapshots = payload.get("Snapshots") or []
    timed = [s for s in snapshots if isinstance(s, dict) and s.get("Gpus")]
    if not timed:
        return []
    latest = max(timed, key=lambda s: _to_float(s.get("Time")))
    taken_at = int(_to_float(latest.get("Time")))
    result = []
    for gpu in latest["Gpus"]:
        if not isinstance(gpu, dict):
            continue
        result.append(
            GpuSnapshot(
                gpu_id=int(_to_float(gpu.get("gpu_id"), -1)),
                name=str(gpu.get("name", "")),
                util=_to_float(gpu.get("proc")),
                temperature=_to_float(gpu.get("temperature")),
                mem_percent=_to_float(gpu.get("mem")),
                snapshot_time=taken_at,
            )
        )
    return sorted(result, key=lambda s: s.gpu_id)


def parse_processes(payload: object) -> list[GpuProcess]:
    """从 gpustatReal 响应中提取进程级占用；使用人显示为「昵称(用户名)」。"""
    if not isinstance(payload, list):
        return []
    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        username = str(item.get("Uname", "")).strip()
        nickname = str(item.get("Nickname", "")).strip()
        if nickname and username and nickname != username:
            user = f"{nickname}({username})"
        else:
            user = nickname or username
        result.append(
            GpuProcess(
                gpu_id=int(_to_float(item.get("GID"), -1)),
                pid=int(_to_float(item.get("PID"), -1)),
                user=user,
                instance=str(item.get("INS", "")).strip(),
                mem_mb=_to_float(item.get("GMem")),
                mem_total_mb=_to_float(item.get("GMemTotal")),
                util=_to_float(item.get("GPUsage")),
                command=str(item.get("Pname", "")).strip(),
            )
        )
    return result


def render_report(
    endpoint_name: str,
    endpoint_id: int,
    snapshots: list[GpuSnapshot],
    processes: list[GpuProcess],
) -> tuple[str, list[int]]:
    """渲染文本报表，返回 (报表文本, 空闲卡号列表)。"""
    gpu_ids = sorted({s.gpu_id for s in snapshots} | {p.gpu_id for p in processes})
    by_gpu: dict[int, list[GpuProcess]] = {}
    for process in processes:
        if process.is_system:
            continue
        by_gpu.setdefault(process.gpu_id, []).append(process)

    snapshot_map = {s.gpu_id: s for s in snapshots}
    lines: list[str] = []
    free_ids: list[int] = []
    header = f"{'卡':>2}  {'利用率':>6}  {'显存':>12}  {'温度':>5}  使用者 / 进程"
    lines.append(f"{endpoint_name}（endpoint {endpoint_id}）@ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(header)
    lines.append("-" * len(header))

    for gpu_id in gpu_ids:
        gpu_processes = by_gpu.get(gpu_id, [])
        snapshot = snapshot_map.get(gpu_id)
        used_mb = sum(p.mem_mb for p in gpu_processes)
        # GMemTotal 是卡的总显存，任意一条该卡记录（含系统进程）都带同样的值
        total_mb = max((p.mem_total_mb for p in processes if p.gpu_id == gpu_id), default=0.0)

        util = f"{snapshot.util:.0f}%" if snapshot else "—"
        temperature = f"{snapshot.temperature:.0f}°C" if snapshot else "—"
        if total_mb > 0:
            memory = f"{used_mb / 1024:.1f}/{total_mb / 1024:.1f}G"
        elif used_mb > 0:
            memory = f"{used_mb / 1024:.1f}G"
        else:
            memory = "—"

        if not gpu_processes:
            free_ids.append(gpu_id)
            who = "空闲"
        else:
            users = sorted({p.user for p in gpu_processes})
            who = "、".join(f"{u}×{sum(1 for p in gpu_processes if p.user == u)}" for u in users)
        lines.append(f"{gpu_id:>2}  {util:>6}  {memory:>12}  {temperature:>5}  {who}")

    if not snapshots:
        lines.append("（提示：endpoints 快照缺失，利用率为平台尚未采集或字段变化）")
    return "\n".join(lines), free_ids


def render_processes(processes: list[GpuProcess]) -> str:
    """渲染进程明细（谁在用、跑的什么）。"""
    user_processes = [p for p in processes if not p.is_system]
    if not user_processes:
        return "进程明细：当前没有用户进程占用 GPU。"
    lines = ["进程明细："]
    for process in sorted(user_processes, key=lambda p: (p.gpu_id, p.user)):
        command = process.command if len(process.command) <= 70 else process.command[:69] + "…"
        lines.append(
            f"  卡{process.gpu_id} | {process.user} | {process.mem_mb / 1024:.1f}G | "
            f"{process.instance or '无实例名'} | {command}"
        )
    return "\n".join(lines)


def check_once(args: argparse.Namespace, token: str) -> list[int]:
    """抓取一次并打印报表；返回空闲卡号列表（供 --watch 告警对比）。"""
    base = args.url.rstrip("/")
    endpoint_payload = fetch_json(f"{base}/api/endpoints/{args.endpoint_id}", token, args.timeout)
    process_payload = fetch_json(f"{base}/api/gpustatReal/{args.endpoint_id}", token, args.timeout)
    snapshots = parse_snapshots(endpoint_payload)
    processes = parse_processes(process_payload)

    if args.debug:
        print("[debug] endpoints:", json.dumps(endpoint_payload, ensure_ascii=False)[:2000])
        print("[debug] gpustatReal:", json.dumps(process_payload, ensure_ascii=False)[:2000])

    endpoint_name = ""
    if isinstance(endpoint_payload, dict):
        endpoint_name = str(endpoint_payload.get("Name", ""))
    if not snapshots and not processes:
        raise RuntimeError("两个接口都没有返回数据，可能页面结构变化或 Token 失效。")

    report, free_ids = render_report(endpoint_name, args.endpoint_id, snapshots, processes)
    print(report)
    print(render_processes(processes))
    return free_ids


def run_watch(args: argparse.Namespace, token: str) -> int:
    """持续刷新；开启 --alert-free 时在空闲卡数跨越阈值时推送钉钉。"""
    notifier = None
    if args.notify == "dingtalk":
        notifier = DingTalkNotifier.from_env(args.dingtalk_prefix)
    previous_free: set[int] | None = None
    while True:
        failure = False
        try:
            free_ids = check_once(args, token)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 检查失败：{exc}", file=sys.stderr)
            failure = True

        if not failure:
            if notifier is not None and args.alert_free > 0 and previous_free is not None:
                if len(free_ids) >= args.alert_free > len(previous_free):
                    message = f"当前空闲 {len(free_ids)} 张卡：{('、'.join(str(i) for i in free_ids)) or '无'}"
                    notifier.send("GPU 空闲提醒", message)
                    print(f"[提醒] 已推送钉钉：{message}")
            previous_free = set(free_ids)

        if not args.watch:
            return 1 if failure else 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GPU 平台监控：每卡利用率 / 显存 / 温度与使用人",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=os.environ.get("GPU_PLATFORM_URL", DEFAULT_BASE_URL), help="平台地址")
    parser.add_argument("--endpoint-id", type=int,
                        default=int(os.environ.get("GPU_PLATFORM_ENDPOINT_ID", DEFAULT_ENDPOINT_ID)),
                        help="服务器 endpoint id")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE, help="Token 文件路径")
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT, help="单次请求超时秒数")
    parser.add_argument("--watch", action="store_true", help="持续刷新")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="刷新间隔秒数")
    parser.add_argument("--alert-free", type=int, default=0, help="空闲卡数达到该值时推送钉钉（0 关闭，配合 --watch）")
    parser.add_argument("--notify", choices=("none", "dingtalk"), default="none", help="提醒方式")
    parser.add_argument("--dingtalk-prefix", default="GPU", help="钉钉环境变量前缀（<前缀>_DINGTALK_WEBHOOK）")
    parser.add_argument("--debug", action="store_true", help="打印原始 JSON 前 2000 字符")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.watch and args.interval <= 0:
        print("错误：--interval 必须大于 0。", file=sys.stderr)
        return 2
    try:
        token = read_token(args.token_file)
    except (OSError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    try:
        return run_watch(args, token)
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
