#!/usr/bin/env python3
"""浙江理工大学校园网 ePortal 认证助手。

脚本按门户页面实际执行的调用顺序完成：

1. GET 登录入口，保留当前 JSESSIONID；
2. POST ``pageInfo``，读取密码加密开关和动态 RSA 公钥；
3. POST ``getServices``，读取默认服务；
4. POST ``login``，提交与页面相同的字段。

账号、密码和 Cookie 都只存在于本次进程内，不写入磁盘，也不会在 debug
输出中打印。门户地址中的 wlanuserip/mac/url 等参数通常是当前会话的，
不要把某一次抓包的参数长期硬编码。
"""

from __future__ import annotations

import argparse
import getpass
import html
import http.client
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import (
    HTTPHandler,
    HTTPSHandler,
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)


DEFAULT_DISCOVERY_URL = "http://connectivitycheck.gstatic.com/generate_204"
DEFAULT_TIMEOUT = 10.0
PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
JS_URI_SAFE = "-_.!~*'()"


class PortalError(RuntimeError):
    """门户响应或本地配置不符合预期。"""


class AlreadyOnline(PortalError):
    """连通性检测返回 204，说明当前网络已经可用。"""


@dataclass
class Response:
    status: int
    url: str
    text: str
    headers: Any


@dataclass
class PortalConfig:
    entry_url: str
    query_string: str
    endpoint: str
    password_encrypt: bool
    public_exponent: str
    public_modulus: str
    service: str
    service_options: list[str]
    service_required: bool
    valid_code_url: str
    prefix_value: str


def load_env_file(path: Path) -> None:
    """加载一个很小的 KEY=VALUE 配置文件，不覆盖已有环境变量。"""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def clear_proxy_environment() -> None:
    """只在当前进程内绕过代理，避免代理改写内网认证请求。"""

    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "192.168.102.130,localhost,127.0.0.1"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def decode_body(raw: bytes, headers: Any) -> str:
    charset = None
    try:
        charset = headers.get_content_charset()
    except (AttributeError, TypeError):
        pass

    candidates = [charset, "utf-8", "gbk", "latin-1"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class PortalClient:
    def __init__(self, timeout: float, debug: bool = False) -> None:
        clear_proxy_environment()
        self.timeout = timeout
        self.debug = debug
        cookie_jar = CookieJar()
        tls_context = ssl.create_default_context()
        self.opener = build_opener(
            ProxyHandler({}),
            HTTPCookieProcessor(cookie_jar),
            HTTPHandler(),
            HTTPSHandler(context=tls_context),
        )

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: str | bytes | None = None,
        referer: str | None = None,
    ) -> Response:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0 zstu-wifi-login/1.0",
        }
        if referer:
            headers["Referer"] = referer
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            parts = urlsplit(url)
            if parts.scheme and parts.netloc:
                headers["Origin"] = f"{parts.scheme}://{parts.netloc}"

        if isinstance(data, str):
            data = data.encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)

        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                return Response(
                    status=response.status,
                    url=response.geturl(),
                    text=decode_body(raw, response.headers),
                    headers=response.headers,
                )
        except HTTPError as exc:
            try:
                raw = exc.read()
                text = decode_body(raw, exc.headers)
            except Exception:
                text = ""
            return Response(
                status=exc.code,
                url=exc.geturl() or url,
                text=text,
                headers=exc.headers,
            )
        except (URLError, TimeoutError, http.client.RemoteDisconnected) as exc:
            raise PortalError(f"无法访问认证门户: {exc}") from exc


def js_encode_uri_component(value: str) -> str:
    """等价于 JavaScript 的 encodeURIComponent。"""

    return quote(str(value), safe=JS_URI_SAFE, encoding="utf-8", errors="strict")


def js_double_encode(value: str) -> str:
    return js_encode_uri_component(js_encode_uri_component(value))


def raw_query_string(url: str) -> str:
    query = urlsplit(url).query
    if not query:
        raise PortalError(
            "认证入口缺少查询参数。请把当前未认证时浏览器地址栏中的完整 URL "
            "传给 --url；wlanuserip、mac、url 等参数通常会变化。"
        )
    return query


def raw_query_param(query: str, name: str) -> str:
    for item in query.split("&"):
        key, separator, value = item.partition("=")
        if separator and unquote(key) == name:
            return value
    return ""


