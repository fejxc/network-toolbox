# 网络与远程开发配置工具库

个人网络与开发环境的配置留存与自动化脚本：认证、监控、远程访问、AI CLI 部署。

## 网络接入

- [浙江图书馆 Wi-Fi](zjlib/README.md)：Mac/Online Service 认证。
- [浙江理工大学校园网](zstu/README.md)：Mac ePortal、路由器版和掉线自动重登。
- [OpenWrt + WireGuard 远程回家 VPN](OpenWrt_WireGuard/README.md)：Mac/iPhone Full Tunnel 回家，由家中 OpenWrt + PassWall 分流。

## 监控与远程开发

- [MDPI 投稿监控](mdpi/README.md)：本地轮询 SUSY 投稿状态，状态变化时提醒。
- [远程服务器 Codex + Claude Code + SSH 反向代理](RemoteServer_Codex_ClaudeCode/README.md)：SSH RemoteForward 借用 Mac 代理，远程容器内 CLI 稳定出网。

## 安全约定

账号密码、API Key、私钥等配置文件均被 `.gitignore` 忽略或使用占位符，不要提交到 GitHub。
