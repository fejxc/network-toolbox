#!/usr/bin/env python3
"""GPU 监控本地大屏：一个命令起一个只读看板，浏览器自动刷新，无需登录学校平台。

组成：

1. 后台线程定期用 /api/auth/validate 续期 JWT（与前端机制一致）；
2. HTTP 服务（默认 127.0.0.1:8787，仅本机可访问）：
   - ``/``         简单直观的状态大屏（自动轮询）；
   - ``/api/status`` 返回 JSON（带 TTL 缓存，不会打爆平台）。

用法：

    python3 gpu_dashboard.py                    # 起服务并自动打开浏览器
    python3 gpu_dashboard.py --port 8888 --no-open
    python3 gpu_dashboard.py --endpoint-id 117  # 看其它服务器

Token 与 gpu_monitor.py 共用同一文件（~/.config/gpu-monitor/token），
两边的 --refresh-token / cron 续期互相兼容。
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# 同目录复用 gpu_monitor.py 的解析、认证与续期逻辑
from gpu_monitor import (
    DEFAULT_BASE_URL,
    DEFAULT_ENDPOINT_ID,
    DEFAULT_TOKEN_FILE,
    REQUEST_TIMEOUT,
    auth_headers,
    parse_processes,
    parse_snapshots,
    read_token,
    refresh_jwt,
    save_token,
)

DEFAULT_PORT = 8787
DEFAULT_CACHE_TTL = 15.0
DEFAULT_RENEW_INTERVAL = 1800.0  # 30 分钟，和网页开着不关一个效果
STATUS_CACHE_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()

_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GPU 资源监控大屏</title>
<style>
*{box-sizing:border-box}
body{margin:0;padding:26px 30px;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#dbe7ff;background:#050b1c;min-height:100vh}
body::before{content:'';position:fixed;inset:0;pointer-events:none;background:radial-gradient(1100px 460px at 50% -8%,rgba(35,100,230,.28),transparent 65%),repeating-linear-gradient(0deg,rgba(90,150,255,.045) 0 1px,transparent 1px 46px),repeating-linear-gradient(90deg,rgba(90,150,255,.045) 0 1px,transparent 1px 46px)}
.wrap{position:relative;max-width:1440px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid rgba(0,212,255,.3);padding-bottom:12px;margin-bottom:18px}
h1{margin:0;font-size:24px;letter-spacing:5px;color:#eaf6ff;text-shadow:0 0 22px rgba(0,212,255,.55)}
.sub{font-size:13px;color:#7f9cc7;margin-top:4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:18px}
.tile{background:linear-gradient(160deg,rgba(16,35,80,.72),rgba(8,17,42,.88));border:1px solid rgba(0,212,255,.22);border-radius:10px;padding:13px 10px;text-align:center}
.tile b{display:block;font-size:28px;font-weight:600;color:#00d4ff;font-variant-numeric:tabular-nums;text-shadow:0 0 16px rgba(0,212,255,.45)}
.tile i{font-style:normal;font-size:12px;color:#7f9cc7;letter-spacing:2px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
@media (max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:540px){.grid{grid-template-columns:1fr}}
.gcard{background:linear-gradient(165deg,rgba(16,35,80,.68),rgba(7,14,35,.92));border:1px solid rgba(0,212,255,.18);border-radius:12px;padding:14px 16px}
.gcard.isfree{border-color:rgba(61,219,217,.3)}
.ghead{display:flex;justify-content:space-between;align-items:center;font-size:14px;color:#bfe0ff;letter-spacing:1px}
.badge{font-size:11px;padding:2px 9px;border-radius:10px;border:1px solid}
.badge.run{color:#00d4ff;border-color:rgba(0,212,255,.55)}
.badge.free{color:#3ddbd9;border-color:rgba(61,219,217,.55)}
.ring{width:88px;height:88px;border-radius:50%;margin:12px auto 10px;display:grid;place-items:center;background:conic-gradient(var(--c) calc(var(--p)*1%),rgba(120,160,255,.13) 0)}
.ring div{width:70px;height:70px;border-radius:50%;background:#0a1430;display:grid;place-items:center;text-align:center}
.ring b{font-size:17px;font-variant-numeric:tabular-nums}
.ring i{font-style:normal;font-size:10px;color:#7f9cc7;display:block;margin-top:2px}
.kv{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#7f9cc7;margin-top:9px}
.kv em{font-style:normal;color:#c9dcff;font-variant-numeric:tabular-nums}
.bar{display:inline-block;width:92px;height:8px;background:rgba(120,160,255,.14);border-radius:4px;vertical-align:middle;margin-right:8px}
.bar i{display:block;height:8px;border-radius:4px;background:linear-gradient(90deg,#00d4ff,#3d7eff)}
.users{margin-top:10px;padding-top:9px;border-top:1px dashed rgba(0,212,255,.18);font-size:13px;color:#cfe2ff;min-height:20px}
h2{font-size:15px;color:#bfe0ff;letter-spacing:2px;margin:24px 0 0}
table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{padding:9px 12px;font-size:13px;text-align:left;border-bottom:1px solid rgba(0,212,255,.12);white-space:nowrap}
thead th{color:#7fd6ff;background:rgba(0,212,255,.07);letter-spacing:1px}
td.cmd{color:#8fa8cf;white-space:normal;max-width:520px}
.hot{color:#ff5c7a}.free{color:#3ddbd9}
.empty{color:#7f9cc7;text-align:center;padding:18px}
.err{color:#ff5c7a;padding:12px 0}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div><h1>GPU 资源监控大屏</h1><div class="sub" id="sub">加载中…</div></div>
  <div class="sub">每 __INTERVAL__s 自动刷新</div>
</header>
<section class="tiles" id="tiles"></section>
<section class="grid" id="grid"></section>
<h2>进程明细</h2>
<table>
<thead><tr><th>卡</th><th>使用者</th><th>显存</th><th>实例</th><th>命令</th></tr></thead>
<tbody id="proc-rows"></tbody>
</table>
</div>
<script>
const INTERVAL = __INTERVAL__;
const esc = s => {const d=document.createElement('div');d.textContent=s??'';return d.innerHTML};
const utilColor = u => u<=50 ? '#00d4ff' : u<=85 ? '#ffb84d' : '#ff5c7a';
const bars = p => `<span class="bar"><i style="width:${Math.min(100,p)}%"></i></span>`;
function render(d){
  document.getElementById('sub').textContent =
    `${d.endpoint_name}（endpoint ${d.endpoint_id}）· 更新于 ${d.updated_at}`;
  const g = d.gpus, free = g.filter(x=>x.free).length;
  const avg = g.length ? g.reduce((s,x)=>s+(x.util||0),0)/g.length : 0;
  const used = g.reduce((s,x)=>s+(x.mem_used_gb||0),0), tot = g.reduce((s,x)=>s+(x.mem_total_gb||0),0);
  const tile = (v,l,c)=>`<div class="tile"><b${c?` style="color:${c};text-shadow:0 0 16px ${c}66"`:''}>${v}</b><i>${l}</i></div>`;
  document.getElementById('tiles').innerHTML =
    tile(g.length,'总卡数') + tile(free,'空闲卡', free ? '#3ddbd9' : '') +
    tile(g.length-free,'使用中') + tile(Math.round(avg)+'%','平均利用率') +
    tile(used.toFixed(0)+'/'+tot.toFixed(0)+'G','显存用量');
  document.getElementById('grid').innerHTML = g.map(x=>{
    const c = utilColor(x.util||0);
    return `<div class="gcard${x.free?' isfree':''}">
      <div class="ghead"><span>GPU ${x.id}</span><span class="badge ${x.free?'free':'run'}">${x.free?'空闲':'运行中'}</span></div>
      <div class="ring" style="--p:${x.util||0};--c:${c}"><div><b style="color:${c}">${Math.round(x.util||0)}%</b><i>利用率</i></div></div>
      <div class="kv"><span>显存 ${bars(x.mem_percent)}</span><em>${x.mem_used_gb.toFixed(1)} / ${x.mem_total_gb.toFixed(1)} G</em></div>
      <div class="kv"><span>温度</span><em class="${x.temp>=75?'hot':''}">${Math.round(x.temp)}°C</em></div>
      <div class="users">${x.free?'<span class="free">空闲</span>':esc(x.users.join('、'))}</div>
    </div>`;}).join('');
  document.getElementById('proc-rows').innerHTML = d.processes.length
    ? d.processes.map(p=>`<tr><td>${p.gpu}</td><td>${esc(p.user)}</td><td>${p.mem_gb.toFixed(1)}G</td>`+
        `<td>${esc(p.instance)}</td><td class="cmd">${esc(p.command)}</td></tr>`).join('')
    : '<tr><td colspan="5" class="empty">当前没有用户进程占用 GPU</td></tr>';
}
async function refresh(){
  try{
    const d = await (await fetch('/api/status')).json();
    if(d.error){document.getElementById('sub').innerHTML = `<span class="err">错误：${esc(d.error)}</span>`;return}
    render(d);
  }catch(e){document.getElementById('sub').innerHTML = `<span class="err">请求失败：${esc(String(e))}</span>`}
}
refresh();
setInterval(refresh, INTERVAL * 1000);
</script>
</body>
</html>
"""


