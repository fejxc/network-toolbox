#!/usr/bin/env python3
"""四川省公务员报名系统（bm.scs.gov.cn）收藏职位人数监控脚本。

原理：
  前端页面调用 GET /pp/gkweb/web-gk-person-app/api/person/collection/getCollection
  获取"我的收藏职位"列表，其中带 待审查人数(waitnum) 和 审查通过人数(passnum)。
  请求需要登录 Cookie，并附带 HMAC-SHA256 签名头（与前端 core-security.js 一致）。

登录失效时可自动续登（--login 或监控中遇到 401 自动触发）：
  复刻前端登录流程 prevent/token → 图形验证码 → POST idandmobile/login，
  账号密码用与前端 DESUtil.js 完全一致的 strEnc DES 加密（已对拍验证）。
  验证码默认弹图片人工输入；装了 ddddocr 可全自动识别（pip install ddddocr）。

用法：
  python3 monitor.py --once          # 查一次，有变化则通知（适合配 cron）
  python3 monitor.py --watch 300     # 常驻轮询，每 300 秒一次
  python3 monitor.py --login         # 手动重新登录（自动更新 cookie.txt）
  python3 monitor.py --once --quiet  # 只在有变化时输出
"""

import argparse
import base64
import csv
import hashlib
import hmac
import http.cookiejar
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = BASE_DIR / "cookie.txt"
CRED_FILE = BASE_DIR / "credentials.txt"
STATE_FILE = BASE_DIR / "state.json"
HISTORY_CSV = BASE_DIR / "history.csv"
FILTER_FILE = BASE_DIR / "positions.txt"  # 可选：每行一个职位代码，只监控这些职位
GLM_KEY_FILE = BASE_DIR / "glm_key.txt"  # 可选：GLM 视觉 API key，用于高精度验证码识别
GLM_VISION_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
# 免费视觉模型梯队：拥堵(429)时依次切换（均为官方 free 目录模型）
GLM_VISION_MODELS = ["glm-4.6v-flash", "glm-4v-flash", "glm-4.1v-thinking-flash"]
DINGTALK_FILE = BASE_DIR / "dingtalk.txt"  # 钉钉机器人 webhook（含 access_token）
DINGTALK_KEYWORD = "职位人数监控"  # 机器人安全设置里的自定义关键词，推送正文必含
CAPTCHA_PNG = BASE_DIR / "captcha.png"

SITE = "http://bm.scs.gov.cn"
API_PATH = "/pp/gkweb/web-gk-person-app/api/person/collection/getCollection"
API_URL = SITE + API_PATH
LOGIN_PATH = "/pp/gkweb/api/person/idandmobile/login"
LOGIN_URL = SITE + LOGIN_PATH
TOKEN_PATH = "/pp/gkweb/prevent/token"
TOKEN_URL = SITE + TOKEN_PATH
CAPTCHA_PATH = "/pp/gkweb/captcha/img"
CAPTCHA_JSON_URL = SITE + CAPTCHA_PATH
JUDGE_PATH = "/pp/gkweb/web-gk-person-app/api/person/select/judge"
JUDGE_URL = SITE + JUDGE_PATH
LOGIN_REFERER = SITE + "/pp/gkweb/core/web/ui/business/auth/login.html"
CORE_CONSTANT_URL = "http://dl.scs.gov.cn/pp/gkweb/core/web/ui/js/core/core-constant.js"
HB01_ID_FALLBACK = "8a81f6eca0221f1401a0242759fc0000"  # core-constant.js 中的考试批次
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFERER = SITE + "/pp/gkweb/core/web/ui/business/person/person_home.html"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now()}] {msg}", flush=True)


def notify(title, msg, sound=True):
    """macOS 通知中心 + 钉钉机器人双通道推送。"""
    body = msg.replace('"', "'")
    action = f'display notification "{body}" with title "{title}" sound name "Glass"'
    if not sound:
        action = f'display notification "{body}" with title "{title}"'
    try:
        subprocess.run(["osascript", "-e", action], check=False, capture_output=True)
    except OSError:
        pass
    return dingtalk_send(f"{title}\n{msg}")


def dingtalk_send(text):
    """推送钉钉机器人消息（正文自带关键词）。失败仅记日志，不影响主流程。"""
    if not DINGTALK_FILE.exists():
        return False
    url = DINGTALK_FILE.read_text(encoding="utf-8").strip().splitlines()
    url = url[0].strip() if url else ""
    if "robot/send" not in url or "access_token=" not in url:
        return False
    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": f"【{DINGTALK_KEYWORD}】{text}"},
    }).encode()
    for attempt in (1, 2):  # 钉钉偶发限流，最多重试一次
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            if result.get("errcode") == 0:
                return True
            log(f"钉钉推送被拒：errcode={result.get('errcode')} {result.get('errmsg', '')}")
            return False
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            if attempt == 2:
                log(f"钉钉推送异常（已跳过）：{e}")
                return False
            time.sleep(2)
    return False


