#!/usr/bin/env python3
"""监控 MDPI SUSY 投稿系统的稿件状态变化。

脚本刻意只从环境变量或本地文件读取已登录的 Cookie：不代填账号密码、
不尝试登录、也不绕过反爬；Cookie 同样不会写进状态基线文件。

工作方式：

1. 定时抓取投稿状态页，用无依赖的表格解析器提取每篇稿件；
2. 与本地状态基线（state.json）对比，找出状态变化的稿件；
3. 有变化时通过 macOS 系统通知和/或钉钉机器人推送；
4. 抓取成功后刷新基线；Cookie 过期、页面改版等异常情况不更新基线，
   避免把坏数据当成新状态。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import html.parser
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_URL = "https://susy.mdpi.com/user/manuscripts/status"
DEFAULT_INTERVAL = 5 * 60
DEFAULT_COOKIE_FILE = Path.home() / ".config" / "mdpi-monitor" / "cookie"
DEFAULT_STATE_FILE = Path.home() / ".cache" / "mdpi-monitor" / "state.json"

# SUSY 页面上出现过的状态短语。匹配时按长度倒序，防止 “pending minor
# revision” 被更短的 “revision” 提前命中。
STATUS_PHRASES = (
    "pending editorial office processing",
    "rejected by editorial office",
    "pending author confirmation",
    "pending author revision",
    "pending major revision",
    "pending minor revision",
    "pending major revisions",
    "pending minor revisions",
    "pending major or minor revisions",
    "pending apc payment",
    "awaiting reviewer assignment",
    "awaiting reviewer",
    "reviewer invited",
    "revision requested",
    "technical check",
    "under review",
    "pending review",
    "pending decision",
    "with editor",
    "editor assigned",
    "in production",
    "resubmitted",
    "paper accepted",
    "english correction done",
    "author proofreading",
    "pending conversion",
    "pdf2xml",
    "submitted",
    "accepted",
    "rejected",
    "withdrawn",
    "published",
    "revision",
)

# 页面出现这些字样说明账号下确实没有投稿，而不是页面解析失败。
NO_MANUSCRIPT_MARKERS = (
    "no manuscripts",
    "no submissions",
    "no records",
    "没有稿件",
    "暂无稿件",
)


@dataclass
class HTMLRow:
    """解析出的一行：所属表格 ID、单元格文本、链接、是否含表头单元格。"""

    table_id: int
    cells: list[str]
    links: list[str]
    has_header_cell: bool


@dataclass
class Manuscript:
    """一篇投稿的快照；key 用于跨轮次对齐同一条记录。"""

    key: str
    manuscript_id: str
    title: str
    status: str
    date: str
    url: str


class TableParser(html.parser.HTMLParser):
    """无第三方依赖的表格行与链接解析器。

    只提取每行单元格文本、链接和是否含表头单元格；script/style/noscript
    里的文本用深度计数跳过，不算单元格内容。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[HTMLRow] = []
        self._table_stack: list[int] = []
        self._next_table_id = 0
        self._row: HTMLRow | None = None
        self._cell_text: list[str] | None = None
        self._cell_links: list[str] = []
        self._cell_is_header = False
        self._ignored_tag_depth = 0

    @property
    def _table_id(self) -> int:
        return self._table_stack[-1] if self._table_stack else 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "table":
            self._next_table_id += 1
            self._table_stack.append(self._next_table_id)
            return
        if tag in {"script", "style", "noscript"}:
            self._ignored_tag_depth += 1
            return
        if self._ignored_tag_depth:
            return
        if tag == "tr":
            if self._row is not None:
                self._finish_row()
            self._row = HTMLRow(self._table_id, [], [], False)
            return
        if tag in {"td", "th"} and self._row is not None:
            if self._cell_text is not None:
                self._finish_cell()
            self._cell_text = []
            self._cell_links = []
            self._cell_is_header = tag == "th"
            return
        if tag == "a" and self._cell_text is not None:
            href = attr.get("href", "").strip()
            if href:
                self._cell_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            if self._ignored_tag_depth:
                self._ignored_tag_depth -= 1
            return
        if self._ignored_tag_depth:
            return
        if tag in {"td", "th"} and self._cell_text is not None:
            self._finish_cell()
        elif tag == "tr" and self._row is not None:
            self._finish_row()
        elif tag == "table" and self._table_stack:
            if self._row is not None:
                self._finish_row()
            self._table_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None and not self._ignored_tag_depth:
            self._cell_text.append(data)

    def _finish_cell(self) -> None:
        if self._row is None or self._cell_text is None:
            return
        text = clean_text("".join(self._cell_text))
        self._row.cells.append(text)
        self._row.links.extend(self._cell_links)
        self._row.has_header_cell = self._row.has_header_cell or self._cell_is_header
        self._cell_text = None
        self._cell_links = []
        self._cell_is_header = False

    def _finish_row(self) -> None:
        if self._row is None:
            return
        if self._cell_text is not None:
            self._finish_cell()
        if any(self._row.cells):
            self.rows.append(self._row)
        self._row = None


