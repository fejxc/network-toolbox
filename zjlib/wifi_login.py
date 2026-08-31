#!/usr/bin/env python3
"""浙江图书馆 Wi-Fi 认证门户登录助手。

脚本模拟浏览器完成整个认证流程：

1. 清除代理环境变量，直连认证入口（门户在内网 2.2.1.1，走代理/TUN
   会连不上或拿到错误页面）；
2. GET 入口页，从其中的 iframe 定位真正的登录页 /cn/login.html；
3. 解析登录页的 HTML form，识别用户名/密码等字段；
4. 按门户 Mac 端 XHR 的字段组合提交登录（见 build_zjlib_ajax_payload）；
5. 根据响应内容和最终 URL 判断是否登录成功。

账号、密码来自 .env 或命令行参数，只存在于本次进程内，不写入磁盘；
debug 保存的 HTML 也不包含密码。
"""

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


# 认证门户入口。门户只在「未认证」状态下伺服该页，已认证后再访问会返回 404。
DEFAULT_URL = "https://2.2.1.1:8443/cn/index.html"
# 这些代理变量会把对内网门户的请求劫持到外网，登录前需要全部清掉。
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
    """登录页里解析出的一个 <form>。"""

    action: str = ""
    method: str = "GET"
    inputs: list[dict[str, str]] = field(default_factory=list)


class PortalParser(html.parser.HTMLParser):
    """提取 iframe 与 form 的极简 HTML 解析器（无第三方依赖）。"""

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
    """读取简单键值形式的 .env 文件；不覆盖已存在的环境变量。"""
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
    """清空代理变量并设置 NO_PROXY。

    门户在内网 2.2.1.1，系统代理或 TUN 会把请求带去外网，导致连不上
    或拿到错误页面；登录和状态检查都必须直连。
    """
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "2.2.1.1,2.2.1.0/24,localhost,127.0.0.1"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def build_opener() -> tuple[urllib.request.OpenerDirector, CookieJar]:
    """构造跳过证书校验、强制直连、自动管理 Cookie 的 opener。"""
    # 门户使用自签名证书，标准证书校验必然失败，只能跳过。
    context = ssl._create_unverified_context()
    # ProxyHandler({}) 显式禁用代理，即使环境残留代理设置也不生效。
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
    """通过 ``opener`` 请求 ``url``。

    返回 ``(status, final_url, body)``；``return_headers=True`` 时额外追加
    第四个元素 ``headers``（dict），供 debug 输出使用。

    非 2xx 响应（例如已认证状态下门户返回 404）不抛异常，而是照常返回，
    让调用方按 status 分支处理。
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
        # 把非 2xx（例如已在线时的 404）当普通响应返回，让调用方按状态码
        # 分支处理，而不是让整个脚本直接崩掉。
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
    """从入口页定位真正的登录页 URL。

    门户把登录页嵌在 iframe 里：优先取 src 含 ``login`` 的 iframe，
    否则取第一个 iframe；都没有则认为入口页本身就是登录页。
    """
    parser = parse_html(entry_html)
    for src in parser.iframes:
        if "login" in src.lower():
            return urllib.parse.urljoin(entry_url, src)
    if parser.iframes:
        return urllib.parse.urljoin(entry_url, parser.iframes[0])
    return entry_url


def pick_form(forms: list[Form]) -> Form | None:
    """在页面的多个 form 中挑出登录表单。

    依据是控件 name/id 是否含 user/account/login/pass 等关键词；
    都不匹配时退回第一个 form。
    """
    for form in forms:
        names = " ".join(
            (item.get("name", "") + " " + item.get("id", "")).lower()
            for item in form.inputs
        )
        if any(word in names for word in ("user", "account", "login", "name", "pass", "pwd")):
            return form
    return forms[0] if forms else None


def find_input(form: Form, patterns: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
    """按关键词在 form 控件中查找字段名（name/id）。

    匹配范围含 name、id、placeholder、type；``exclude`` 用于排除
    RedirectUrl 这类名字里恰好含关键词、但不是账号输入的字段。
    """
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
    """按通用规则构造提交数据：保留隐藏字段默认值，再填入账号密码。

    通用兜底实现；zjlib 门户实际提交走 build_zjlib_ajax_payload。
    """
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

    # 与 /cn/login.html 中 Mac 端提交函数 submitFunc() 发出的 XHR 字段
    # 完全一致。不能只提交用户名密码：门户还依赖 RedirectUrl、anonymous
    # 等隐藏字段，缺失会被当成非法请求。
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
    """根据响应内容和最终 URL 粗略判断登录是否成功。

    成功时门户跳到 auth_success.html 或提示「已登录」；出现 error/失败
    字样则判定失败；两者皆无时，以最终页面是否仍是登录页兜底。
    """
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
    """把调试 HTML 保存到当前目录，返回文件路径。"""
    path = Path.cwd() / name
    path.write_text(text, encoding="utf-8")
    return path


def describe_inputs(form: Form) -> list[str]:
    """生成表单控件清单（密码值打码），配合 --debug --show-inputs 使用。"""
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
    """执行完整登录流程，返回进程退出码。

    0 成功；1 请求已提交但未识别为成功；2 网络/门户异常；4 登录页里
    没有可用表单（门户改版，需按保存的 debug HTML 更新脚本）。
    """
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
    """--status 模式：只探测门户入口可达性，打印识别到的登录页 URL。"""
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
    script_dir = Path(__file__).resolve().parent
    # 优先读脚本同目录的 .env，其次退回仓库根目录的 .env；这样把脚本
    # 挪进 zjlib/ 子目录后，原有配置依然生效。
    load_dotenv(script_dir / ".env")
    load_dotenv(script_dir.parent / ".env")
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
