# 国考职位人数监控

监控报名系统（bm.scs.gov.cn，当前为 2027 年度国考）里**收藏职位**的
**待审查人数 / 审查通过人数**，以及个人中心的**资格审查状态**，
变化时同时推送 **macOS 通知 + 钉钉机器人**。

支持**全自动续登**：Cookie 过期后自动重新登录。验证码识别为**纯 GLM 视觉方案**
（免费模型梯队 + 双采样自洽共识），识别失败随机睡眠换图重试，实测自动登录
综合成功率约 99.9%。

数据来自网页端"我的收藏"同一接口，登录加密（DES）、签名（HMAC-SHA256）
均按前端 JS 原样复刻，只读查询、频率很低。

## 文件说明

| 文件 | 作用 |
|---|---|
| `monitor.py` | 监控脚本（仅 Python 标准库） |
| `credentials.txt` | 账号（第 1 行）+ 密码（第 2 行），自动登录用（权限 600） |
| `cookie.txt` | 登录 Cookie（自动登录成功会自动更新，一般不用手动管） |
| `glm_key.txt` | 智谱开放平台 API key（自动登录的验证码识别用，权限 600） |
| `dingtalk.txt` | 钉钉机器人 webhook（第 1 行），权限 600 |
| `positions.txt` | 可选。填写职位代码则只监控这几个，删掉/留空则监控全部收藏 |
| `state.json` / `history.csv` | 上次数值与历史记录（自动生成） |
| `review_history.csv` | 资格审查状态变化记录，含审核说明，仅保存在本机（权限 600） |

## 钉钉推送

- 收到推送的事件：**收藏职位人数变化**、**资格审查信息变化**、**登录失效提醒**、**自动续登结果**
- 资格审查状态与网页个人中心一致：**待审核 / 审核通过 / 审核不通过 / 退回补充材料**。
  首次接入会推送当前状态；后续状态、审核说明、审核时间或报考职位变化时推送，
  包含前后状态、审核说明和精确到秒的采集时间。网络或接口异常不会覆盖已记录状态，
  钉钉发送失败会在后续查询中重试。审核监控不受收藏职位列表是否为空影响。
  不保存完整个人档案；状态文件和审核历史可能包含私密信息，均禁止提交到 Git。
- 人数变化正文包含单位、职位名称与代码、招考人数、待审查人数和审查通过人数，
  展示变化前后数值、增减值、未变化字段及两次采集时间；“统计中”不计算增减值。
- 首次成功采集会推送一次全部收藏职位的完整快照，时间为北京时间，精确到秒
  （`YYYY-MM-DD HH:MM:SS`）。推送成功后仅在人数变化时通知；可用
  `python3 monitor.py --initial` 手动再次推送完整快照。
- 机器人安全设置为"自定义关键词"，关键词是：**`职位人数监控`**
  （脚本所有推送正文都自带该前缀；更换关键词需同步改 monitor.py 顶部的
  `DINGTALK_KEYWORD`）
- webhook 存在 `dingtalk.txt`；换机器人只需替换该文件第 1 行

## 首次使用

1. **准备私密配置**：从示例复制配置文件，再填写实际内容。真实配置文件已由
   `.gitignore` 排除，不应提交到 Git。
   ```bash
   cp credentials.example.txt credentials.txt
   cp cookie.example.txt cookie.txt
   cp glm_key.example.txt glm_key.txt
   cp dingtalk.example.txt dingtalk.txt
   chmod 600 credentials.txt cookie.txt glm_key.txt dingtalk.txt
   ```
   `credentials.txt` 第 1 行填账号（身份证号或手机号），第 2 行填密码。
   `glm_key.txt` 填模型 API key，`dingtalk.txt` 填机器人 Webhook；
   自动登录会更新 `cookie.txt`，无需手动填写 Cookie。
2. **登录一次**（验证码全自动识别）：
   ```bash
   cd ~/Documents/zjlib-wifi/scs-position-monitor
   python3 monitor.py --login
   ```