def endpoint_for(entry_url: str, method: str) -> str:
    interface = urljoin(entry_url, "./InterFace.do")
    return f"{interface}?method={method}"


def post_form(client: PortalClient, url: str, fields: dict[str, str], referer: str) -> Response:
    # AuthInterFace.js 手工拼接已经编码过的字段；不能再用 urlencode，
    # 否则字段里的 % 会被再次编码，和浏览器请求不一致。
    body = "&".join(f"{js_encode_uri_component(key)}={value}" for key, value in fields.items())
    return client.request(url, method="POST", data=body, referer=referer)


def parse_json(response: Response, label: str) -> dict[str, Any]:
    if response.status != 200:
        raise PortalError(f"{label} 请求失败：HTTP {response.status}")
    try:
        value = json.loads(response.text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise PortalError(f"{label} 返回的不是 JSON（HTTP {response.status}）") from exc
    if not isinstance(value, dict):
        raise PortalError(f"{label} 返回的 JSON 不是对象")
    return value


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def html_attribute(fragment: str, attribute: str) -> str:
    pattern = rf"\b{re.escape(attribute)}\s*=\s*(['\"])(.*?)\1"
    match = re.search(pattern, fragment or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html.unescape(match.group(2))


def service_names(service_json: Any) -> list[str]:
    if isinstance(service_json, str):
        try:
            service_json = json.loads(service_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(service_json, list):
        return []
    names: list[str] = []
    for item in service_json:
        if isinstance(item, dict):
            name = item.get("serviceName")
            if name is not None and str(name) not in names:
                names.append(str(name))
    return names


def bootstrap(client: PortalClient, entry_url: str) -> PortalConfig:
    entry = client.request(entry_url)
    if entry.status == 204:
        raise AlreadyOnline(
            "连通性检测返回 HTTP 204，当前网络已经可以上网，无需重复认证。"
        )
    if entry.status != 200:
        raise PortalError(f"认证入口返回 HTTP {entry.status}：{entry.url}")
    if "设备未注册" in entry.text:
        raise PortalError("门户提示设备未注册，请先连接学校 WLAN 或联系网络中心。")

    final_url = entry.url
    query = raw_query_string(final_url)
    page_info_url = endpoint_for(final_url, "pageInfo")
    encoded_query = js_encode_uri_component(query)
    page_info_response = post_form(
        client,
        page_info_url,
        {"queryString": encoded_query},
        referer=final_url,
    )
    page_info = parse_json(page_info_response, "pageInfo")

    get_services_url = endpoint_for(final_url, "getServices")
    get_services_url += "&queryString=" + encoded_query
    service_response = client.request(
        get_services_url,
        method="POST",
        data=b"",
        referer=final_url,
    )
    services = parse_json(service_response, "getServices")

    default_service_html = str(services.get("defaultService") or "")
    default_service = html_attribute(default_service_html, "value")
    options = service_names(services.get("serviceJson"))
    service_required = bool_value(services.get("isService"))
    if not default_service and len(options) == 1 and service_required:
        default_service = options[0]

    exponent = str(page_info.get("publicKeyExponent") or "")
    modulus = str(page_info.get("publicKeyModulus") or "")
    password_encrypt = bool_value(page_info.get("passwordEncrypt"))
    if password_encrypt and (not exponent or not modulus):
        raise PortalError("门户要求 RSA 加密，但 pageInfo 没有下发完整公钥。")

    return PortalConfig(
        entry_url=final_url,
        query_string=query,
        endpoint=endpoint_for(final_url, "login"),
        password_encrypt=password_encrypt,
        public_exponent=exponent,
        public_modulus=modulus,
        service=default_service,
        service_options=options,
        service_required=service_required,
        valid_code_url=str(page_info.get("validCodeUrl") or ""),
        prefix_value=(str(page_info.get("prefixValue") or "") if bool_value(page_info.get("prefixName")) else ""),
    )


def js_code_units(value: str) -> list[int]:
    raw = value.encode("utf-16-le", errors="surrogatepass")
    return [raw[index] | (raw[index + 1] << 8) for index in range(0, len(raw), 2)]


def js_reverse(value: str) -> list[int]:
    # JavaScript split("").reverse() reverses UTF-16 code units, not Python
    # Unicode code points. ASCII passwords (the normal case) are identical.
    return list(reversed(js_code_units(value)))


def rsa_encrypt_js(value: str, exponent_hex: str, modulus_hex: str) -> str:
    """复现 security.js 中 RSAUtils.encryptedString 的无填充 RSA。"""

    exponent_hex = exponent_hex.strip().lower().removeprefix("0x")
    modulus_hex = modulus_hex.strip().lower().removeprefix("0x")
    try:
        exponent = int(exponent_hex, 16)
        modulus = int(modulus_hex, 16)
    except ValueError as exc:
        raise PortalError("门户下发的 RSA 公钥格式无效。") from exc
    if exponent <= 0 or modulus <= 0:
        raise PortalError("门户下发的 RSA 公钥为空。")

    # security.js 使用 16-bit digits，chunkSize = 2 * (highIndex(modulus))。
    digit_count = (modulus.bit_length() + 15) // 16
    chunk_size = 2 * (digit_count - 1)
    if chunk_size <= 0:
        raise PortalError("RSA 模数太短，无法建立加密块。")

    units = js_reverse(value)
    while len(units) % chunk_size:
        units.append(0)

    blocks: list[str] = []
    for start in range(0, len(units), chunk_size):
        block = 0
        chunk = units[start : start + chunk_size]
        for index in range(0, chunk_size, 2):
            low = chunk[index]
            high = chunk[index + 1]
            digit = low + (high << 8)
            block |= digit << (16 * (index // 2))
        encrypted = pow(block, exponent, modulus)
        encoded = format(encrypted, "x")
        encoded = encoded.zfill((len(encoded) + 3) // 4 * 4)
        blocks.append(encoded)
    return " ".join(blocks)


def build_login_payload(
    config: PortalConfig,
    username: str,
    password: str,
    *,
    service: str,
    operator_user_id: str = "",
    operator_password: str = "",
    validcode: str = "",
) -> dict[str, str]:
    mac = raw_query_param(config.query_string, "mac") or "111111111"
    portal_password = password
    portal_operator_password = operator_password
    # 页面在 passwordEncrypt=false 时仍会把长度超过 150 的内容视为已保存
    # 密文，并把最终字段改成 true；保留这个兼容分支，避免误用旧 Cookie
    # 密文时与浏览器行为不一致。
    effective_encrypt = config.password_encrypt or len(js_code_units(password)) > 150

    if effective_encrypt:
        # 页面代码以 UTF-16 长度判断是否已经是保存的密文。
        if config.password_encrypt and len(js_code_units(password)) < 150:
            portal_password = rsa_encrypt_js(
                password + ">" + mac,
                config.public_exponent,
                config.public_modulus,
            )
        if operator_password:
            portal_operator_password = rsa_encrypt_js(
                operator_password,
                config.public_exponent,
                config.public_modulus,
            )

    return {
        "userId": js_double_encode(username),
        "password": js_double_encode(portal_password),
        "service": js_double_encode(service),
        "queryString": js_double_encode(config.query_string),
        "operatorPwd": js_double_encode(portal_operator_password),
        "operatorUserId": js_double_encode(operator_user_id),
        # doauthen() 没有对验证码额外 encodeURIComponent；保持页面行为。
        "validcode": validcode,
        "passwordEncrypt": js_double_encode("true" if effective_encrypt else "false"),
    }


def query_keys(query: str) -> list[str]:
    keys: list[str] = []
    for item in query.split("&"):
        key = unquote(item.partition("=")[0])
        if key and key not in keys:
            keys.append(key)
    return keys


def print_bootstrap(config: PortalConfig) -> None:
    parts = urlsplit(config.entry_url)
    safe_entry = f"{parts.scheme}://{parts.netloc}{parts.path}"
    print(f"认证入口：{safe_entry}")
    print("查询参数：" + ", ".join(query_keys(config.query_string)))
    print("密码加密：" + ("RSA（动态公钥）" if config.password_encrypt else "关闭"))
    print("服务：" + (config.service if config.service else "空/默认服务"))
    if config.service_options:
        print("可选服务：" + ", ".join(config.service_options))
    if config.valid_code_url:
        print("验证码：需要；请用 --validcode 传入")
    else:
        print("验证码：当前不需要")


def safe_message(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:300]


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    username = args.username or os.environ.get("ZSTU_WIFI_USERNAME", "")
    password = args.password or os.environ.get("ZSTU_WIFI_PASSWORD", "")
    if not username:
        if not sys.stdin.isatty():
            raise PortalError("缺少账号：请设置 ZSTU_WIFI_USERNAME 或传入 --username。")
        username = input("校园网账号：").strip()
    if not password:
        if not sys.stdin.isatty():
            raise PortalError("缺少密码：请设置 ZSTU_WIFI_PASSWORD，或在交互终端运行脚本。")
        password = getpass.getpass("校园网密码：")
    if not username or not password:
        raise PortalError("账号和密码都不能为空。")
    return username, password


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="浙江理工大学校园网 ePortal 认证")
    parser.add_argument(
        "--url",
        help="当前未认证时的完整 ePortal URL（建议包含 wlanuserip/mac/url 等查询参数）",
    )
    parser.add_argument(
        "--discover-url",
        help=f"未提供 --url 时用于触发门户跳转的 URL（默认 {DEFAULT_DISCOVERY_URL}）",
    )
    parser.add_argument("--username", help="校园网账号；更建议放在 .env.zstu 中")
    parser.add_argument("--password", help="校园网密码；命令行参数可能进入 shell 历史")
    parser.add_argument("--service", help="服务值；不传则读取门户动态默认值")
    parser.add_argument("--operator-user-id", default="", help="可选：运营商账号")
    parser.add_argument("--operator-password", default="", help="可选：运营商密码")
    parser.add_argument("--validcode", default="", help="可选：验证码")
    parser.add_argument("--env-file", default=".env.zstu", help="配置文件（默认 .env.zstu）")
    parser.add_argument("--timeout", type=float, default=None, help="请求超时秒数，默认 10")
    parser.add_argument("--status", action="store_true", help="只读取门户配置，不提交登录")
    parser.add_argument("--dry-run", action="store_true", help="构造登录字段但不提交登录")
    parser.add_argument("--debug", action="store_true", help="输出不含账号、密码和 Cookie 的调试信息")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(Path(args.env_file))

    timeout = args.timeout
    if timeout is None:
        timeout = float(os.environ.get("ZSTU_WIFI_TIMEOUT", str(DEFAULT_TIMEOUT)))
    entry_url = args.url or os.environ.get("ZSTU_WIFI_URL", "")
    if not entry_url:
        entry_url = args.discover_url or os.environ.get(
            "ZSTU_WIFI_DISCOVERY_URL", DEFAULT_DISCOVERY_URL
        )

    client = PortalClient(timeout=timeout, debug=args.debug)
    try:
        config = bootstrap(client, entry_url)
        print_bootstrap(config)

        if args.status:
            print("仅检查模式：未提交认证请求。")
            return 0

        username = password = ""
        if not args.dry_run:
            username, password = resolve_credentials(args)
        else:
            username = args.username or os.environ.get("ZSTU_WIFI_USERNAME", "<username>")
            password = args.password or os.environ.get("ZSTU_WIFI_PASSWORD", "<password>")

        service = (
            args.service
            if args.service is not None
            else os.environ.get("ZSTU_WIFI_SERVICE", config.service)
        )
        if config.service_required and not service:
            options = ", ".join(config.service_options) if config.service_options else "门户下拉框中的值"
            raise PortalError(f"门户要求选择服务，请用 --service 传入；可选值：{options}")

        payload = build_login_payload(
            config,
            username,
            password,
            service=service,
            operator_user_id=args.operator_user_id,
            operator_password=args.operator_password,
            validcode=args.validcode,
        )

        if args.dry_run:
            print("试运行：已构造登录字段，但没有提交账号密码。")
            print("字段：" + ", ".join(payload.keys()))
            return 0

        response = post_form(client, config.endpoint, payload, referer=config.entry_url)
        result = parse_json(response, "login")
        if str(result.get("result", "")).lower() != "success":
            message = safe_message(result.get("message")) or "门户未返回成功结果"
            print(f"认证失败：{message}", file=sys.stderr)
            if result.get("validCodeUrl"):
                print("门户要求验证码，请重新运行并传入 --validcode。", file=sys.stderr)
            return 1

        interval = result.get("keepaliveInterval")
        if interval not in (None, "", 0, "0"):
            print(f"认证成功（门户保活间隔：{interval} 分钟）。")
        else:
            print("认证成功。")
        if args.debug:
            print("登录接口返回成功；userIndex 未输出，避免泄露会话标识。")
        return 0
    except AlreadyOnline as exc:
        print(str(exc))
        return 0
    except PortalError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：配置中的超时值无效：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
