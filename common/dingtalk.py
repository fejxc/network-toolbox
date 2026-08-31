"""通用钉钉自定义机器人通知工具类。

任何监控脚本只需「Webhook 地址 + 安全关键词」即可接入推送：

    from common.dingtalk import DingTalkNotifier

    # 方式一：从环境变量读取（推荐）
    #   优先读 <前缀>_DINGTALK_WEBHOOK / <前缀>_DINGTALK_KEYWORD，
    #   其次读通用的 DINGTALK_WEBHOOK / DINGTALK_KEYWORD。
    notifier = DingTalkNotifier.from_env("MDPI")

    # 方式二：构造时直接传参
    notifier = DingTalkNotifier(
        webhook="https://oapi.dingtalk.com/robot/send?access_token=***",
        keyword="MDPI",
    )

    notifier.send("标题", "正文")

约定：

1. 消息以 markdown 发送，标题行自动携带关键词，满足机器人关键词安全校验；
2. Webhook 仅允许 https://oapi.dingtalk.com；
3. 所有错误信息绝不包含 Webhook URL（其中带 access_token）。
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DINGTALK_WEBHOOK_HOST = "oapi.dingtalk.com"
DEFAULT_KEYWORD = "NOTIFY"
DEFAULT_TIMEOUT = 20.0


def clean_text(value: str) -> str:
    """反转义 HTML 实体，并把连续空白压成单个空格。"""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


@dataclass(frozen=True)
class DingTalkNotifier:
    """钉钉自定义机器人通知器（不可变配置对象）。

    webhook：机器人 Webhook 地址；
    keyword：机器人安全关键词，消息标题行会自动带上；
    timeout：单次请求超时秒数。
    """

    webhook: str
    keyword: str = DEFAULT_KEYWORD
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, prefix: str = "", *, timeout: float = DEFAULT_TIMEOUT) -> "DingTalkNotifier":
        """从环境变量构造；prefix 让多个监控各自独立配置、互不干扰。

        查找顺序（命中即用）：
          webhook：``<prefix>_DINGTALK_WEBHOOK`` → ``DINGTALK_WEBHOOK``
          keyword：``<prefix>_DINGTALK_KEYWORD`` → ``DINGTALK_KEYWORD`` → 默认值
        """
        webhook_names = ([f"{prefix}_DINGTALK_WEBHOOK"] if prefix else []) + ["DINGTALK_WEBHOOK"]
        webhook = next((os.environ[name] for name in webhook_names if os.environ.get(name)), "")

        keyword_names = ([f"{prefix}_DINGTALK_KEYWORD"] if prefix else []) + ["DINGTALK_KEYWORD"]
        keyword = next((os.environ[name] for name in keyword_names if os.environ.get(name)), "")

        return cls(webhook=webhook, keyword=keyword or DEFAULT_KEYWORD, timeout=timeout)

    def _validate(self) -> None:
        """校验 Webhook 已配置且指向钉钉官方域名；错误信息不含 URL 本身。"""
        if not self.webhook:
            raise RuntimeError("未配置钉钉 Webhook：请设置环境变量或构造参数。")
        parsed = urllib.parse.urlparse(self.webhook)
        if parsed.scheme != "https" or parsed.netloc != DINGTALK_WEBHOOK_HOST:
            raise RuntimeError("钉钉 Webhook 地址必须是 https://oapi.dingtalk.com/...。")

    def send(self, title: str, message: str) -> None:
        """发送 markdown 消息；失败抛 RuntimeError，错误信息不含 Webhook URL。"""
        self._validate()

        safe_keyword = clean_text(self.keyword) or DEFAULT_KEYWORD
        markdown = f"### {safe_keyword}｜{title}\n\n{message}\n"
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": f"{safe_keyword}｜{title}", "text": markdown},
        }
        request = urllib.request.Request(
            self.webhook,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # 不把异常本身带进错误信息：其字符串里可能包含 URL 中的 access_token。
            raise RuntimeError(f"钉钉 Webhook 返回 HTTP {exc.code}。") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"钉钉 Webhook 访问失败：{exc.reason}") from exc

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("钉钉 Webhook 返回了无法解析的响应。") from exc
        if result.get("errcode") != 0:
            errmsg = clean_text(str(result.get("errmsg", "未知错误")))
            raise RuntimeError(f"钉钉推送失败：{errmsg}")
