# Codex CLI 升级指南（npm 全局安装版）

## 1. 当前环境确认

当前 Codex CLI 为 npm 全局安装版本。

Codex 可执行文件：

```text
/opt/nodejs/bin/codex
```

软链接指向：

```text
../lib/node_modules/@openai/codex/bin/codex.js
```

实际文件：

```text
/opt/node-v24.15.0-linux-x64/lib/node_modules/@openai/codex/bin/codex.js
```

npm 全局安装前缀：

```text
/opt/node-v24.15.0-linux-x64
```

npm 全局模块目录：

```text
/opt/node-v24.15.0-linux-x64/lib/node_modules
```

当前 Codex 版本：

```text
codex-cli 0.147.0
```

---

## 2. 升级前先开启服务器代理

服务器访问外网需要先执行：

```bash
proxy_on
```

当前代理：

```text
http://127.0.0.1:7890
```

如果不先开启代理，npm 可能无法正常访问外网。

---

## 3. 查看当前 Codex 版本

执行：

```bash
codex --version
```

也可以确认当前实际调用位置：

```bash
command -v codex
```

正常应返回：

```text
/opt/nodejs/bin/codex
```

---

## 4. 查看 npm 最新版本

执行：

```bash
proxy_on
npm view @openai/codex version
```

用于查看 npm 仓库中的最新 Codex CLI 版本。

---

## 5. 正式升级 Codex CLI

推荐执行：

```bash
proxy_on
npm install -g @openai/codex@latest
```

升级完成后刷新 Shell 的命令缓存：

```bash
hash -r
```

然后检查：

```bash
command -v codex
codex --version
```

正常情况下，安装路径应该仍然是：

```text
/opt/nodejs/bin/codex
```

只是版本号更新为最新版。

---

## 6. 一键升级命令

日后如果只是想快速升级，可以直接执行：

```bash
proxy_on && \
npm install -g @openai/codex@latest && \
hash -r && \
codex --version
```

这条命令会依次：

```text
开启代理
  ↓
升级 Codex
  ↓
刷新 Shell 缓存
  ↓
输出新版本
```

---

## 7. 完整推荐升级流程

如果希望升级时同时查看升级前后版本，推荐：

```bash
proxy_on

echo "=== 当前版本 ==="
codex --version

echo "=== npm 最新版本 ==="
npm view @openai/codex version

echo "=== 开始升级 ==="
npm install -g @openai/codex@latest

hash -r

echo "=== 升级后 ==="
command -v codex
codex --version
```

---

## 8. 升级后 Remote SSH 的 Codex 进程：重连即可，`pkill` 只是备选

**不是每次升级后都必须执行 `pkill`。**

`pkill -f 'codex.*app-server' || true` 只是用来强制结束**升级前已经运行着的旧版远程 app-server**。

可以按这个规则记：

* 升级 Codex 时，Mac Codex **没有连接远程服务器** → 不需要 `pkill`
* 升级时 Remote SSH **正在连接 / 正在使用** → 建议先在 Mac 端断开再重新连接；通常这样就够了
* 重新连接后仍然异常、还在用旧进程、`waiting for network`、功能表现不对 → 再执行 `pkill`

所以正常升级可以简化为：

```bash
proxy_on && \
npm install -g @openai/codex@latest && \
hash -r && \
codex --version
```

升级成功后，Mac Codex 里：

```text
断开远程连接
    ↓
重新连接
```

**只有重新连接还不正常时**，服务器再执行：

```bash
pkill -f 'codex.*app-server' || true
```

而且在执行前可先确认是否存在旧进程：

```bash
ps -ef | grep '[c]odex.*app-server'
```

> `pkill` 会直接终止当前正在运行的 Codex app-server。如果服务器上还有正在执行的远程 Codex 任务，**不要随手执行**。

理解成两步走：

```text
正常升级：
升级 CLI → 断开 / 重连 Remote SSH

故障处理：
升级 CLI → 重连仍异常 → pkill app-server → 再重连
```

---

## 9. 如果出现 models cache 报错

升级前后可能看到类似：