def clean_text(value: str) -> str:
    """反转义 HTML 实体，并把连续空白压成单个空格。"""
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalized(value: str) -> str:
    """clean_text 后再 casefold，作为状态比较用的规范形式。"""
    return clean_text(value).casefold()


def parse_cookie_value(raw: str) -> str:
    """接受 Cookie 请求头、Netscape cookie 文件，或整条复制来的 curl 命令。"""
    text = raw.strip()
    if not text:
        return ""

    # 如果用户保存的是整条 curl 命令，则提取其中的 -b/--cookie 参数，
    # 而不是把整段 shell 命令当请求头发出去。
    match = re.search(r"(?:^|\s)(?:-b|--cookie)\s+(['\"])(.*?)\1", text, re.DOTALL)
    if match:
        text = match.group(2).strip()

    text = re.sub(r"(?im)^\s*cookie\s*:\s*", "", text).strip()
    text = text.strip("'\"")

    # 粘贴进来的内容可能带 Markdown 转义。这些反斜杠是排版产物，
    # 不是浏览器 Cookie 的一部分。
    for escaped, literal in ((r"\_", "_"), (r"\~", "~"), (r"\.", ".")):
        text = text.replace(escaped, literal)

    # 同时接受 Netscape 格式的 cookie 文件（即 curl -b 能直接使用的那种）。
    netscape_pairs: list[str] = []
    non_comment_lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if non_comment_lines and all("\t" in line for line in non_comment_lines):
        for line in non_comment_lines:
            fields = line.split("\t")
            if len(fields) >= 7:
                netscape_pairs.append(f"{fields[5]}={fields[6]}")
        if netscape_pairs:
            return "; ".join(netscape_pairs)

    pairs: list[str] = []
    for part in text.replace("\n", ";").split(";"):
        part = part.strip().strip("'\"")
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip().lstrip("\\")
        value = value.strip()
        if name:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def read_cookie(cookie_file: Path | None) -> str:
    """按「Cookie 文件 → 环境变量 MDPI_COOKIE」的顺序读取，都没有则报错。"""
    if cookie_file is not None and cookie_file.exists():
        warn_if_insecure(cookie_file, "Cookie 文件")
        cookie = parse_cookie_value(cookie_file.read_text(encoding="utf-8"))
        if cookie:
            return cookie

    env_cookie = os.environ.get("MDPI_COOKIE", "")
    cookie = parse_cookie_value(env_cookie)
    if cookie:
        return cookie

    locations = []
    if cookie_file is not None:
        locations.append(str(cookie_file))
    locations.append("环境变量 MDPI_COOKIE")
    raise RuntimeError("未找到 MDPI Cookie。请写入 " + " 或 ".join(locations) + "。")


def warn_if_insecure(path: Path, label: str) -> None:
    """文件权限对其他用户开放时给出警告，避免 Cookie 被同机其他用户读取。"""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & 0o077:
        print(f"警告：{label}权限为 {mode:04o}，建议执行 chmod 600 {path}", file=sys.stderr)


def fetch_page(url: str, cookie: str, timeout: float) -> tuple[int, str, str]:
    """带上浏览器请求头抓取状态页，返回 (状态码, 最终URL, 页面文本)。"""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
        "Cache-Control": "max-age=0",
        "Cookie": cookie,
        "Referer": url,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, response.geturl(), body.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        charset = exc.headers.get_content_charset() or "utf-8"
        return exc.code, exc.geturl(), body.decode(charset, errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"访问 MDPI 失败：{exc.reason}") from exc


def _header_score(row: HTMLRow) -> int:
    """统计一行中表头关键词的命中数，用于识别表头行。"""
    text = " ".join(row.cells).casefold()
    terms = ("manuscript", "submission", "status", "title", "date", "updated")
    return sum(term in text for term in terms)