# ---------- Cookie / 签名 ----------

def load_cookie(required=True):
    if not COOKIE_FILE.exists():
        if required:
            log(f"缺少 {COOKIE_FILE}，请按 README.md 把浏览器里的 Cookie 粘贴进去，"
                f"或在 {CRED_FILE} 配置账号密码后运行 python3 monitor.py --login。")
            sys.exit(1)
        return None
    raw = COOKIE_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        if required:
            log(f"{COOKIE_FILE} 是空的，请把浏览器登录后的 Cookie 粘贴进去。")
            sys.exit(1)
        return None
    if raw.lower().startswith("cookie:"):
        raw = raw[len("cookie:"):].strip()
    raw = raw.replace("\n", "")
    if "PASTE_COOKIE_HERE" in raw:
        if required:
            log(f"请先按 README.md 第 1 步，把浏览器里的 Cookie 粘贴到 {COOKIE_FILE}。")
            sys.exit(1)
        return None
    return raw


def cookie_value(cookie_header, name):
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def get_account(cookie_header):
    """从前端 cookie user-cookie-guokao 解出登录账号（即 HMAC 密钥）。

    前端逻辑（core-security.js）：
      cookie 值是 JSON，user 字段去头尾各 4 个字符后 base64 解码，取 account。
    """
    raw = cookie_value(cookie_header, "user-cookie-guokao")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(urllib.parse.unquote(raw))
        except json.JSONDecodeError:
            return None
    user_field = data.get("user") if isinstance(data, dict) else None
    if not user_field or len(user_field) <= 8:
        return None
    try:
        b64 = user_field[4:-4]
        b64 += "=" * (-len(b64) % 4)
        decoded = base64.b64decode(b64).decode("utf-8")
        return json.loads(decoded).get("account")
    except Exception:
        return None


def sign(method, path, params_str, data_str, ts, key):
    """复刻前端 RestFulSecuritySessionSigner：
    HMAC-SHA256("METHOD:path:params:data:ts", key=账号)
    本接口为无参数 GET，params 与 data 均为空串。
    """
    msg = f"{method}:{path}:{params_str}:{data_str}:{ts}"
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


# ---------- DES 加密（登录用，逐行复刻前端 DESUtil.js 的 strEnc） ----------

_DES_SHIFT = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
_DES_PC2 = [13,16,10,23,0,4,2,27,14,5,20,9,22,18,11,3,25,7,15,6,26,19,12,1,
            40,51,30,36,46,54,29,39,50,44,32,47,43,48,38,55,33,52,45,41,49,35,28,31]
_DES_P = [15,6,19,20,28,11,27,16,0,14,22,25,4,17,30,9,1,7,23,13,31,26,2,8,18,12,29,5,21,10,3,24]
_DES_FP = [39,7,47,15,55,23,63,31,38,6,46,14,54,22,62,30,37,5,45,13,53,21,61,29,36,4,44,12,52,20,60,28,
           35,3,43,11,51,19,59,27,34,2,42,10,50,18,58,26,33,1,41,9,49,17,57,25,32,0,40,8,48,16,56,24]
_DES_SBOX = [
 [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],[0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
  [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],[15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]],
 [[15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10],[3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5],
  [0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15],[13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9]],
 [[10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8],[13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1],
  [13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7],[1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12]],
 [[7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15],[13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9],
  [10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4],[3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14]],
 [[2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9],[14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6],
  [4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14],[11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3]],
 [[12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11],[10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8],
  [9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6],[4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13]],
 [[4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1],[13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6],
  [1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2],[6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12]],
 [[13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7],[1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2],
  [7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8],[2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]],
]
_DES_BOXBIN = [format(i, "04b") for i in range(16)]


def _des_str_to_bt(s):
    bt = [0] * 64
    if len(s) < 4:
        chars = [ord(c) for c in s] + [0] * (4 - len(s))
    else:
        chars = [ord(s[i]) for i in range(4)]
    for i, c in enumerate(chars):
        for j in range(16):
            f = 1
            for m in range(15, j, -1):
                f *= 2
            bt[16 * i + j] = int(c / f) % 2
    return bt


def _des_get_key_bytes(key):
    e = []
    n = int(len(key) / 4)
    for s in range(n):
        e.append(_des_str_to_bt(key[4 * s:4 * s + 4]))
    if len(key) % 4 > 0:
        e.append(_des_str_to_bt(key[4 * n:]))
    return e