```text
ERROR codex_models_manager::manager:
failed to load models cache:
missing field `supports_parallel_tool_calls`
```

如果 Codex 请求仍然可以正常执行，这通常不是网络问题，而是旧的模型缓存与新版本不完全兼容。

可以先备份：

```bash
cp /root/.codex/models_cache.json \
   /root/.codex/models_cache.json.bak
```

然后删除旧缓存：

```bash
rm /root/.codex/models_cache.json
```

或者直接：

```bash
mv /root/.codex/models_cache.json \
   /root/.codex/models_cache.json.bak
```

再重新启动 Codex：

```bash
codex --version
codex exec --skip-git-repo-check "只回复 OK"
```

新的 `models_cache.json` 会重新生成。

不要删除：

```text
/root/.codex/auth.json
/root/.codex/config.toml
/root/.codex/sessions
```

等正常配置文件和会话数据。

---

## 10. 检查是否存在多个 Codex

当前服务器中可能同时存在：

```text
/opt/nodejs/bin/codex
/usr/local/bin/codex
```

可以检查：

```bash
echo "=== 默认 ==="
command -v codex
codex --version

echo "=== /opt ==="
/opt/nodejs/bin/codex --version

echo "=== /usr/local ==="
/usr/local/bin/codex --version
```

由于当前 `PATH` 中：

```text
/opt/nodejs/bin
```

位于：

```text
/usr/local/bin
```

之前，所以默认执行的仍然是：

```text
/opt/nodejs/bin/codex
```

如果 `/usr/local/bin/codex` 版本较旧，也不需要急着删除，只要确认默认 `command -v codex` 指向正确位置即可。

---

## 11. 升级不会影响 Codex 专用代理配置

之前已经为 Codex 配置：

```text
/root/.codex/.env
```

内容类似：

```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=127.0.0.1,localhost,::1

http_proxy=http://127.0.0.1:7890
https_proxy=http://127.0.0.1:7890
no_proxy=127.0.0.1,localhost,::1
```

升级：

```bash
npm install -g @openai/codex@latest
```

只会更新 Codex CLI 程序本身，不会删除：

```text
/root/.codex/.env
/root/.codex/config.toml
/root/.codex/auth.json
```

因此原来的 Remote SSH 代理配置可以继续使用。

---

## 12. 日后维护推荐

日常只需要记住以下几条：

### 查看版本

```bash
codex --version
```

### 查看最新版本

```bash
proxy_on
npm view @openai/codex version
```

### 升级

```bash
proxy_on
npm install -g @openai/codex@latest
hash -r
codex --version
```

### Remote SSH 升级后

升级后先在 Mac Codex 里断开再重新连接远程服务器即可；**只有重连后仍异常**（还在用旧进程、`waiting for network`、功能表现不对）才执行：

```bash
pkill -f 'codex.*app-server' || true
```

再重新连接服务器。

---

## 13. 当前关键路径备忘

Node：

```text
/opt/node-v24.15.0-linux-x64
```

npm 全局 prefix：

```text
/opt/node-v24.15.0-linux-x64
```

Codex CLI：

```text
/opt/nodejs/bin/codex
```

Codex 实际模块：

```text
/opt/node-v24.15.0-linux-x64/lib/node_modules/@openai/codex
```

Codex Home：

```text
/root/.codex
```

Codex 专用代理：

```text
/root/.codex/.env
```

服务器代理：

```text
http://127.0.0.1:7890
```

---

## 14. 最简结论

当前 Codex 是 npm 全局安装版。

以后升级直接执行：

```bash
proxy_on && \
npm install -g @openai/codex@latest && \
hash -r && \
codex --version
```

如果本地 Codex 正通过 Remote SSH 使用该服务器，升级完成后**断开再重新连接远程服务器**即可。

`pkill -f 'codex.*app-server'` 不是必做步骤，只是「重连后仍异常」时的故障处理手段；服务器上还有正在执行的远程 Codex 任务时不要随手执行（见 [8](#8-升级后-remote-ssh-的-codex-进程重连即可pkill-只是备选)）。
