# Linux 容器服务器接入 OpenWrt WireGuard：wireproxy 用户态方案

> 在没有 `NET_ADMIN`、`/dev/net/tun` 或创建 network namespace 权限的 Linux 容器服务器上，通过用户态 `wireproxy` 连接家中 OpenWrt `WG_HOME`，为同一网络命名空间内的 Codex、Claude Code、curl 等程序提供 HTTP / SOCKS5 入口。
>
> 状态：2026-10-22 已在 `192服务器` 完成连通性实测，但未完成 24 小时长时运行验收；OpenWrt 服务端历史私钥泄露整改也尚未确认完成。
>
> 安全说明：本仓库只保存占位符，不保存真实 WireGuard 私钥。历史调试中 OpenWrt `WG_HOME` 接口私钥曾被发送到聊天，应视为已暴露；轮换完成前不要把本方案作为长期依赖。

## 目录

- [1. 结论与边界](#1-结论与边界)
- [2. 地址与端口](#2-地址与端口)
- [3. OpenWrt 配置](#3-openwrt-配置)
- [4. Linux 配置](#4-linux-配置)
- [5. 运维与守护](#5-运维与守护)
- [6. 容器接入代理](#6-容器接入代理)
- [7. 验证](#7-验证)
- [8. 排障](#8-排障)
- [9. 密钥暴露整改](#9-密钥暴露整改)
- [10. 长期验收清单](#10-长期验收清单)

## 1. 结论与边界

这台服务器的容器环境没有 `NET_ADMIN`、没有 `/dev/net/tun`，也不能创建独立 network namespace，因此不能使用 `wg-quick`，也不能创建系统 WireGuard 接口。可行方案是运行纯用户态 `wireproxy`：

```text
Linux 应用
  -> 127.0.0.1:7890 HTTP CONNECT 或 127.0.0.1:27890 SOCKS5
  -> wireproxy 用户态 WireGuard
  -> OpenWrt WG_HOME:51820
  -> PassWall 分流
  -> Internet
```

`wireproxy` 不创建系统网络接口，不修改 Linux 默认路由。只有显式使用代理入口的应用进入 WireGuard；DNS 和未设置代理的进程仍按服务器原路由出网。

方案边界：

| 项 | 结论 |
|---|---|
| 依赖 Mac / ProxyBridge / SSH 反向隧道 | 不依赖 |
| 系统默认路由 | 不改变 |
| 未主动使用代理的进程 | 不进入 OpenWrt 分流 |
| WireGuard 内部路由语义 | `AllowedIPs = 0.0.0.0/0` 只作用于 wireproxy 自己选路 |
| 代理传输 | `wireproxy v1.1.2` 的 HTTP / SOCKS5 入口面向 TCP CONNECT；不要假设 UDP 应用一定可用 |
| IPv6 | 当前只声明 IPv4 隧道语义，必须单独验收 |
| 127.0.0.1 可达范围 | 仅限运行 wireproxy 的同一网络命名空间，见 [6](#6-容器接入代理) |

## 2. 地址与端口

| 项目 | 值 |
|---|---|
| OpenWrt 服务端接口 | `WG_HOME`，`10.66.66.1/24` |
| Linux 隧道地址 | `10.66.66.4/32` |
| OpenWrt 监听 | UDP `51820` |
| Linux 客户端 Endpoint | `hz.fejxc.top:51820` |
| Linux HTTP 入口 | `127.0.0.1:7890` |
| Linux SOCKS5 入口 | `127.0.0.1:27890` |
| wireproxy 健康入口 | `127.0.0.1:27892` |
| MTU | `1380` |
| PersistentKeepalive | `25` |

`7890` 继续保留给现有 Codex 配置，避免修改 `.env` 和启动脚本。不要让 ProxyBridge、旧 SSH 反向代理或其他服务占用 `7890`。`27890` 作为备用 SOCKS5 测试入口；`27892` 只绑定回环，不暴露到局域网或公网。

## 3. OpenWrt 配置

在 `网络 → 接口 → WG_HOME → 对端` 新增 Linux Peer：

| 字段 | 值 |
|---|---|
| 描述 | `192服务器` |
| 公钥 | `<LINUX_PUBLIC_KEY>` |
| 私钥 | 留空 |
| 预共享密钥 | 留空 |
| 允许的 IP | `10.66.66.4/32` |
| Endpoint | 留空 |
| 已禁用 | 不勾选 |

Linux 是主动发起 WireGuard 连接的一端，因此 OpenWrt 侧 Peer 不填写 Endpoint。OpenWrt 只需要保存 Linux 公钥；Linux 私钥只保存在服务器本地，不写入文档、Git、聊天或截图。

## 4. Linux 配置

使用 `wireproxy v1.1.2` Linux amd64 版本。当前安装路径：

```text
/root/.local/lib/wireproxy-1.1.2/wireproxy
```

新增或升级二进制时，应从官方发布页下载，校验版本、架构和 checksum 后再安装；不要使用聊天中转的二进制。当前配置文件路径：

```text
/root/.config/wireproxy/wireproxy.conf
```

配置语义如下；真实密钥只保留在服务器本地，仓库模板见 [clients/linux-server-wireproxy.conf.example](clients/linux-server-wireproxy.conf.example)：

```ini
[Interface]
Address = 10.66.66.4/32
PrivateKey = <LINUX_PRIVATE_KEY>
DNS = 10.66.66.1
MTU = 1380
CheckAlive = 10.66.66.1
CheckAliveInterval = 5

[Peer]
PublicKey = <OPENWRT_WG_HOME_PUBLIC_KEY>
Endpoint = hz.fejxc.top:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25

[http]
BindAddress = 127.0.0.1:7890

[Socks5]
BindAddress = 127.0.0.1:27890
```

说明：

- `DNS = 10.66.66.1` 是 wireproxy 内部解析语义，不是修改 `/etc/resolv.conf`，也不会接管系统 DNS。
- `AllowedIPs = 0.0.0.0/0` 是 wireproxy 内部路由语义，不会改变 Linux 系统默认路由。
- `CheckAlive` 让 `27892/readyz` 能反映 `Linux -> OpenWrt` 的活跃探测结果；没有它，健康入口只能提供 `/metrics`，不能直接表达最近 pong。
- 配置文件必须只包含 Linux 私钥，不要保存 OpenWrt 接口私钥。

设置权限并做配置校验：

```bash
install -d -m 700 /root/.config/wireproxy
install -m 600 /dev/null /root/.config/wireproxy/wireproxy.conf
/root/.local/lib/wireproxy-1.1.2/wireproxy \
  -c /root/.config/wireproxy/wireproxy.conf \
  -n
```

## 5. 运维与守护

当前服务器管理脚本：

```text
/root/wireproxy.sh
```

可复用模板见 [linux/wireproxy.sh.example](linux/wireproxy.sh.example)。常用命令：

```bash
bash /root/wireproxy.sh start
bash /root/wireproxy.sh status
bash /root/wireproxy.sh check
bash /root/wireproxy.sh restart
bash /root/wireproxy.sh stop
bash /root/wireproxy.sh logs
```

脚本要点：

- `start` 可安全重复执行；通过 PID 文件、可执行文件路径和进程状态识别自己的实例，不使用裸 `pkill` 或 `killall`。
- 启动命令携带 `-i 127.0.0.1:27892`，否则健康端口不会监听。
- `check` 通过 `127.0.0.1:7890` 访问 OpenAI API；未携带凭据时返回 HTTP 401 即表示 HTTPS / CONNECT 链路可达。
- `watch` 每 5 秒检查一次进程，崩溃后调用 `start`。前台 `Ctrl+C` 只退出守护，保留代理进程。

可选前台守护：

```bash
bash /root/wireproxy.sh watch
```

`watch` 只是进程守护，不处理 WireGuard 握手失败、UDP 路径故障、DDNS 失效或 OpenWrt 侧异常。健康检查失败时不应盲目重启进程，先看日志和 `/metrics`。

当前 wireproxy 尚未接入容器启动流程。服务器或容器重启后需要手动执行：

```bash
bash /root/wireproxy.sh start
```

后台运行不等于自动启动。若要真正自启，必须加入宿主机的容器启动命令、entrypoint、compose command 或受支持的 init / supervisor 流程；不要只在 shell 配置里追加 `start`。

## 6. 容器接入代理

`127.0.0.1` 不会跨网络命名空间。先确认 Codex / Claude Code 和 wireproxy 是否在同一网络命名空间：

| 部署形态 | 接入方式 |
|---|---|
| Codex / Claude Code 与 wireproxy 都在同一容器或 `network_mode: host` | 继续使用 `http://127.0.0.1:7890` |
| 普通 bridge 容器访问宿主机 wireproxy | 不能使用容器内 `127.0.0.1`；改用 host network，或将 wireproxy 只绑定到受防火墙保护的 Docker 网关地址，并为代理加认证 |
| 多个不可信容器共用代理 | 禁止共享无认证入口；必须加认证、网络策略和最小允许列表 |

Codex 当前固定读取 `/root/.codex/.env` 时可使用：

```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=localhost,127.0.0.1,::1
http_proxy=http://127.0.0.1:7890
https_proxy=http://127.0.0.1:7890
no_proxy=localhost,127.0.0.1,::1
```

普通 shell 可手动导出同一组变量。不要同时设置指向另一个 SOCKS 端口的 `ALL_PROXY`，避免应用把 HTTP 入口当成 SOCKS5 或产生两层代理。

## 7. 验证

### 7.1 已实测结果

2026-10-22 在 `192服务器` 上验证：

| 检查 | 结果 |
|---|---|
| wireproxy 配置校验 | 通过 |
| WireGuard 握手 | 成功，双向字节增长 |
| HTTP 入口访问 OpenAI API | HTTP 401，HTTPS 链路可达 |
| HTTP 入口访问 ChatGPT 接口 | HTTP 401，HTTPS 链路可达 |
| HTTP 入口查询 IPv4 出口 | 返回 PassWall 预期出口 |
| SOCKS5 入口查询 IPv4 出口 | 与 HTTP 入口一致 |
| 清除代理后直连 OpenAI / ChatGPT | 超时，证明服务器默认路由未进入该链路 |

HTTP 401 是未携带凭据访问 API 的预期响应；它证明 TCP / TLS / 代理链路可达，不等价于 Codex 登录态或完整业务验收。

### 7.2 进程和端口

```bash
bash /root/wireproxy.sh status
ss -lntp | grep -E ':(7890|27890|27892)\b'
curl -fsS http://127.0.0.1:27892/readyz; printf '\n'
curl -fsS http://127.0.0.1:27892/metrics | sed -n '1,80p'
```

`/metrics` 应能看到最新握手时间、传输字节数和 Endpoint。`/readyz` 期望 HTTP 200；若返回 503，先检查 OpenWrt、主路由 UDP 转发和 DDNS。

### 7.3 代理出口

把 `<PASSWALL_EGRESS_IPV4>` 替换为 Mac / iPhone 相同规则下应该出现的 IPv4：

```bash
curl --proxy http://127.0.0.1:7890 https://api.ipify.org; printf '\n'
curl --proxy socks5h://127.0.0.1:27890 https://api.ipify.org; printf '\n'
```

两个入口应返回 PassWall 预期出口。再验证 API 链路：

```bash
curl --proxy http://127.0.0.1:7890 -o /dev/null -sS -w '%{http_code}\n' https://api.openai.com/v1/models
```

未携带凭据时 HTTP 401 是预期结果。它证明 TCP / TLS / 代理链路可达，不等价于 Codex 登录态或完整业务验收。

### 7.4 反向验证

清除当前 shell 代理后再直连：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  curl --connect-timeout 5 https://api.ipify.org; printf '\n'
```

如果直连超时而代理出口正常，说明服务器默认路由没有进入该链路，代理语义符合预期。

## 8. 排障

按顺序检查：

1. 端口：确认没有旧 SSH 反向代理、ProxyBridge 或旧 wireproxy 占用 `7890`。
2. 配置：`wireproxy -n` 必须通过；确认配置文件路径和权限。
3. 进程：`status` 中的 PID 必须仍存在，且启动命令包含当前配置路径和 `-i 127.0.0.1:27892`。
4. 健康：`/readyz` 返回 503 或 `/metrics` 无 handshake 时，检查 DDNS 解析、主路由 UDP `51820` 转发、OpenWrt `WG_HOME` 和 Linux Peer。
5. 出口：握手正常但出口不符合预期时，检查 PassWall 对 `10.66.66.4` 的分流规则。
6. TLS / 大包失败：ping 可通但 HTTPS 卡顿或部分站点失败时，尝试继续降低 MTU，例如 `1360`。
7. 容器边界：bridge 容器内无法访问 `127.0.0.1:7890` 不是 wireproxy 故障，应改用 [6](#6-容器接入代理) 中对应方案。

常见错误：

| 现象 | 常见原因 |
|---|---|
| 配置校验失败 | 密钥占位符未替换、路径错误、重复监听段或配置字段拼写错误 |
| 端口未监听 | 启动失败；先看 `logs`，不要只看 `watch` 是否仍在 |
| `/metrics` handshake 为 0 | Endpoint、公钥、UDP 转发或 DDNS 错误 |
| 有握手但无出口 | OpenWrt 旁路由缺默认网关、防火墙区域错误或 PassWall 分流不匹配 |
| 只有一个入口可用 | HTTP 和 SOCKS5 的协议 scheme 混用，或客户端缓存了旧代理 |

## 9. 密钥暴露整改

调试过程中，OpenWrt `WG_HOME` 服务端接口私钥曾被发送到聊天中。该私钥应视为已暴露，必须轮换：

1. 在 OpenWrt 备份当前 `WG_HOME` 配置。
2. 轮换 `WG_HOME` 接口密钥对，只保留新公钥在 UI 中。
3. 同步更新 Mac、iPhone 和 Linux 配置中的服务端公钥。
4. 逐个重启客户端 WireGuard，确认握手和分流。
5. 确认全部设备恢复后，再废弃旧服务端密钥。
6. 在本文和主 README 中记录轮换完成日期，确认“历史私钥未处置”状态解除。

Linux 私钥仍在服务器本地，权限应为 `600`，未写入文档。若怀疑泄露，应重新生成 Linux 密钥对，更新 OpenWrt Linux Peer 公钥，并重启 wireproxy。

## 10. 长期验收清单

继续验收前确认：

- [ ] OpenWrt `WG_HOME` 历史私钥已轮换，所有客户端已切换新服务端公钥。
- [ ] PassWall 对 `10.66.66.4` 的分流规则与 Mac / iPhone 预期一致。
- [ ] HTTP `7890`、SOCKS5 `27890` 和健康 `27892` 都只绑定到预期网络边界。
- [ ] `wireproxy` 连续运行至少 24 小时，观察 `/readyz`、握手、传输字节、内存、日志和代理失败率。
- [ ] IPv4 出口符合规则；IPv6 泄漏已单独检查。
- [ ] 容器重启、OpenWrt 重启、家庭公网 IP 变化和 DDNS 更新后的恢复路径可用。
- [ ] Codex、Claude Code 在真实业务流量下完成端到端验证。
- [ ] 自动启动方案已落到受支持的容器 entrypoint / command / init / supervisor，而不是只依赖手动后台启动。
