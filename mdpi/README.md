# MDPI 投稿状态监控

脚本位于本目录的 [mdpi_monitor.py](mdpi_monitor.py)，只使用 Python 标准库，不需要额外安装依赖。

## 配置 Cookie

脚本不会自动登录，也不会把会话 Cookie 写入状态文件。把浏览器导出的 Cookie header 保存到权限受限的文件：

```bash
mkdir -p ~/.config/mdpi-monitor
umask 077
${EDITOR:-vi} ~/.config/mdpi-monitor/cookie
chmod 600 ~/.config/mdpi-monitor/cookie
```

文件内容可以是 `name=value; name2=value2`，也可以直接粘贴完整 `curl` 命令；脚本会读取其中的 `-b/--cookie` 参数。建议在实际运行机器的浏览器重新导出 Cookie。

## 运行

从仓库根目录运行，默认每 5 分钟检查一次：

```bash
python3 mdpi/mdpi_monitor.py --notify dingtalk --interval 300
```

首次运行只建立状态基线，不发送通知。之后只有同一稿件的 `status` 字段发生变化才推送；新稿件出现、标题变化和日期变化不会单独推送。

也可以只检查一次：

```bash
python3 mdpi/mdpi_monitor.py --once --notify none
```

Cookie 过期或 MDPI 返回登录页时，脚本会提示重新从浏览器导出 Cookie。状态基线默认保存到 `~/.cache/mdpi-monitor/state.json`。

## 钉钉机器人推送

在钉钉群自定义机器人安全设置中，关键词建议配置为 **`MDPI`**。脚本会在消息标题和正文中都带上这个词，满足关键词校验。钉钉官方说明见[获取自定义机器人 Webhook 地址](https://open.dingtalk.com/document/orgapp/custom-robot-access)。

不要把带 `access_token` 的完整地址写入代码或提交到 Git；使用环境变量：

```bash
export MDPI_DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=替换为新token'
export MDPI_DINGTALK_KEYWORD='MDPI'
python3 mdpi/mdpi_monitor.py --notify dingtalk --interval 300
```

如果希望同时保留 macOS 通知：

```bash
python3 mdpi/mdpi_monitor.py --notify both --interval 300
```

配置模板：[mdpi_monitor.env.example](mdpi_monitor.env.example)。

## 查看日志

监控日志默认追加到本目录的 `mdpi_monitor.log`。查看最近记录：

```bash
tail -n 50 mdpi/mdpi_monitor.log
```

实时跟踪：

```bash
tail -f mdpi/mdpi_monitor.log
```