def _des_generate_keys(bt64):
    e = [0] * 56
    for t in range(7):
        for j in range(8):
            e[8 * t + j] = bt64[8 * (7 - j) + t]
    rounds = []
    for rnd in range(16):
        for _ in range(_DES_SHIFT[rnd]):
            s0, o0 = e[0], e[28]
            for k in range(27):
                e[k] = e[k + 1]
                e[28 + k] = e[29 + k]
            e[27], e[55] = s0, o0
        rounds.append([e[_DES_PC2[i]] for i in range(48)])
    return rounds


def _des_init_permute(r):
    e = [0] * 64
    m, n = 1, 0
    for i in range(4):
        k = 0
        for j in range(7, -1, -1):
            e[8 * i + k] = r[8 * j + m]
            e[8 * i + k + 32] = r[8 * j + n]
            k += 1
        m += 2
        n += 2
    return e


def _des_expand_permute(r):
    e = [0] * 48
    for i in range(8):
        e[6 * i + 0] = r[31] if i == 0 else r[4 * i - 1]
        e[6 * i + 1] = r[4 * i + 0]
        e[6 * i + 2] = r[4 * i + 1]
        e[6 * i + 3] = r[4 * i + 2]
        e[6 * i + 4] = r[4 * i + 3]
        e[6 * i + 5] = r[0] if i == 7 else r[4 * i + 4]
    return e


def _des_s_box_permute(r):
    e = [0] * 32
    for m in range(8):
        l = 2 * r[6 * m + 0] + r[6 * m + 5]
        b = 2 * r[6 * m + 1] * 2 * 2 + 2 * r[6 * m + 2] * 2 + 2 * r[6 * m + 3] + r[6 * m + 4]
        a = _DES_BOXBIN[_DES_SBOX[m][l][b]]
        for j in range(4):
            e[4 * m + j] = int(a[j])
    return e


def _des_enc_block(r, key_bt64):
    keys = _des_generate_keys(key_bt64)
    n = _des_init_permute(r)
    t, s = n[:32], n[32:]
    for c in range(16):
        o = t[:]
        t = s[:]
        expanded = [x ^ y for x, y in zip(_des_expand_permute(s), keys[c])]
        s = [x ^ y for x, y in zip(_des_p_permute(_des_s_box_permute(expanded)), o)]
    return _des_finally_permute(s + t)


def _des_p_permute(r):
    return [r[_DES_P[i]] for i in range(32)]


def _des_finally_permute(r):
    return [r[_DES_FP[i]] for i in range(64)]


def _des_bt64_to_hex(r):
    out = []
    for i in range(16):
        nib = "".join(str(r[4 * i + j]) for j in range(4))
        out.append(format(int(nib, 2), "X"))
    return "".join(out)


def des_str_enc(data, first="auth", second="nps", third="web"):
    """= 前端 strEnc：数据按 4 字符分块（不足补 0），每块级联三次单 DES 加密。"""
    if not data:
        return ""
    kt = _des_get_key_bytes(first)
    ks = _des_get_key_bytes(second)
    kw = _des_get_key_bytes(third)

    def encrypt_block(bt):
        for k in kt:
            bt = _des_enc_block(bt, k)
        for k in ks:
            bt = _des_enc_block(bt, k)
        for k in kw:
            bt = _des_enc_block(bt, k)
        return bt

    hex_out = []
    if len(data) < 4:
        hex_out.append(_des_bt64_to_hex(encrypt_block(_des_str_to_bt(data))))
    else:
        full, rem = divmod(len(data), 4)
        for v in range(full):
            hex_out.append(_des_bt64_to_hex(encrypt_block(_des_str_to_bt(data[4 * v:4 * v + 4]))))
        if rem:
            hex_out.append(_des_bt64_to_hex(encrypt_block(_des_str_to_bt(data[4 * full:]))))
    return "".join(hex_out)


# ---------- 自动登录（续登 cookie） ----------