def find_headers(rows: Iterable[HTMLRow]) -> dict[int, list[str]]:
    """为每张表挑出得分最高的表头行，返回 {表格ID: 列名列表}。"""
    headers: dict[int, list[str]] = {}
    best_scores: dict[int, int] = {}
    for row in rows:
        score = _header_score(row)
        if (row.has_header_cell or score >= 2) and score > best_scores.get(row.table_id, 0):
            headers[row.table_id] = [normalized(cell) for cell in row.cells]
            best_scores[row.table_id] = score
    return headers


def header_index(headers: list[str], *terms: str) -> int | None:
    """在表头列名里找第一个含任一关键词的列下标。"""
    for index, value in enumerate(headers):
        if any(term in value for term in terms):
            return index
    return None


def status_from_text(text: str) -> str:
    """从单元格文本中识别已知状态短语；未命中返回空串。"""
    lower = normalized(text)
    for phrase in sorted(STATUS_PHRASES, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", lower):
            return clean_text(text)
    return ""


def manuscript_id_from_text(text: str) -> str:
    # MDPI 稿号形如 ``applsci-1234567``；第二个正则覆盖页面上以
    # “Manuscript ID:” 等标签形式展示的编号。
    match = re.search(r"\b[A-Za-z][A-Za-z0-9_.]*-\d{4,}\b", text)
    if match:
        return match.group(0)
    match = re.search(
        r"(?:manuscript|submission|article|ms)\s*(?:id|no\.?|number|#)?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{4,})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def choose_title(cells: list[str], manuscript_id: str, status: str) -> str:
    id_lower = manuscript_id.casefold()
    status_lower = status.casefold()
    candidates = [
        cell
        for cell in cells
        if cell
        and cell.casefold() != id_lower
        and cell.casefold() != status_lower
        and not re.fullmatch(r"\d{1,6}", cell)
    ]
    if not candidates:
        return "未显示标题"
    # 标题通常是最长的非状态单元格，日期和操作链接都很短。保留原文，
    # 但为推送内容截断长度。
    return max(candidates, key=len)[:300]


def extract_manuscripts(page_url: str, page_html: str) -> list[Manuscript]:
    """从页面 HTML 提取全部投稿记录；同稿号只保留一条。"""
    parser = TableParser()
    parser.feed(page_html)
    headers_by_table = find_headers(parser.rows)
    manuscripts: dict[str, Manuscript] = {}

    for row in parser.rows:
        if len(row.cells) < 2:
            continue
        # 避免把 ``Manuscript | Status`` 这类表头行解析成状态为
        # “Status” 的假稿件。
        if row.has_header_cell or _header_score(row) >= 2:
            continue
        headers = headers_by_table.get(row.table_id, [])
        row_text = " | ".join(cell for cell in row.cells if cell)
        manuscript_id = manuscript_id_from_text(row_text)

        status = ""
        status_index = header_index(headers, "status", "state", "decision")
        if status_index is not None and status_index < len(row.cells):
            status = status_from_text(row.cells[status_index]) or row.cells[status_index]
        if not status:
            for cell in row.cells:
                status = status_from_text(cell)
                if status:
                    break

        if not manuscript_id and not status:
            continue

        title = ""
        title_index = header_index(headers, "article title", "manuscript title", "title")
        if title_index is not None and title_index < len(row.cells):
            title = row.cells[title_index]
        if not title:
            title = choose_title(row.cells, manuscript_id, status)

        date = ""
        date_index = header_index(headers, "last update", "updated", "submitted", "date")
        if date_index is not None and date_index < len(row.cells):
            date = row.cells[date_index]

        link = ""
        for candidate in row.links:
            candidate_lower = candidate.casefold()
            if manuscript_id and manuscript_id.casefold() in candidate_lower:
                link = candidate
                break
            if "manuscript" in candidate_lower or "submission" in candidate_lower:
                link = candidate
        absolute_link = urllib.parse.urljoin(page_url, link) if link else ""

        if manuscript_id:
            key = manuscript_id.casefold()
        else:
            key_source = "|".join((title, status, date, absolute_link))
            key = "row-" + hashlib.sha1(key_source.encode("utf-8")).hexdigest()[:16]

        manuscripts[key] = Manuscript(
            key=key,
            manuscript_id=manuscript_id or key,
            title=title,
            status=status or "未知状态",
            date=date,
            url=absolute_link,
        )

    return list(manuscripts.values())


def looks_like_auth_error(status_code: int, final_url: str, page_html: str, records: list[Manuscript]) -> bool:
    """判断是否 Cookie 失效：401/403，或无记录且页面/URL 像登录页。"""
    if status_code in {401, 403}:
        return True
    if records:
        return False
    lower_url = final_url.casefold()
    lower_html = page_html[:200_000].casefold()
    auth_markers = ("/login", "sign in", "please log in", "login to", "susy login")
    return any(marker in lower_url or marker in lower_html for marker in auth_markers)


def load_state(path: Path) -> dict[str, dict[str, str]]:
    """读取本地状态基线；文件缺失视为首次运行，格式损坏则报错而非清零。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"状态文件不可读取：{path}（{exc}）") from exc
    manuscripts = data.get("manuscripts", {})
    if not isinstance(manuscripts, dict):
        raise RuntimeError(f"状态文件格式错误：{path}")
    return manuscripts


def save_state(path: Path, records: list[Manuscript]) -> None:
    """写入新的状态基线，并把文件权限收紧到 600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "manuscripts": {record.key: asdict(record) for record in records},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def short_title(title: str, limit: int = 70) -> str:
    """把标题截断到 limit 字符以内，用于通知与终端输出。"""
    title = clean_text(title)
    return title if len(title) <= limit else title[: limit - 1] + "…"


def print_records(records: list[Manuscript]) -> None:
    """向终端打印本次抓到的全部稿件概要。"""
    print(f"发现 {len(records)} 篇投稿：")
    for record in sorted(records, key=lambda item: item.manuscript_id.casefold()):
        date = f"；日期：{record.date}" if record.date else ""
        print(f"  {record.manuscript_id} | {record.status} | {short_title(record.title)}{date}")


def detect_changes(
    old: dict[str, dict[str, str]], records: list[Manuscript]
) -> tuple[list[tuple[Manuscript, str]], list[Manuscript]]:
    """对比新旧基线，返回 (状态变化列表, 本次消失的记录列表)。"""
    changes: list[tuple[Manuscript, str]] = []
    current_keys = set()
    for record in records:
        current_keys.add(record.key)
        previous = old.get(record.key)
        if previous is None:
            # 只关注状态流转。新出现的稿件仅记入下一次基线，不触发推送，
            # 避免页面改版或筛选变化造成推送轰炸。
            continue
        old_status = normalized(str(previous.get("status", "")))
        new_status = normalized(record.status)
        if old_status != new_status:
            changes.append((record, f"状态：{previous.get('status', '未知状态')} → {record.status}"))

    removed = [
        Manuscript(
            key=key,
            manuscript_id=str(value.get("manuscript_id", key)),
            title=str(value.get("title", "")),
            status=str(value.get("status", "")),
            date=str(value.get("date", "")),
            url=str(value.get("url", "")),
        )
        for key, value in old.items()
        if key not in current_keys and isinstance(value, dict)
    ]
    return changes, removed


def apple_script_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send_dingtalk(webhook: str, title: str, message: str, keyword: str) -> None:
    """通过钉钉自定义机器人 Webhook 发送 markdown 消息。"""
    if not webhook:
        raise RuntimeError("未配置钉钉 Webhook；请设置 MDPI_DINGTALK_WEBHOOK。")
    parsed = urllib.parse.urlparse(webhook)
    if parsed.scheme != "https" or parsed.netloc != "oapi.dingtalk.com":
        raise RuntimeError("钉钉 Webhook 地址必须是 https://oapi.dingtalk.com/...。")

    safe_keyword = clean_text(keyword) or "MDPI"
    markdown = f"### {safe_keyword}｜{title}\n\n{message}\n\n来源：MDPI SUSY"
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": f"{safe_keyword}｜{title}", "text": markdown},
    }
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
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


def notify(
    title: str,
    message: str,
    mode: str,
    *,
    dingtalk_webhook: str = "",
    dingtalk_keyword: str = "MDPI",
) -> None:
    """按 mode 派发通知：macos 系统通知、dingtalk 机器人，或 both/none。"""
    if mode == "none":
        return
    if mode in {"macos", "both"} and platform.system() == "Darwin":
        script = f"display notification {apple_script_quote(message)} with title {apple_script_quote(title)}"
        try:
            subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    elif mode in {"macos", "both"}:
        print(f"通知：{title}：{message}", file=sys.stderr)

    if mode in {"dingtalk", "both"}:
        send_dingtalk(dingtalk_webhook, title, message, dingtalk_keyword)


def check_once(args: argparse.Namespace, cookie: str) -> int:
    """抓取一次状态页，对比基线并在有变化时推送；成功后刷新基线。"""
    status_code, final_url, page_html = fetch_page(args.url, cookie, args.timeout)
    records = extract_manuscripts(final_url, page_html)
    if looks_like_auth_error(status_code, final_url, page_html, records):
        raise RuntimeError(f"MDPI 返回 HTTP {status_code} 或登录页，Cookie 可能已过期，请重新导出。")
    if status_code < 200 or status_code >= 300:
        raise RuntimeError(f"MDPI 返回 HTTP {status_code}，暂不更新状态文件。")
    if not records and not any(marker in normalized(page_html) for marker in NO_MANUSCRIPT_MARKERS):
        raise RuntimeError("页面抓取成功，但没有识别到投稿记录；可能是页面结构变化，暂不更新状态文件。")

    old = load_state(args.state_file)
    changes, removed = detect_changes(old, records)
    print(f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}] 抓取成功。")
    print_records(records)

    if not old:
        print("已建立首次状态基线；后续状态变化会提醒。")
    else:
        for record, reason in changes:
            message = f"{record.manuscript_id}：{reason}；{short_title(record.title)}"
            print(f"变化：{message}")
            notify(
                "MDPI 投稿状态变化",
                message,
                args.notify,
                dingtalk_webhook=args.dingtalk_webhook,
                dingtalk_keyword=args.dingtalk_keyword,
            )
        if removed:
            print("注意：以下记录本次未出现（未自动视为撤稿，可能是页面筛选或结构变化）：")
            for record in removed:
                print(f"  {record.manuscript_id} | {short_title(record.title)}")

    save_state(args.state_file, records)
    return 0


