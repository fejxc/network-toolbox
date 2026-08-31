# 通用组件（common/）

各监控 / 脚本子目录共用的工具类集合。目标是：**新监控只需配置地址和关键词，即可接入通知**。

> 目前仓库内暂无使用方——现有监控（mdpi）为自包含实现、可独立运行；本目录是给后续新监控备用的框架。

## DingTalkNotifier：钉钉机器人通知

代码：[dingtalk.py](dingtalk.py)（仅标准库，不可变配置对象）。单元测试：[test_dingtalk.py](test_dingtalk.py)。

```python
from common.dingtalk import DingTalkNotifier

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

> Webhook 只放环境变量，不要写进代码或 Git；错误信息不会包含 URL（内含 `access_token`）。