def build_status(url: str, endpoint_id: int, token_file: Path, timeout: float) -> dict:
    """抓取平台并组装大屏 JSON；401 时自动续期后重试一次。"""
    base = url.rstrip("/")
    token = read_token(token_file)

    def fetch_both(tok: str):
        endpoint_payload = _get_json(f"{base}/api/endpoints/{endpoint_id}", tok, timeout)
        process_payload = _get_json(f"{base}/api/gpustatReal/{endpoint_id}", tok, timeout)
        return endpoint_payload, process_payload

    try:
        endpoint_payload, process_payload = fetch_both(token)
    except RuntimeError as exc:
        if "401" not in str(exc):
            raise
        token = refresh_jwt(base, token, timeout)  # 过期自动续期
        save_token(token_file, token)
        endpoint_payload, process_payload = fetch_both(token)

    snapshots = parse_snapshots(endpoint_payload)
    processes = parse_processes(process_payload)
    endpoint_name = str(endpoint_payload.get("Name", "")) if isinstance(endpoint_payload, dict) else ""

    users_by_gpu: dict[int, dict[str, dict]] = {}
    detail: list[dict] = []
    for process in processes:
        if process.is_system:
            continue
        users = users_by_gpu.setdefault(process.gpu_id, {})
        entry = users.setdefault(process.user, {"mem_gb": 0.0, "procs": 0})
        entry["mem_gb"] += process.mem_mb / 1024
        entry["procs"] += 1
        detail.append({
            "gpu": process.gpu_id,
            "user": process.user,
            "mem_gb": round(process.mem_mb / 1024, 1),
            "instance": process.instance,
            "command": process.command[:90],
        })

    total_by_gpu: dict[int, float] = {}
    for process in processes:
        total_by_gpu[process.gpu_id] = max(total_by_gpu.get(process.gpu_id, 0.0), process.mem_total_mb)

    snapshot_map = {s.gpu_id: s for s in snapshots}
    gpu_ids = sorted(set(snapshot_map) | set(total_by_gpu) | set(users_by_gpu))
    gpus = []
    for gpu_id in gpu_ids:
        snapshot = snapshot_map.get(gpu_id)
        used_mb = sum(u["mem_gb"] for u in users_by_gpu.get(gpu_id, {}).values()) * 1024
        total_mb = total_by_gpu.get(gpu_id, 0.0)
        gpus.append({
            "id": gpu_id,
            "name": snapshot.name if snapshot else "",
            "util": snapshot.util if snapshot else 0.0,
            "temp": snapshot.temperature if snapshot else 0.0,
            "mem_percent": snapshot.mem_percent if snapshot else 0.0,
            "mem_used_gb": round(used_mb / 1024, 1),
            "mem_total_gb": round(total_mb / 1024, 1),
            "free": gpu_id not in users_by_gpu,
            "users": sorted(users_by_gpu.get(gpu_id, {})),
        })
    return {
        "ok": True,
        "endpoint_name": endpoint_name,
        "endpoint_id": endpoint_id,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "gpus": gpus,
        "processes": sorted(detail, key=lambda p: (p["gpu"], p["user"])),
    }