def parse_args() -> argparse.Namespace:
    env_cookie_file = os.environ.get("MDPI_COOKIE_FILE", "")
    env_state_file = os.environ.get("MDPI_STATE_FILE", "")
    env_interval = os.environ.get("MDPI_INTERVAL", str(DEFAULT_INTERVAL))
    env_notify = os.environ.get("MDPI_NOTIFY", "macos")
    env_dingtalk_webhook = os.environ.get("MDPI_DINGTALK_WEBHOOK", "")
    env_dingtalk_keyword = os.environ.get("MDPI_DINGTALK_KEYWORD", "MDPI")

    parser = argparse.ArgumentParser(description="监控 MDPI SUSY 投稿状态变化。")
    parser.add_argument("--url", default=os.environ.get("MDPI_URL", DEFAULT_URL), help="投稿状态页面地址")
    parser.add_argument(
        "--cookie-file",
        type=Path,
        default=Path(env_cookie_file).expanduser() if env_cookie_file else DEFAULT_COOKIE_FILE,
        help="Cookie 文件；也可用 MDPI_COOKIE 环境变量",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(env_state_file).expanduser() if env_state_file else DEFAULT_STATE_FILE,
        help="本地状态基线文件",
    )
    parser.add_argument("--interval", type=int, default=int(env_interval), help="轮询间隔（秒），默认 300")
    parser.add_argument("--timeout", type=float, default=30, help="单次请求超时（秒）")
    parser.add_argument("--once", action="store_true", help="只检查一次，不持续轮询")
    parser.add_argument("--notify", choices=("macos", "dingtalk", "both", "none"), default=env_notify, help="变化提醒方式")
    parser.add_argument(
        "--dingtalk-webhook",
        default=env_dingtalk_webhook,
        help="钉钉机器人 Webhook；也可用 MDPI_DINGTALK_WEBHOOK 环境变量",
    )
    parser.add_argument(
        "--dingtalk-keyword",
        default=env_dingtalk_keyword,
        help="钉钉机器人安全关键词，默认 MDPI",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.interval <= 0:
        print("错误：--interval 必须大于 0。", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("错误：--timeout 必须大于 0。", file=sys.stderr)
        return 2

    try:
        cookie = read_cookie(args.cookie_file)
    except (OSError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    # Ctrl+C 也可能发生在轮询 sleep 期间，统一在外层捕获，避免打印 Traceback。
    try:
        while True:
            try:
                result = check_once(args, cookie)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}] 检查失败：{exc}", file=sys.stderr)
                result = 1

            if args.once:
                return result
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
