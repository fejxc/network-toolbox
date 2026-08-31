"""dingtalk.py 的单元测试（仅标准库，不发起真实网络请求）。

运行：python3 monitors/test_dingtalk.py
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

# 与被测模块同目录；直接运行本文件时脚本目录已在 sys.path 上。
from dingtalk import DEFAULT_KEYWORD, DingTalkNotifier

WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=secret-token"


def make_urlopen(raw: bytes = b'{"errcode":0,"errmsg":"ok"}'):
    """返回一个可当上下文管理器使用的 urlopen mock。"""
    response = MagicMock()
    response.__enter__.return_value.read.return_value = raw
    return MagicMock(return_value=response)


class DingTalkSendTest(unittest.TestCase):
    def test_send_embeds_keyword_and_markdown(self):
        with patch("dingtalk.urllib.request.urlopen", make_urlopen()) as urlopen:
            DingTalkNotifier(webhook=WEBHOOK, keyword="MDPI").send("状态变化", "正文内容")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertIn("MDPI", payload["markdown"]["title"])
        self.assertIn("### MDPI｜状态变化", payload["markdown"]["text"])
        self.assertIn("正文内容", payload["markdown"]["text"])

    def test_send_uses_default_keyword_when_blank(self):
        with patch("dingtalk.urllib.request.urlopen", make_urlopen()) as urlopen:
            DingTalkNotifier(webhook=WEBHOOK, keyword="  ").send("t", "m")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn(DEFAULT_KEYWORD, payload["markdown"]["title"])

    def test_rejects_non_dingtalk_webhook(self):
        notifier = DingTalkNotifier(webhook="https://evil.example.com/robot?access_token=x")
        with self.assertRaisesRegex(RuntimeError, "oapi.dingtalk.com"):
            notifier.send("t", "m")

    def test_rejects_empty_webhook(self):
        with self.assertRaisesRegex(RuntimeError, "未配置"):
            DingTalkNotifier(webhook="").send("t", "m")

    def test_http_error_does_not_leak_token(self):
        exc = urllib.error.HTTPError(WEBHOOK, 400, "Bad Request", hdrs=None, fp=None)
        with patch("dingtalk.urllib.request.urlopen", MagicMock(side_effect=exc)):
            with self.assertRaisesRegex(RuntimeError, "HTTP 400") as ctx:
                DingTalkNotifier(webhook=WEBHOOK).send("t", "m")
        self.assertNotIn("secret-token", str(ctx.exception))

    def test_errcode_nonzero_reports_errmsg(self):
        raw = b'{"errcode":310000,"errmsg":"keywords not in content"}'
        with patch("dingtalk.urllib.request.urlopen", make_urlopen(raw)):
            with self.assertRaisesRegex(RuntimeError, "keywords not in content"):
                DingTalkNotifier(webhook=WEBHOOK).send("t", "m")


class FromEnvTest(unittest.TestCase):
    def test_prefix_env_wins_over_generic(self):
        env = {
            "FOO_DINGTALK_WEBHOOK": WEBHOOK,
            "FOO_DINGTALK_KEYWORD": "FOO",
            "DINGTALK_WEBHOOK": "https://oapi.dingtalk.com/generic",
            "DINGTALK_KEYWORD": "GENERIC",
        }
        with patch.dict("os.environ", env, clear=True):
            notifier = DingTalkNotifier.from_env("FOO")
        self.assertEqual(notifier.webhook, WEBHOOK)
        self.assertEqual(notifier.keyword, "FOO")

    def test_falls_back_to_generic_env(self):
        env = {"DINGTALK_WEBHOOK": WEBHOOK}
        with patch.dict("os.environ", env, clear=True):
            notifier = DingTalkNotifier.from_env("FOO")
        self.assertEqual(notifier.webhook, WEBHOOK)
        self.assertEqual(notifier.keyword, DEFAULT_KEYWORD)

    def test_prefix_only_reads_prefix_env(self):
        env = {"DINGTALK_WEBHOOK": WEBHOOK}
        with patch.dict("os.environ", env, clear=True):
            notifier = DingTalkNotifier.from_env()
        self.assertEqual(notifier.webhook, WEBHOOK)


if __name__ == "__main__":
    unittest.main()
