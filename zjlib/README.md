# 浙江图书馆 Wi-Fi 自动认证

本目录提供一个本地登录脚本，用于浙江图书馆 Wi-Fi 的 Online Service 认证页。脚本会模拟页面在 macOS 上的 XHR 登录逻辑，而不是直接提交整个 HTML form。

> **适用范围**：目前支持 **浙江图书馆大学路馆区** 和 **城市书房自习室** 的 Wi-Fi 认证（统一走 `2.2.1.1` 认证网关）。其它馆区若使用不同网关或认证方式，可能不适用。

从仓库根目录运行：

```bash
python3 zjlib/wifi_login.py
```

也可以直接运行 shell wrapper：

```bash
./zjlib/login.sh
```

脚本默认读取仓库根目录的 `.env`。模板位于 `zjlib/env.example`：

```bash
cp zjlib/env.example .env
# 然后编辑 .env 填入真实账号密码
```

`.env` 的内容如下：

```bash
ZJLIB_WIFI_USERNAME=your_username
ZJLIB_WIFI_PASSWORD=your_password
ZJLIB_WIFI_URL=https://2.2.1.1:8443/cn/index.html
```

也可以直接用环境变量覆盖：

```bash
ZJLIB_WIFI_USERNAME=... ZJLIB_WIFI_PASSWORD=... python3 zjlib/wifi_login.py
```

脚本会在进程内清理 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 等代理环境变量，并使用禁用代理的 opener 直连 `2.2.1.1`。如果你使用的是透明代理、TUN/VPN 模式或系统级代理，请在代理客户端里额外把 `2.2.1.1`、`2.2.1.0/24` 或浙江图书馆 WLAN 网段加入直连/绕过规则。

常用参数：

```bash
python3 zjlib/wifi_login.py --status
python3 zjlib/wifi_login.py --debug
python3 zjlib/wifi_login.py --url https://2.2.1.1:8443/cn/index.html
python3 zjlib/wifi_login.py --timeout 10
./zjlib/login.sh --debug
```

`--debug` 会打印识别到的登录页、提交地址、字段名、**完整请求体、响应状态/头/体摘要**，并保存调试 HTML。脚本按 `/cn/login.html` 中 `submitFunc()` 的 Mac 分支发送字段：`username`、`password`、`RedirectUrl`、`anonymous`、`anonymousurl`、`checkbox`、`checkbox1`、`accesscode`。

> 注意：`--debug` 的请求体会以明文打印账号密码，分享输出前请先打码。
