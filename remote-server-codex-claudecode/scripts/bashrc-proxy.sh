# 追加到 /root/.bashrc（Codex / Claude Code 真正运行的容器内）：
#   cat bashrc-proxy.sh >> /root/.bashrc && source /root/.bashrc
#
# 用法：proxy_on 开启，proxy_off 关闭。
# 大小写代理变量同时设置：不同 CLI / Node.js 包 / Python 库支持不一致；
# 不使用 SOCKS5：7890 是 HTTP / Mixed 代理入口。

proxy_on() {
    export HTTP_PROXY=http://127.0.0.1:7890
    export HTTPS_PROXY=http://127.0.0.1:7890

    export http_proxy=http://127.0.0.1:7890
    export https_proxy=http://127.0.0.1:7890

    export NO_PROXY=localhost,127.0.0.1,::1
    export no_proxy=localhost,127.0.0.1,::1

    unset ALL_PROXY all_proxy

    echo "Proxy ON: http://127.0.0.1:7890"
}

proxy_off() {
    unset HTTP_PROXY HTTPS_PROXY
    unset http_proxy https_proxy
    unset ALL_PROXY all_proxy
    unset NO_PROXY no_proxy

    echo "Proxy OFF"
}