3. **开始监控**（二选一）：
   ```bash
   python3 monitor.py --watch 300      # 终端常驻，每 5 分钟查一次
   ```
   或按下面"后台运行"配置 launchd/cron，只每次 `--once`。

## 自动续登是怎么工作的

- 发现 401（登录失效）→ 自动走网页登录流程：
  取验证码 → GLM 视觉识别（3 倍/4 倍放大图各一次，两次一致才提交；macOS 无 OpenCV 时使用系统 sips）
  → DES 加密账号密码提交 → 补 judge 步骤 → 新 Cookie 写回 `cookie.txt`
- 识别未达共识或免费模型拥堵 → **随机睡眠 3~8 秒换一张验证码重试**
  （验证码免费、错误不触发密码校验不锁账号；提交上限 5 次）
- 连续失败有 10 分钟冷却；终端运行时若提交连续错 2 次会弹图请你人工确认
- 免费模型梯队：glm-4.6v-flash → glm-4v-flash → glm-4.1v-thinking-flash

## 后台运行（可选）

### 方式 A：launchd（推荐，开机可用）

macOS 后台运行目录为 `~/.local/share/scs-position-monitor`（权限 `700`），
将脚本及配置文件放在该目录，避免 macOS 对后台访问“文稿”目录的隐私限制。
`Documents/zjlib-wifi/scs-position-monitor` 保留部署源码副本；运行时更新的
Cookie、`state.json`、`history.csv` 和 `monitor.log` 均位于实际运行目录。
LaunchAgent 在用户登录后加载，每 1 分钟查询一次。

存为 `~/Library/LaunchAgents/com.scs.monitor.plist`（每 1 分钟一次）。
将下面的 `YOUR_USERNAME` 替换为 macOS 用户短名称；plist 中的路径必须是绝对路径。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.scs.monitor</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string>
    <string>/Users/YOUR_USERNAME/.local/share/scs-position-monitor/monitor.py</string>
    <string>--once</string><string>--quiet</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/Users/YOUR_USERNAME/.local/share/scs-position-monitor/monitor.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_USERNAME/.local/share/scs-position-monitor/monitor.log</string>
</dict></plist>
```

```bash
launchctl load   ~/Library/LaunchAgents/com.scs.monitor.plist   # 启用
launchctl unload ~/Library/LaunchAgents/com.scs.monitor.plist   # 停用
```

### 方式 B：crontab

```bash
crontab -e
* * * * * /usr/bin/python3 "$HOME/.local/share/scs-position-monitor/monitor.py" --once --quiet >> "$HOME/.local/share/scs-position-monitor/monitor.log" 2>&1
```

## 输出含义

- **待审查人数**（waitnum）：已报名未通过资格初审
- **审查通过人数**（passnum）：资格初审通过
- "统计中"= 官方尚未统计（接口返回 -1）
- 变化明细同时写入 `history.csv`
- 报名截止后，收藏人数接口可能返回“报考已结束”；监控会停止更新收藏人数，继续独立查询资格审查状态。

## 常见问题

- **提示自动登录失败**：连续 5 次提交都没通过或模型长时间拥堵。终端跑一次
  `python3 monitor.py --login`（会弹图人工确认），或在钉钉群看失败通知。
- **钉钉收不到消息**：检查机器人安全设置的关键词是否为 `职位人数监控`；
  钉钉机器人限流 20 条/分钟（本脚本推送频率远低于此）。
- **密码改了**：更新 `credentials.txt`；脚本 10 分钟冷却后会自动用新密码。
- **安全**：`credentials.txt`、`glm_key.txt`、`dingtalk.txt`、`cookie.txt`
  均为敏感凭证，已设为仅本人可读（600），请勿外传、勿提交到 git。
- **查询频率**：Mac mini 当前每 60 秒查询一次；请求失败自动退出，由定时任务下个周期再试。若出现限流，需延长查询间隔；自动登录失败仍保留 10 分钟冷却。
