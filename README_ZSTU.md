# 浙江理工大学校园网自动认证

`zstu_wifi_login.py` 根据校园网 ePortal 页面实际调用动态读取 `pageInfo`、`getServices`，然后提交 `InterFace.do?method=login`。密码加密开关和 RSA 公钥不会写死；账号、密码和 Cookie 只在本次进程内使用。

## 使用

```bash
cp zstu.env.example .env.zstu
# 编辑 .env.zstu 填入账号和密码
chmod 600 .env.zstu

# 只检查入口、动态公钥和服务，不登录
./zstu-login.sh --status

# 登录
./zstu-login.sh
```

也可以直接传入当前完整门户地址：

```bash
./zstu-login.sh --url 'http://192.168.102.130/eportal/index.jsp?...当前查询参数...'
```

门户地址中的 `wlanuserip`、`mac`、`url` 等参数通常跟当前连接会话有关。你抓包里的旧 URL、`JSESSIONID` 和 Cookie 不要长期复用；如果自动发现返回 204 或提示没有查询参数，请在未认证状态下重新打开任意 HTTP 页面，复制浏览器跳转后的完整 ePortal 地址再用 `--url` 传入。

调试时只输出字段名和配置摘要，不输出账号、密码、Cookie 或 `userIndex`：

```bash
./zstu-login.sh --dry-run
./zstu-login.sh --debug
```

如果门户临时启用验证码：

```bash
./zstu-login.sh --validcode '图片中的验证码'
```

## 路由器版

已确认 RM2100 路由器环境为 MIPS/MIPSel + BusyBox `ash`，没有 Python，但有 `/usr/sbin/curl 8.18`。可使用 [zstu_wifi_login_router.sh](./zstu_wifi_login_router.sh)；该版本只依赖 shell 和 curl，当前门户 `passwordEncrypt=false` 时可直接认证。

把脚本复制到路由器持久目录，例如 `/etc/storage/zstu_wifi_login_router.sh`，再参考 [zstu_wifi_router.conf.example](./zstu_wifi_router.conf.example) 创建 `/etc/storage/zstu_wifi.conf`：

```sh
ZSTU_WIFI_USERNAME='你的账号'
ZSTU_WIFI_PASSWORD='你的密码'
# 建议填写当前未认证时浏览器跳转后的完整 URL；不填则尝试自动发现
ZSTU_WIFI_URL='http://192.168.102.130/eportal/index.jsp?...当前查询参数...'
ZSTU_WIFI_SERVICE=''
```

然后运行：

```sh
chmod 700 /etc/storage/zstu_wifi_login_router.sh
chmod 600 /etc/storage/zstu_wifi.conf
/etc/storage/zstu_wifi_login_router.sh --status
/etc/storage/zstu_wifi_login_router.sh
```

如果希望路由器持续检测掉线并自动重登，再复制 [zstu_wifi_router_watch.sh](./zstu_wifi_router_watch.sh)，然后运行：

```sh
chmod 700 /etc/storage/zstu_wifi_router_watch.sh
/etc/storage/zstu_wifi_router_watch.sh >/tmp/zstu_wifi_watch.log 2>&1 &
```

它默认每 60 秒检查一次；可用 `ZSTU_WIFI_CHECK_INTERVAL=30` 改成 30 秒。HTTP 204 表示当前已在线，认证入口重定向回来时才提交登录；暂时断网则等待下一轮重试。要让它重启后自动运行，需要把启动命令加入该路由器固件的启动脚本/计划任务。

RM2100 的 `/etc/storage` 启动时会从 Storage 闪存分区恢复。通过 SSH 修改后必须执行：

```sh
/sbin/mtd_storage.sh save
```

看到 `Done.` 后再重启，否则脚本和配置只存在当前运行内存中。

如果 `pageInfo` 将 `passwordEncrypt` 改为 `true`，路由器版会停止并提示使用 Mac 版或带 Python/Node 的旁路设备；它不会错误地把未加密密码提交出去。

你贴出的请求中包含了会话 Cookie 和保存密码密文，建议立即退出当前校园网会话并修改校园网密码；之后不要把完整 Cookie、密码字段或带认证信息的 curl 日志发到公开位置。
