# 监控脚本与钉钉通知

本目录收纳所有「定时监控 + 变化提醒」类脚本，以及它们共用的钉钉通知组件。**以后新增监控也放在本目录**：直接复用 `dingtalk.py`，配置 Webhook + 关键词即可接入推送。

## 目录结构

```text
monitors/
├── README.md                  # 本文件
├── dingtalk.py                # 钉钉机器人通知工具类（监控共用）
├── test_dingtalk.py           # dingtalk.py 单元测试
├── mdpi_monitor.py            # MDPI 投稿状态监控
├── mdpi_monitor.env.example   # MDPI 监控配置模板
├── gpu_monitor.py             # 学校 GPU 平台监控
├── gpu_dashboard.py           # GPU 本地大屏（自动续期，免登录）
└── gpu_monitor.env.example    # GPU 监控配置模板
```

> `mdpi_monitor.py` 依赖同目录的 `dingtalk.py`（同目录 import，无路径配置），两者需一起拷贝部署。

---

## MDPI 投稿状态监控

[mdpi_monitor.py](mdpi_monitor.py) 只使用 Python 标准库，不需要额外安装依赖。

### 配置 Cookie

脚本不会自动登录，也不会把会话 Cookie 写入状态文件。把浏览器导出的 Cookie header 保存到权限受限的文件：

```bash
mkdir -p ~/.config/mdpi-monitor
umask 077
${EDITOR:-vi} ~/.config/mdpi-monitor/cookie
chmod 600 ~/.config/mdpi-monitor/cookie
```

文件内容可以是 `name=value; name2=value2`，也可以直接粘贴完整 `curl` 命令；脚本会读取其中的 `-b/--cookie` 参数。建议在实际运行机器的浏览器重新导出 Cookie。

### 运行

从仓库根目录运行，默认每 5 分钟检查一次，提醒方式默认为 macOS 系统通知（可选 `dingtalk` / `both` / `none`）：

```bash
python3 monitors/mdpi_monitor.py --notify dingtalk --interval 300
```

首次运行只建立状态基线，不发送通知。之后只有同一稿件的 `status` 字段发生变化才推送；新稿件出现、标题变化和日期变化不会单独推送。

也可以只检查一次：

```bash
python3 monitors/mdpi_monitor.py --once --notify none
```

Cookie 过期或 MDPI 返回登录页时，脚本会提示重新从浏览器导出 Cookie。状态基线默认保存到 `~/.cache/mdpi-monitor/state.json`。

配置模板：[mdpi_monitor.env.example](mdpi_monitor.env.example)。

### 钉钉机器人推送（MDPI）