def _get_json(url: str, token: str, timeout: float):
    """GET 一个平台接口并解析 JSON；401/403 抛出带状态码的 RuntimeError。"""
    request = Request(url, headers=auth_headers(token), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError(f"平台返回 401，Token 已过期（{url}）") from exc
        raise RuntimeError(f"平台返回 HTTP {exc.code}：{url}") from exc


def renewal_loop(url: str, token_file: Path, interval: float, timeout: float) -> None:
    """后台定期续期 JWT，让会话像网页开着一样一直有效。"""
    while True:
        time.sleep(interval)
        try:
            with REFRESH_LOCK:
                token = read_token(token_file)
                save_token(token_file, refresh_jwt(url, token, timeout))
            print(f"[{datetime.now().strftime('%H:%M:%S')}] JWT 已自动续期")
        except (OSError, RuntimeError) as exc:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] JWT 续期失败：{exc}", file=sys.stderr)


class DashboardHandler(BaseHTTPRequestHandler):
    """只读路由：/ 返回大屏页面，/api/status 返回带缓存的 JSON。

    配置与缓存都挂在 server 实例上（main() 注入）。
    """

    def do_GET(self) -> None:  # noqa: N802（http.server 命名约定）
        if self.path == "/":
            body = _HTML.replace("__INTERVAL__", str(self.server.config.interval)).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            payload = self._status_cached()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        else:
            self._send(404, b'{"error":"not found"}', "application/json; charset=utf-8")

    def _status_cached(self) -> dict:
        cfg = self.server.config
        now = time.monotonic()
        with STATUS_CACHE_LOCK:
            cached = getattr(self.server, "cache", None)
            if cached and now - cached[0] < cfg.cache_ttl:
                return cached[1]
        try:
            payload = build_status(cfg.url, cfg.endpoint_id, cfg.token_file, cfg.timeout)
        except (OSError, RuntimeError, ValueError) as exc:
            payload = {"ok": False, "error": str(exc)}
        with STATUS_CACHE_LOCK:
            self.server.cache = (now, payload)
        return payload

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # 安静模式：不刷屏，只保留启动与续期日志


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU 监控本地大屏（自动续期 JWT，无需登录）")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="平台地址")
    parser.add_argument("--endpoint-id", type=int, default=DEFAULT_ENDPOINT_ID, help="服务器 endpoint id")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE, help="JWT token 文件")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--interval", type=int, default=15, help="页面自动刷新秒数")
    parser.add_argument("--cache-ttl", type=float, default=DEFAULT_CACHE_TTL, help="平台数据缓存秒数")
    parser.add_argument("--renew-interval", type=float, default=DEFAULT_RENEW_INTERVAL, help="JWT 续期间隔秒数")
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT, help="上游请求超时秒数")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # 启动前确认 token 可用，尽早给出明确报错
    read_token(args.token_file)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.config = args
    server.cache = None

    renewer = threading.Thread(
        target=renewal_loop,
        args=(args.url, args.token_file, args.renew_interval, args.timeout),
        daemon=True,
    )
    renewer.start()

    address = f"http://{args.host}:{args.port}/"
    print(f"GPU 大屏已启动：{address}（Ctrl+C 停止）")
    print(f"JWT 每 {args.renew_interval:g}s 自动续期；页面每 {args.interval}s 刷新")
    if not args.no_open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