def load_credentials():
    """credentials.txt 两行：第一行账号（身份证/手机号），第二行密码。"""
    if not CRED_FILE.exists():
        return None
    lines = [ln.strip() for ln in CRED_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 2 or any("PASTE" in x.upper() or "填账号" in x for x in lines[:2]):
        return None
    return lines[0], lines[1]


def hb01_id():
    """考试批次 ID：从线上 core-constant.js 实时取，失败用内置回退值。"""
    try:
        req = urllib.request.Request(CORE_CONSTANT_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            m = re.search(r'hb01Id=neu\.examSelect\?"":"([0-9a-f]+)"',
                          resp.read().decode("utf-8", "ignore"))
            if m:
                return m.group(1)
    except (urllib.error.URLError, OSError):
        pass
    return HB01_ID_FALLBACK


def _open_image(path):
    try:
        subprocess.run(["open", str(path)], check=False, capture_output=True)
    except OSError:
        pass


def make_ocr_solver():
    """验证码识别器（纯 GLM 视觉，自洽共识）：
    - GLM 对 3 倍/4 倍放大图各识别一次，避免小图无法辨认
    - 两次一致（忽略大小写）才提交；不一致或调用失败返回 None，
      由调用方随机睡眠后换一张验证码重试
    无 key 时返回 None（do_login 据此退化为人工输码或放弃）。
    """
    glm = make_glm_vision_solver()
    if glm is None:
        return None

    def solve(png_bytes):
        g1 = glm(_upscale_image(png_bytes, 3), upscale=False)
        if not g1:
            return None
        g2 = glm(_upscale_image(png_bytes, 4), upscale=False)
        if not g2:
            return None
        return g1 if g1.lower() == g2.lower() else None

    return solve


def _upscale_image(png_bytes, factor=3):
    """放大验证码；无 OpenCV 的 macOS 使用系统 sips，失败返回原图。"""
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return png_bytes
        big = cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
        ok, out = cv2.imencode(".png", big)
        return out.tobytes() if ok else png_bytes
    except Exception:
        pass
    if sys.platform == "darwin" and png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        try:
            width = int.from_bytes(png_bytes[16:20], "big")
            height = int.from_bytes(png_bytes[20:24], "big")
            with tempfile.TemporaryDirectory(prefix="scs-captcha-") as tmp:
                source = Path(tmp) / "source.png"
                target = Path(tmp) / "scaled.png"
                source.write_bytes(png_bytes)
                subprocess.run(
                    ["/usr/bin/sips", "-z", str(height * factor), str(width * factor),
                     str(source), "--out", str(target)],
                    check=True, capture_output=True, timeout=15,
                )
                return target.read_bytes()
        except (OSError, subprocess.SubprocessError):
            pass
    return png_bytes


def make_glm_vision_solver():
    """GLM 视觉 API 识别器（官方免费模型梯队，拥堵自动切换）。无 key 返回 None。
    solve(png, upscale=True)。
    """
    key = ""
    if GLM_KEY_FILE.exists():
        key = GLM_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        key = os.environ.get("GLM_API_KEY", "").strip()
    if not key:
        return None

    def solve(png_bytes, upscale=True):
        src = _upscale_image(png_bytes) if upscale else png_bytes
        b64 = base64.b64encode(src).decode()
        prompt = ("这是登录验证码图片，共4位字母数字（大小写敏感），"
                  "有彩色干扰线，请仔细区分 l/1/i、0/O/o、8/B。"
                  "只输出这4个字符，不要输出任何其他文字。")
        congested = []
        for model in GLM_VISION_MODELS:
            # 图片格式：官方新版 SDK 用裸 base64，旧接口用 data URL，400 时互换重试
            for image_ref in (f"data:image/png;base64,{b64}", b64):
                try:
                    payload = json.dumps({
                        "model": model,
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": image_ref}},
                            {"type": "text", "text": prompt},
                        ]}],
                        "temperature": 0.1,
                        "max_tokens": 1024,  # 思考型模型需要空间输出 </think> 后的答案
                    }).encode()
                    req = urllib.request.Request(GLM_VISION_URL, data=payload, headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {key}",
                    })
                    with urllib.request.urlopen(req, timeout=40) as resp:
                        answer = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
                    text = re.sub(r"<think>.*?</think>", "", str(answer), flags=re.S)
                    code = re.sub(r"[^0-9A-Za-z]", "", text)
                    if len(code) == 4:
                        return code
                    log(f"{model} 回答无法解析（{str(answer)[:40]!r}），换下一模型…")
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        congested.append(model)
                        break  # 拥堵：换下一模型
                    if e.code in (400, 404, 422):
                        if image_ref.startswith("data:") is False:
                            log(f"{model} 请求被拒（HTTP {e.code}），换下一模型…")
                            break
                        continue  # 换图片格式重试
                    log(f"{model} HTTP {e.code}，换下一模型…")
                    break
                except Exception as e:
                    log(f"{model} 调用异常（{e}），换下一模型…")
                    break
        if congested:
            log(f"GLM 模型拥堵（{', '.join(congested)}），跳过该来源。")
        return None

    return solve


class _SetCookieRecorder(urllib.request.BaseHandler):
    """记录流程中每一条原始 Set-Cookie，绕开 cookiejar 的各种属性过滤。"""

    def __init__(self, sink):
        self._sink = sink

    def http_response(self, request, response):
        for sc in (response.headers.get_all("Set-Cookie") or []):
            self._sink.append(sc)
        return response

    https_response = http_response