在钉钉群自定义机器人安全设置中，关键词建议配置为 **`MDPI`**。脚本会在消息标题和正文中都带上这个词，满足关键词校验。钉钉官方说明见[获取自定义机器人 Webhook 地址](https://open.dingtalk.com/document/orgapp/custom-robot-access)。

不要把带 `access_token` 的完整地址写入代码或提交到 Git；使用环境变量：

```bash
export MDPI_DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=替换为新token'
export MDPI_DINGTALK_KEYWORD='MDPI'
python3 monitors/mdpi_monitor.py --notify dingtalk --interval 300
```

如果希望同时保留 macOS 通知，把 `--notify` 换成 `both`。

### 查看日志

脚本本身只输出到终端（stdout/stderr），不会自己写日志文件。如需保留历史记录，用重定向追加到本目录的 `mdpi_monitor.log`（已被 `.gitignore` 忽略）：

```bash
python3 monitors/mdpi_monitor.py --notify dingtalk --interval 300 >> monitors/mdpi_monitor.log 2>&1
```

查看最近记录：`tail -n 50 monitors/mdpi_monitor.log`；实时跟踪：`tail -f monitors/mdpi_monitor.log`。

---

## GPU 平台监控

[gpu_monitor.py](gpu_monitor.py) 只用标准库，查看学校 GPU 平台（Portainer 变体）上每张卡的利用率 / 显存 / 温度和使用人。数据来自两个只读接口：`/api/endpoints/{id}` 的快照（每卡聚合）与 `/api/gpustatReal/{id}`（进程级占用）。刻意不调用 `/api/users/usergpus/{id}`——该接口会把账号明文密码一起返回。

### 配置 Token

平台 JWT 只从本地文件或环境变量读取，不入仓库。在浏览器登录平台后，从任意 `/api/...` 请求头里复制 `Bearer ` 后面的 Token：

```bash
mkdir -p ~/.config/gpu-monitor && umask 077
${EDITOR:-vi} ~/.config/gpu-monitor/token     # 粘贴 Token，末尾不要换行也行
chmod 600 ~/.config/gpu-monitor/token
```

Token 是 JWT（平台约每几小时过期）。**续期无需账号密码**：平台前端本身就是靠定期 `POST /api/auth/validate` 维持登录的，脚本复用同一机制，用旧 JWT 换新 JWT 并写回 token 文件：

```bash
python3 monitors/gpu_monitor.py --refresh-token    # 手动续期一次
```

长期保活用 cron（每 30 分钟一次即可，和网页开着不关一个效果）：

```bash
*/30 * * * * /usr/bin/python3 /path/to/network-toolbox/monitors/gpu_monitor.py --refresh-token >> /tmp/gpu-token-refresh.log 2>&1
```

`--watch` 模式下遇到 401 也会自动续期后重试。若超过过期窗口一直没续期（如长期关机），validate 会返回 401，此时需重新登录导出一次。

### 运行

```bash
python3 monitors/gpu_monitor.py                    # 打印一次当前状态
python3 monitors/gpu_monitor.py --watch            # 持续刷新（默认 60s）
python3 monitors/gpu_monitor.py --endpoint-id 117  # 其它服务器
```

### 空闲卡钉钉提醒（配合 --watch）

```bash
export GPU_DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=替换'
export GPU_DINGTALK_KEYWORD='GPU'
python3 monitors/gpu_monitor.py --watch --interval 120 --alert-free 2 --notify dingtalk
```

空闲卡数**从阈值以下涨到以上**时推送一次（状态沿触发，不重复轰炸）。配置模板：[gpu_monitor.env.example](gpu_monitor.env.example)。

### GPU 本地大屏（免登录）

[gpu_dashboard.py](gpu_dashboard.py) 起一个仅本机可访问的 HTTP 服务，浏览器看卡状态，**从此不用登录学校平台**：

```bash
python3 monitors/gpu_dashboard.py          # 默认 http://127.0.0.1:8787/ 并自动打开浏览器
```

- 顶部实时时钟（秒级跳动）；页面默认每 5 秒自动刷新（`--interval` 可调），数据带 5 秒缓存（`--cache-ttl`），失败结果不缓存；
- 后台线程每 30 分钟自动续期 JWT（`--renew-interval`），与 `--refresh-token`/cron 互不冲突，共用同一 token 文件；
- 401 时自动续期重试；服务只绑定 `127.0.0.1`，Token 不会出现在页面里；
- 换服务器：`--endpoint-id 117`；换端口：`--port 8888`；不开浏览器：`--no-open`。

---

## 钉钉通知框架（dingtalk.py）

[dingtalk.py](dingtalk.py) 仅用标准库，核心是不可变工具类 `DingTalkNotifier`：

```python
from dingtalk import DingTalkNotifier

notifier = DingTalkNotifier.from_env("MDPI")   # 读 MDPI_DINGTALK_WEBHOOK / MDPI_DINGTALK_KEYWORD
notifier.send("标题", "正文")                   # markdown 消息，标题自动带关键词
```

### 环境变量

| 环境变量 | 说明 |
|---|---|
| `<前缀>_DINGTALK_WEBHOOK` | 该监控专属 Webhook（优先） |
| `<前缀>_DINGTALK_KEYWORD` | 该监控专属安全关键词（优先） |
| `DINGTALK_WEBHOOK` | 通用兜底 Webhook |
| `DINGTALK_KEYWORD` | 通用兜底关键词，缺省 `NOTIFY` |

### 新监控接入三步

1. 钉钉群添加自定义机器人，安全设置选「自定义关键词」，复制 Webhook；
2. 设置环境变量（专属前缀或通用名均可）：
   ```bash
   export MYMON_DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=***'
   export MYMON_DINGTALK_KEYWORD='MYMON'
   ```
3. 监控代码里两行接入：
   ```python
   notifier = DingTalkNotifier.from_env("MYMON")
   notifier.send("监控标题", "变化详情")
   ```

框架约定：消息以 markdown 发送、标题行自动携带关键词；Webhook 仅允许 `https://oapi.dingtalk.com`；错误信息绝不包含 Webhook URL（内含 `access_token`）。

### 运行测试

```bash
python3 monitors/test_dingtalk.py
```

9 个用例覆盖：关键词嵌入、Webhook 校验、HTTP 错误不泄漏 token、errcode 处理、环境变量优先级。全部为 mock，不发起真实请求。
