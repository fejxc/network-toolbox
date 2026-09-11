# Codex Remote SSH 代理配置方案

## 1. 问题现象

本地 Codex 可以通过 SSH 正常连接远程服务器，也能访问远程目录，但 Codex 会反复出现：

```text
Reconnecting...
waiting for network
```

远程服务器中手动执行：

```bash
proxy_on
```

后，终端里的 Codex CLI 可以正常访问 OpenAI。

服务器现有 `proxy_on`：

```bash
proxy_on () 
{ 
    unset all_proxy ALL_PROXY ws_proxy WS_PROXY wss_proxy WSS_PROXY;
    export http_proxy='http://127.0.0.1:7890';
    export https_proxy='http://127.0.0.1:7890';
    export HTTP_PROXY="$http_proxy";
    export HTTPS_PROXY="$https_proxy";
    export no_proxy='127.0.0.1,localhost,::1';
    export NO_PROXY="$no_proxy";
    echo '代理已开启：127.0.0.1:7890'
}
```

---

## 2. 根本原因

`proxy_on` 只会给**当前终端 Shell** 注入代理环境变量。

因此：

```text
手动打开终端
    ↓
执行 proxy_on
    ↓
当前 Shell 获得 HTTP_PROXY / HTTPS_PROXY
    ↓
Codex CLI 可以联网
```

但本地 Codex 通过 SSH 自动启动远程 Codex / app-server 时，不一定经过已经执行过 `proxy_on` 的 Shell，因此不会自动继承这些代理变量。

表现就是：

```text
SSH 连接                正常
远程目录访问            正常
远程 Codex CLI          已安装
Codex 访问 OpenAI       失败
```

---

## 3. 当前环境

远程服务器：

```text
HOME=/root
CODEX_HOME=未设置
```

因此 Codex 默认配置目录为：

```text
/root/.codex
```

Codex CLI：

```bash
command -v codex
```

输出：

```text
/opt/nodejs/bin/codex
```

版本：

```text
codex-cli 0.147.0
```

服务器代理地址：

```text
http://127.0.0.1:7890
```

---

## 4. 最终解决方案

给 Codex 自己配置固定代理环境。

创建：

```text
/root/.codex/.env
```

内容：

```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=127.0.0.1,localhost,::1

http_proxy=http://127.0.0.1:7890
https_proxy=http://127.0.0.1:7890
no_proxy=127.0.0.1,localhost,::1
```

创建命令：

```bash
mkdir -p /root/.codex

cat > /root/.codex/.env <<'EOF'
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=127.0.0.1,localhost,::1
http_proxy=http://127.0.0.1:7890
https_proxy=http://127.0.0.1:7890
no_proxy=127.0.0.1,localhost,::1
EOF
```

检查：

```bash
cat /root/.codex/.env
```

---

## 5. 验证 Codex 是否真正脱离 `proxy_on`

为了确认 Codex 是通过 `/root/.codex/.env` 自动获取代理，而不是继承当前终端环境，可以主动移除当前 Shell 的代理变量再测试：

```bash
env \
-u HTTP_PROXY \
-u HTTPS_PROXY \
-u http_proxy \
-u https_proxy \
-u ALL_PROXY \
-u all_proxy \
/opt/nodejs/bin/codex exec \
--skip-git-repo-check \
"只回复 OK"
```

成功结果：

```text
OpenAI Codex v0.147.0
...
user
只回复 OK
codex
OK
```

这说明：

```text
当前 Shell 无代理
        ↓
Codex 启动
        ↓
读取 /root/.codex/.env
        ↓
使用 127.0.0.1:7890
        ↓
成功访问 OpenAI
```

说明配置成功。

---

## 6. Remote SSH 重新连接

配置完成后，如果 Codex Desktop 仍然显示：

```text
Reconnecting...
waiting for network
```

可能是旧的远程 app-server 进程仍在运行。

检查：

```bash
ps -ef | grep '[c]odex.*app-server'
```

如有旧进程，可执行：

```bash
pkill -f 'codex.*app-server' || true
```

然后在本地 Codex 中：

```text
断开远程 SSH
    ↓
重新连接服务器
    ↓
重新打开远程项目
```

此后 Remote SSH 应可正常使用。

---

## 7. 日常使用方式

### Codex CLI

以后使用 Codex 时，不需要再执行：

```bash
proxy_on
```

因为 Codex 会自动读取：

```text
/root/.codex/.env
```

---

### 普通终端程序

如果 `curl`、`git`、`wget`、`pip`、`npm` 等其他程序仍需要代理，可以继续手动执行：

```bash
proxy_on
```

因此可以理解为：

```text
Codex
  → 自动使用 /root/.codex/.env

普通 Shell 程序
  → 需要时继续 proxy_on
```

这种方式比把代理直接永久写入 `/root/.bashrc` 更干净，不会强制服务器上所有程序始终走代理。

---

## 8. 网络快速测试

测试本地代理端口能否访问 ChatGPT：

```bash
curl -x http://127.0.0.1:7890 \
  -I \
  --connect-timeout 10 \
  --max-time 20 \
  https://chatgpt.com
```

只要能够快速收到 HTTP 响应，例如：

```text
HTTP/2 200
```

或者：

```text
HTTP/2 302
HTTP/2 403
```

就说明：

```text
127.0.0.1:7890
        ↓
代理工作正常
        ↓
外网可达
```

如果出现：

```text
Connection refused
Timeout
Could not connect
```

则优先检查服务器上的 `127.0.0.1:7890` 代理服务本身是否正常。

---

## 9. `models_cache.json` 报错说明

测试过程中出现过：

```text
ERROR codex_models_manager::manager:
failed to load models cache:
missing field `supports_parallel_tool_calls`
```

但后续 Codex 已正常返回：

```text
OK
```

因此这个错误与代理网络无关，更像是 Codex 更新后旧模型缓存格式不兼容。

如需清理，可先备份：

```bash
cp /root/.codex/models_cache.json \
   /root/.codex/models_cache.json.bak
```

然后删除：

```bash
rm /root/.codex/models_cache.json
```

重新运行 Codex 后缓存通常会自动生成。

不要删除：

```text
/root/.codex/auth.json
/root/.codex/config.toml
/root/.codex/sessions
```

等正常配置与会话文件。

---

## 10. 最终结构

```text
Mac 本地 Codex
       │
       │ SSH
       ▼
远程服务器
       │
       ▼
/opt/nodejs/bin/codex
       │
       ▼
读取 /root/.codex/.env
       │
       ▼
HTTP_PROXY / HTTPS_PROXY
       │
       ▼
127.0.0.1:7890
       │
       ▼
服务器代理服务
       │
       ▼
OpenAI / ChatGPT
```

最终效果：

```text
SSH                ✅
远程文件访问        ✅
Codex CLI           ✅
Codex Remote        ✅
OpenAI 网络连接      ✅
无需手动 proxy_on    ✅
```

---

## 11. 核心文件备忘

Codex 可执行文件：

```text
/opt/nodejs/bin/codex
```

Codex Home：

```text
/root/.codex
```

Codex 代理配置：

```text
/root/.codex/.env
```

代理：

```text
http://127.0.0.1:7890
```

最关键的配置：

```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=127.0.0.1,localhost,::1
```