def save_cookie_bulk(jar, raw_setcookies):
    """合并 jar 与原始 Set-Cookie 头，覆盖式写入 cookie.txt。"""
    merged = {}
    for sc in raw_setcookies:
        first = sc.split(";", 1)[0].strip()
        if "=" in first:
            k, v = first.split("=", 1)
            if k.strip():
                merged[k.strip()] = v
    for c in jar:
        if c.value is not None:
            merged.setdefault(c.name, c.value)
    if not merged:
        return False
    COOKIE_FILE.write_text("; ".join(f"{k}={v}" for k, v in merged.items()) + "\n",
                           encoding="utf-8")
    try:
        COOKIE_FILE.chmod(0o600)
    except OSError:
        pass
    return True


def do_login(captcha_solver=None, interactive=None):
    """完整登录流程：token → 验证码 → DES 加密登录 → 保存 cookie.txt。

    纯 GLM 视觉识别（自洽共识，两次采样一致才提交）；未达共识则随机睡眠
    换一张验证码重试。仅交互终端且未配 GLM key 时退化为人工输码。
    验证码错误不会触发密码校验，多试几次无风险。返回 True/False。
    """
    cred = load_credentials()
    if not cred:
        log(f"未配置账号密码：请在 {CRED_FILE} 写入两行（第 1 行账号，第 2 行密码）。")
        return False
    if interactive is None:
        interactive = sys.stdin.isatty()

    jar = http.cookiejar.CookieJar()
    seen_cookies = []
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                         _SetCookieRecorder(seen_cookies))
    base_headers = {"User-Agent": UA, "Referer": LOGIN_REFERER,
                    "Origin": SITE, "Accept": "*/*"}

    # 1) 预防 token（失败不阻断，与前端一致只触发刷新）
    try:
        req = urllib.request.Request(TOKEN_URL, headers=base_headers)
        opener.open(req, timeout=20).read()
    except (urllib.error.URLError, OSError) as e:
        log(f"获取 token 异常（继续尝试登录）：{e}")

    hb01 = hb01_id()
    solver = captcha_solver or make_ocr_solver()
    MAX_SUBMITS = 5      # 最多提交登录的次数
    MAX_ROUNDS = 40      # 最多取验证码的轮数（未达共识的轮次只换图不提交）
    submits = fetches = 0
    ocr_wrong = 0
    confirm_mode = False  # 提交错 2 次后：每张验证码都请用户确认（仅交互模式）

    if solver is None:
        if interactive:
            log(f"未配置 GLM key（{GLM_KEY_FILE.name}），退化为人工输码模式。")
        else:
            log(f"未配置 GLM key（{GLM_KEY_FILE.name}），后台无法自动识别验证码。")
            return False

    while submits < MAX_SUBMITS and fetches < MAX_ROUNDS:
        fetches += 1
        # 2) 取验证码 id + 图片
        try:
            req = urllib.request.Request(CAPTCHA_JSON_URL, headers=base_headers)
            cid = json.loads(opener.open(req, timeout=20).read().decode())["id"]
            img_url = f"{CAPTCHA_JSON_URL}/{cid}?random={time.time()}"
            req = urllib.request.Request(img_url, headers=base_headers)
            png = opener.open(req, timeout=20).read()
        except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as e:
            log(f"获取验证码失败：{e}")
            return False

        code = solver(png)
        if code is None:
            if solver is None:
                # 无 GLM key 的交互退化模式：人工看图输码
                CAPTCHA_PNG.write_bytes(png)
                _open_image(CAPTCHA_PNG)
                try:
                    code = input(f"[提交 {submits + 1}/{MAX_SUBMITS}] "
                                 f"验证码图片已自动打开，请输入图中字符: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return False
                if not code:
                    continue
            else:
                # 纯 GLM 方案：两次采样未达共识/模型拥堵 → 随机睡眠后换一张重试
                nap = random.uniform(3, 8)
                log(f"第 {fetches}/{MAX_ROUNDS} 轮未达共识，睡眠 {nap:.1f}s 后换图重试…")
                time.sleep(nap)
                continue
        if interactive and confirm_mode:
            # 已连续提交错 2 次：弹图人工把关，自动识别仅作参考
            CAPTCHA_PNG.write_bytes(png)
            _open_image(CAPTCHA_PNG)
            try:
                typed = input(f"验证码图片已自动打开（提交 {submits + 1}/{MAX_SUBMITS}）"
                              f"（自动识别为 {code}，若一致直接回车）: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            if not typed:
                continue
            code = typed

        # 3) 登录（账号密码 DES 加密，与前端一致）
        form = urllib.parse.urlencode({
            "username": des_str_enc(cred[0]),
            "password": des_str_enc(cred[1]),
            "captchaId": cid,
            "captchaWord": code,
            "hb01Id": hb01,
        }).encode()
        req = urllib.request.Request(LOGIN_URL, data=form, headers={
            **base_headers, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        try:
            resp = opener.open(req, timeout=20)
            body = resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            log(f"登录请求失败：HTTP {e.code}")
            return False
        except (urllib.error.URLError, OSError) as e:
            log(f"登录网络异常：{e}")
            return False
        submits += 1
        time.sleep(0.8)

        # 解析响应（该服务器实际格式：{"flag":"0/1","msg":"..."}）
        flag, msg = "", ""
        try:
            body_json = json.loads(body)
            flag = str(body_json.get("flag", body_json.get("status", "")))
            msg = body_json.get("message") or body_json.get("msg") or ""
        except json.JSONDecodeError:
            msg = body[:120]

        # 成功判定：拿到用户 Cookie，或服务端明确返回登录成功
        login_ok = (any(c.name == "user-cookie-guokao" for c in jar)
                    or flag == "1" or "登录成功" in str(msg))
        if not login_ok:
            log(f"登录失败（{submits}/{MAX_SUBMITS}）：{msg or '未知错误'}")
            if "验证码" not in str(msg):
                return False  # 账号/密码等其他错误：不再重试
            if solver and interactive and not confirm_mode:
                ocr_wrong += 1
                if ocr_wrong >= 2:
                    confirm_mode = True
                    log("自动识别连续 2 次未通过，后续验证码改为人工确认。")
            continue

        log(f"服务器确认登录成功（{msg or flag}），补完登录收尾步骤…")

        # 网页版此时会跳转 person_judge.html 并调用 judge 接口（可能在此下发最终 Cookie）
        if not any(c.name == "user-cookie-guokao" for c in jar):
            try:
                ts = str(int(time.time() * 1000))
                req = urllib.request.Request(JUDGE_URL, headers={
                    **base_headers,
                    "X-MICRO-TIME": ts,
                    "X-HMAC-HASH": sign("GET", JUDGE_PATH, "", "", ts, cred[0]),
                    "X-USER": cred[0],
                    "Mobile-Client": "0",
                    "X-Requested-With": "",
                })
                opener.open(req, timeout=20).read()
            except (urllib.error.URLError, OSError) as e:
                log(f"judge 步骤异常（不阻断）：{e}")

        if not save_cookie_bulk(jar, seen_cookies):
            log("登录成功但没有取得任何 Cookie，请把本日志反馈给我。")
            return False

        # 若始终没拿到用户信息 Cookie，用登录账号合成一个（签名需要；服务端会话本身已生效）
        cookie_now = COOKIE_FILE.read_text(encoding="utf-8").strip()
        if get_account(cookie_now) is None:
            merged = {}
            for part in cookie_now.split("; "):
                k, _, v = part.partition("=")
                if k.strip():
                    merged[k.strip()] = v
            user_json = json.dumps({"account": cred[0]}, ensure_ascii=False)
            merged["user-cookie-guokao"] = urllib.parse.quote(
                json.dumps({"user": "AAAA" + base64.b64encode(user_json.encode()).decode() + "BBBB"},
                           ensure_ascii=False))
            COOKIE_FILE.write_text("; ".join(f"{k}={v}" for k, v in merged.items()) + "\n",
                                   encoding="utf-8")
            log("未从服务器取得用户信息 Cookie，已用登录账号合成（用于请求签名）。")

        status, _ = fetch_collection(COOKIE_FILE.read_text(encoding="utf-8").strip())
        if status == 200:
            log("已验证：收藏接口访问正常。")
            notify("自动登录成功", f"报名系统登录态已自动恢复（{now()}），监控继续。", sound=False)
        else:
            log(f"提醒：登录成功但收藏接口返回 HTTP {status}，如监控仍报 401 请把日志反馈给我。")
        return True
    log("多次尝试仍未登录成功，放弃本次。")
    return False


def try_relogin():
    """401 后的自动续登：优先 OCR，其次交互输入。10 分钟冷却防止无效重试。"""
    if load_credentials() is None:
        log(f"登录已失效且未配置账号密码，无法自动续登（可在 {CRED_FILE} 配置，或手动更新 cookie.txt）。")
        return False
    st = load_state()
    last_fail = st.get("last_relogin_fail")
    if last_fail:
        try:
            elapsed = time.time() - datetime.strptime(last_fail, "%Y-%m-%d %H:%M:%S").timestamp()
            if 0 <= elapsed < 600:
                log(f"距上次自动登录失败还不到 10 分钟，本轮跳过自动续登（避免反复尝试）。")
                return False
        except ValueError:
            pass

    def mark_fail():
        st["last_relogin_fail"] = now()
        save_state(st)

    solver = make_ocr_solver()
    if solver:
        log("尝试自动识别验证码重新登录…")
        if do_login(captcha_solver=solver, interactive=False):
            st.pop("last_relogin_fail", None)
            save_state(st)
            return True
        log("自动识别登录未成功，改用人工输入。")
    if sys.stdin.isatty():
        ok = do_login(interactive=True)
        if ok:
            st.pop("last_relogin_fail", None)
            save_state(st)
        else:
            mark_fail()
        return ok
    mark_fail()
    log("后台运行无法人工输入验证码。请手动执行：python3 monitor.py --login")
    return False


# ---------- 请求与解析 ----------

def fetch_collection(cookie_header):
    account = get_account(cookie_header) or "gk"
    ts = str(int(time.time() * 1000))
    headers = {
        "User-Agent": UA,
        "Referer": REFERER,
        "Accept": "*/*",
        "Cookie": cookie_header,
        "X-MICRO-TIME": ts,
        "X-HMAC-HASH": sign("GET", API_PATH, "", "", ts, account),
        "X-USER": account,
        "Mobile-Client": "0",
        "X-Requested-With": "",
    }
    tcode = cookie_value(cookie_header, "tcode")
    if tcode:
        headers["request-tcode"] = tcode
    req = urllib.request.Request(API_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log(f"网络异常：{e}")
        return -1, None


def extract_positions(data):
    """返回 {职位代码: 信息}。waitnum/passnum 为 -1 表示"统计中"。"""
    items = data.get("vhb11DetailListDto") or []
    positions = {}
    for it in items:
        code = str(it.get("ahb110", "")).strip()
        positions[code] = {
            "dept": it.get("ahd011") or "",   # 招录机关
            "bureau": it.get("ahd020") or "", # 用人司局
            "name": it.get("ahb111") or "",   # 职位名称
            "plan": it.get("ahb113"),         # 招考人数
            "wait": it.get("waitnum"),        # 待审查人数
            "pass": it.get("passnum"),        # 审查通过人数
        }
    return positions


def load_filter():
    if not FILTER_FILE.exists():
        return None
    codes = {ln.strip() for ln in FILTER_FILE.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")}
    return codes or None


# ---------- 状态 / 变化检测 ----------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"positions": {}}


def save_state(state):
    state["updated"] = now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def append_history(positions):
    new_file = not HISTORY_CSV.exists()
    with HISTORY_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["时间", "职位代码", "招录机关", "职位名称",
                        "招考人数", "待审查人数", "审查通过人数"])
        for code, p in positions.items():
            w.writerow([now(), code, p["dept"], p["name"], p["plan"], p["wait"], p["pass"]])


def fmt_num(n):
    return "统计中" if n in (-1, None, "-1") else str(n)


def diff_line(code, p, old):
    if all(p.get(key) == old.get(key) for key in ("plan", "wait", "pass")):
        return None
    unit = " / ".join(dict.fromkeys(v for v in (p.get("dept"), p.get("bureau")) if v))
    parts = [f"{p.get('name') or '未命名职位'}（{code}）"]
    if unit:
        parts.append(f"单位：{unit}")
    for key, label in (("plan", "招考人数"), ("wait", "待审查人数"),
                       ("pass", "审查通过人数")):
        new_v, old_v = p.get(key), old.get(key)
        if new_v == old_v:
            parts.append(f"{label}：{fmt_num(new_v)}（无变化）")
            continue
        delta = ""
        if (isinstance(new_v, int) and isinstance(old_v, int)
                and new_v >= 0 and old_v >= 0):
            delta = f"（{new_v - old_v:+d}）"
        parts.append(f"{label}：{fmt_num(old_v)} → {fmt_num(new_v)}{delta}")
    return "\n".join(parts)


def snapshot_line(code, p):
    unit = " / ".join(dict.fromkeys(v for v in (p.get("dept"), p.get("bureau")) if v))
    lines = [f"{p.get('name') or '未命名职位'}（{code}）"]
    if unit:
        lines.append(f"单位：{unit}")
    for key, label in (("plan", "招考人数"), ("wait", "待审查人数"),
                       ("pass", "审查通过人数")):
        lines.append(f"{label}：{fmt_num(p.get(key))}")
    return "\n".join(lines)


def check_once(quiet=False, sound=True, initial=False) -> bool:
    """查询一次。返回是否检测到变化；无法获得有效登录态时退出进程。"""
    cookie = load_cookie(required=False)
    status, data = -1, None
    if cookie:
        status, data = fetch_collection(cookie)

    if cookie is None or status == 401:
        log("尚无有效登录态，尝试自动续登…" if cookie is None
            else "登录已失效（HTTP 401），尝试自动续登…")
        if try_relogin():
            cookie = load_cookie(required=False)
            if cookie:
                status, data = fetch_collection(cookie)
        if status == 401 or cookie is None:
            log("自动续登未成功：请运行 python3 monitor.py --login，"
                "或重新登录网页版后更新 cookie.txt。")
            notify("报名系统监控", "自动登录失败，请运行 monitor.py --login！", sound=sound)
            sys.exit(2)
    if status != 200 or data is None:
        log(f"请求失败：HTTP {status}，稍后重试。")
        sys.exit(1)
    if data.get("collectFlag") != "1":
        flag = data.get("flag", "未知错误")
        log(f"接口返回异常：{flag}")
        if "登录" in str(flag) or "login" in str(flag).lower():
            notify("报名系统监控", "登录已失效，请更新 cookie.txt！", sound=sound)
            sys.exit(2)
        sys.exit(1)

    positions = extract_positions(data)
    if not positions:
        log("收藏列表为空：请先在网页上收藏要监控的职位。")
        return False

    flt = load_filter()
    if flt:
        missing = flt - set(positions)
        if missing:
            log(f"提醒：以下职位代码不在收藏列表里（请确认是否已收藏）：{'、'.join(sorted(missing))}")
        positions = {c: p for c, p in positions.items() if c in flt}
        if not positions:
            log("过滤后没有可监控的职位，检查 positions.txt 里的职位代码。")
            return False

    state = load_state()
    old = state.get("positions", {})
    changes = []
    for code, p in positions.items():
        line = diff_line(code, p, old.get(code, p))
        if line:
            changes.append(line)

    append_history(positions)

    if initial or not state.get("initial_notified"):
        collected_at = now()
        header = f"采集时间：{collected_at}（北京时间）\n收藏职位：{len(positions)} 个"
        details = "\n\n".join(snapshot_line(code, p) for code, p in positions.items())
        if notify("收藏职位首次快照", header + "\n\n" + details, sound=sound):
            state["initial_notified"] = collected_at
            log("首次快照已推送，后续仅在人数变化时推送。")
        else:
            log("首次快照未成功推送，下次查询将重试。")
    elif changes:
        log("=== 检测到人数变化 ===")
        for line in changes:
            print("  " + line)
        header = f"采集时间：{now()}\n对比时间：{state.get('updated', '上次采集')}"
        notify("职位人数变化！", header + "\n\n" + "\n\n".join(changes), sound=sound)
    elif not quiet:
        log("收藏职位当前人数（无变化）：")
        for code, p in positions.items():
            print(f"  {code} {p['name'] or p['bureau']}："
                  f"招考 {fmt_num(p['plan'])}，待审 {fmt_num(p['wait'])}，通过 {fmt_num(p['pass'])}")

    state["positions"] = positions
    save_state(state)
    return bool(changes)


def main():
    ap = argparse.ArgumentParser(description="四川公考报名系统收藏职位人数监控")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="只查一次（默认）")
    g.add_argument("--watch", type=int, metavar="秒", help="常驻轮询，指定间隔秒数（建议≥300）")
    g.add_argument("--login", action="store_true",
                   help="重新登录并更新 cookie.txt（账号密码在 credentials.txt）")
    g.add_argument("--initial", action="store_true", help="查询并推送一次完整的首次快照")
    ap.add_argument("--quiet", action="store_true", help="无变化时不输出详情")
    ap.add_argument("--no-sound", action="store_true", help="变化时不播放提示音")
    args = ap.parse_args()

    if args.login:
        if do_login():
            cookie = load_cookie()
            status, data = fetch_collection(cookie)
            if status == 200:
                positions = extract_positions(data)
                log(f"验证通过：当前收藏 {len(positions)} 个职位。")
            else:
                log(f"注意：登录成功但收藏接口返回 HTTP {status}。")
            sys.exit(0)
        sys.exit(1)

    if args.watch:
        interval = max(args.watch, 60)
        log(f"开始监控收藏职位，每 {interval} 秒查询一次（Ctrl+C 退出）。")
        while True:
            try:
                check_once(quiet=args.quiet, sound=not args.no_sound)
            except SystemExit as e:
                if e.code in (1, 2):  # 请求失败/登录失效：退出循环
                    raise
            time.sleep(interval)
    else:
        check_once(quiet=args.quiet, sound=not args.no_sound, initial=args.initial)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止监控。")
