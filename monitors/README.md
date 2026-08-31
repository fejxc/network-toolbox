# 监控脚本与钉钉通知

本目录收纳所有「定时监控 + 变化提醒」类脚本，以及它们共用的钉钉通知组件。**以后新增监控也放在本目录**：直接复用 `dingtalk.py`，配置 Webhook + 关键词即可接入推送。

## 目录结构

```text
monitors/
├── README.md                  # 本文件
├── dingtalk.py                # 钉钉机器人通知工具类（监控共用）
├── test_dingtalk.py           # dingtalk.py 单元测试
├── mdpi_monitor.py            # MDPI 投稿状态监控
└── mdpi_monitor.env.example   # MDPI 监控配置模板
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
