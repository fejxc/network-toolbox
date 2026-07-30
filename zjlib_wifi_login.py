#!/usr/bin/env python3
"""Login helper for Zhejiang Library Wi-Fi captive portal."""

from __future__ import annotations

import argparse
import http.client
import html.parser
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from pathlib import Path


DEFAULT_URL = "https://2.2.1.1:8443/cn/index.html"
PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


@dataclass
class Form:
    action: str = ""
    method: str = "GET"
    inputs: list[dict[str, str]] = field(default_factory=list)


class PortalParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.iframes: list[str] = []
        self.forms: list[Form] = []
        self._current_form: Form | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "iframe" and attr.get("src"):
            self.iframes.append(attr["src"])
        elif tag == "form":
            self._current_form = Form(
                action=attr.get("action", ""),
                method=(attr.get("method", "GET") or "GET").upper(),
            )
        elif tag == "input" and self._current_form is not None:
            self._current_form.inputs.append(attr)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def clear_proxy_environment() -> None:
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "2.2.1.1,2.2.1.0/24,localhost,127.0.0.1"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def build_opener() -> tuple[urllib.request.OpenerDirector, CookieJar]:
    context = ssl._create_unverified_context()
    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(cookie_jar),
    )
    return opener, cookie_jar


def request_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    referer: str | None = None,
    timeout: float = 10,
    return_headers: bool = False,
    extra_headers: dict[str, str] | None = None,
):
    """Fetch ``url`` via ``opener``.

    Returns ``(status, final_url, body)``; when ``return_headers`` is set, also
    appends a fourth ``headers`` element (a ``dict``) for debugging.

    Non-2xx responses (e.g. 404 when already authenticated) are returned as a
    normal triple instead of raising, so callers can branch on status.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 zjlib-wifi-login/2.0",
        "Accept": "text/html,application/xhtml+xml,application/xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            decoded = body.decode(charset, errors="replace")
            result = (resp.status, resp.geturl(), decoded)
            resp_headers = {k: v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        # Treat non-2xx (e.g. 404 when already online) as a normal response so the
        # caller's status check can handle it instead of crashing the whole run.
        decoded = ""
        resp_headers = {}
        try:
            raw = exc.read()
            decoded = raw.decode(exc.headers.get_content_charset() or "utf-8", errors="replace")
            resp_headers = {k: v for k, v in exc.headers.items()}
        except Exception:
            pass
        result = (exc.code, exc.filename or url, decoded)
    except http.client.RemoteDisconnected as exc:
        raise urllib.error.URLError(exc) from exc

    if return_headers:
        return (*result, resp_headers)
    return result


def parse_html(html: str) -> PortalParser:
    parser = PortalParser()
    parser.feed(html)
    return parser


def find_login_page(entry_url: str, entry_html: str) -> str:
    parser = parse_html(entry_html)
    for src in parser.iframes:
        if "login" in src.lower():
            return urllib.parse.urljoin(entry_url, src)
    if parser.iframes:
        return urllib.parse.urljoin(entry_url, parser.iframes[0])
    return entry_url


def pick_form(forms: list[Form]) -> Form | None:
    for form in forms:
        names = " ".join(
            (item.get("name", "") + " " + item.get("id", "")).lower()
            for item in form.inputs
        )
        if any(word in names for word in ("user", "account", "login", "name", "pass", "pwd")):
            return form
    return forms[0] if forms else None


def find_input(form: Form, patterns: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
    for item in form.inputs:
        key = (item.get("name") or item.get("id") or "").strip()
        haystack = " ".join(
            [
                item.get("name", ""),
                item.get("id", ""),
                item.get("placeholder", ""),
                item.get("type", ""),
            ]
        ).lower()
        if key and any(pat in haystack for pat in patterns) and not any(bad in haystack for bad in exclude):
            return key
    return None


def build_payload(form: Form, username: str, password: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for item in form.inputs:
        key = (item.get("name") or item.get("id") or "").strip()
        if not key:
            continue
        input_type = item.get("type", "text").lower()
        key_lower = key.lower()
        if input_type in {"submit", "button", "image", "file"} or key_lower.startswith("login"):
            continue
        if input_type == "checkbox":
            payload[key] = item.get("value") or "on"
            continue
        payload[key] = item.get("value", "")

    user_key = find_input(
        form,
        ("username", "user", "account", "phone", "mobile", "card", "reader"),
        ("anonymous", "redirect", "url", "login"),
    )
    pass_key = find_input(form, ("pass", "pwd", "password"))
    if not user_key:
        user_key = "username"
    if not pass_key:
        pass_key = "password"

    payload[user_key] = username
    payload[pass_key] = password
    return payload


def build_zjlib_ajax_payload(form: Form, username: str, password: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in form.inputs:
        key = (item.get("name") or item.get("id") or "").strip()
        if not key:
            continue
        input_type = item.get("type", "text").lower()
        if input_type == "checkbox":
            values[key] = item.get("value") or "on"
        else:
            values[key] = item.get("value", "")

    # Match the Mac-specific XHR in /cn/login.html submitFunc().
    return {
        "username": username,
        "password": password,
        "RedirectUrl": values.get("RedirectUrl", ""),
        "anonymous": values.get("anonymous", "DISABLE"),
        "anonymousurl": "",
        "checkbox": values.get("checkbox", "on"),
        "checkbox1": values.get("checkbox1", "on"),
        "accesscode": values.get("accesscode", ""),
    }


def looks_successful(text: str, final_url: str) -> bool:
    lowered = text.lower()
    if any(
        token in lowered
        for token in ("auth_success.html", "success", "logout", "user has online", "已登录", "认证成功", "登录成功", "用户已在线")
    ):
        return True
    if "error" in lowered or "失败" in text or "错误" in text:
        return False
    return "login" not in final_url.lower() and "login" not in lowered[:2000]


def save_debug(name: str, text: str) -> Path:
    path = Path.cwd() / name
    path.write_text(text, encoding="utf-8")
    return path


def describe_inputs(form: Form) -> list[str]:
    lines = []
    for item in form.inputs:
        key = (item.get("name") or item.get("id") or "").strip()
        value = item.get("value", "")
        if "pass" in key.lower() or "pwd" in key.lower():
            value = "***"
        lines.append(
            "type={type} name={name} id={id} value={value}".format(
                type=item.get("type", "text"),
                name=item.get("name", ""),
                id=item.get("id", ""),
                value=value,
            )
        )
    return lines


def login(
    entry_url: str,
    username: str,
    password: str,
    timeout: float,
    debug: bool,
    show_inputs: bool,
) -> int:
    clear_proxy_environment()
    opener, cookie_jar = build_opener()

    status, final_entry_url, entry_html = request_text(opener, entry_url, timeout=timeout)
    if status != 200:
        if status == 404:
            print(
                f"认证门户返回 404: {final_entry_url}\n"
                "  门户只在「未认证」状态下伺服登录页。若你当前已能正常上网，说明已认证，无需重复登录；\n"
                "  若确实无法上网，请确认: 1) 已连接 zjlib Wi-Fi;  2) 关闭代理/TUN/VPN 后重试;  3) 入口 URL 正确。",
                file=sys.stderr,
            )
        else:
            print(f"入口页异常: HTTP {status} {final_entry_url}", file=sys.stderr)
        return 2

    login_page = find_login_page(final_entry_url, entry_html)
    status, final_login_url, login_html = request_text(
        opener, login_page, referer=final_entry_url, timeout=timeout
    )
    if status != 200:
        print(f"登录页异常: HTTP {status} {final_login_url}", file=sys.stderr)
        return 2

    parser = parse_html(login_html)
    form = pick_form(parser.forms)
    if form is None:
        debug_path = save_debug("zjlib_login_page_debug.html", login_html)
        print(
            "没有在 iframe 登录页里找到普通 HTML form。"
            f"已保存 {debug_path}，需要根据其中的 JS 登录接口再补一版。",
            file=sys.stderr,
        )
        return 4

    action = form.action or final_login_url
    action_url = urllib.parse.urljoin(final_login_url, action)
    method = form.method if form.method in {"GET", "POST"} else "POST"
    payload = build_zjlib_ajax_payload(form, username, password)
    encoded = urllib.parse.urlencode(payload).encode("utf-8")

    if debug:
        print(f"入口页: {final_entry_url}")
        print(f"登录页: {final_login_url}")
        print(f"提交到: {method} {action_url}")
        print("字段: " + ", ".join(sorted(payload.keys())))
        if method == "POST" and encoded is not None:
            print(f"请求体: {encoded.decode('utf-8')}")
        save_debug("zjlib_login_page_debug.html", login_html)
        if show_inputs:
            print("表单控件:")
            for line in describe_inputs(form):
                print("  " + line)

    if method == "GET":
        sep = "&" if "?" in action_url else "?"
        action_url = action_url + sep + encoded.decode("utf-8")
        encoded = None

    try:
        status, final_url, result, resp_headers = request_text(
            opener,
            action_url,
            method=method,
            data=encoded,
            referer=final_login_url,
            timeout=timeout,
            return_headers=True,
            extra_headers={"Origin": "https://2.2.1.1:8443"} if method == "POST" else None,
        )
    except urllib.error.URLError as exc:
        if debug:
            save_debug("zjlib_login_page_debug.html", login_html)
        print(f"登录请求被网关断开或拒绝: {exc}", file=sys.stderr)
        if debug:
            print("已保存 zjlib_login_page_debug.html", file=sys.stderr)
        return 2
    if debug:
        save_debug("zjlib_login_result_debug.html", result)
        print(f"响应状态: HTTP {status}")
        print("响应头:")
        for k, v in resp_headers.items():
            print(f"  {k}: {v}")
        body_preview = re.sub(r"\s+", " ", result).strip()[:500]
        print(f"响应体摘要: {body_preview}")

    if status not in (200, 302):
        print(f"登录请求异常: HTTP {status} {final_url}", file=sys.stderr)
        brief = re.sub(r"\s+", " ", result).strip()[:300]
        if brief:
            print(f"返回摘要: {brief}", file=sys.stderr)
        if debug:
            print("已保存 zjlib_login_page_debug.html 和 zjlib_login_result_debug.html", file=sys.stderr)
        return 2

    if looks_successful(result, final_url):
        print(f"登录请求已提交，结果看起来成功: {final_url}")
        return 0

    brief = re.sub(r"\s+", " ", result).strip()[:300]
    print(f"登录请求已提交，但未识别为成功。返回摘要: {brief}", file=sys.stderr)
    if debug:
        print("已保存 zjlib_login_result_debug.html", file=sys.stderr)
    return 1


def status(entry_url: str, timeout: float) -> int:
    clear_proxy_environment()
    opener, _ = build_opener()
    try:
        code, final_url, body = request_text(opener, entry_url, timeout=timeout)
    except urllib.error.URLError as exc:
        print(f"无法直连认证入口: {exc}", file=sys.stderr)
        return 2
    login_page = find_login_page(final_url, body)
    print(f"入口页可访问: HTTP {code} {final_url}")
    print(f"识别到登录页: {login_page}")
    return 0 if code == 200 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Login to Zhejiang Library Wi-Fi captive portal.")
    parser.add_argument("--url", default=os.environ.get("ZJLIB_WIFI_URL", DEFAULT_URL))
    parser.add_argument("--username", default=os.environ.get("ZJLIB_WIFI_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("ZJLIB_WIFI_PASSWORD"))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("ZJLIB_WIFI_TIMEOUT", "10")))
    parser.add_argument("--status", action="store_true", help="only check the portal entry and iframe URL")
    parser.add_argument("--debug", action="store_true", help="print detected form fields and save result HTML")
    parser.add_argument("--show-inputs", action="store_true", help="with --debug, print parsed form controls")
    return parser.parse_args()


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parse_args()

    if args.status:
        return status(args.url, args.timeout)

    if not args.username or not args.password:
        print(
            "缺少账号或密码。请设置 .env 中的 ZJLIB_WIFI_USERNAME / ZJLIB_WIFI_PASSWORD，"
            "或用 --username / --password 传入。",
            file=sys.stderr,
        )
        return 2

    return login(args.url, args.username, args.password, args.timeout, args.debug, args.show_inputs)


if __name__ == "__main__":
    raise SystemExit(main())
