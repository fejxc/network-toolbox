# 实验室 HP LaserJet P1108 连接方式

更新时间：2026-09-17

## 一、网络拓扑

```text
MacBook Pro / Mac mini
        │ SMB 打印共享
        ▼
Windows 主机：192.168.0.105
        │ USB
        ▼
HP LaserJet Professional P1108
```

注意：`192.168.0.105` 是 Windows 主机（打印共享服务器）的地址，不是 P1108 自身的网络地址。P1108 本机只有 USB 2.0、无内置网络接口。[HP 官方规格](https://support.hp.com/cn-zh/product/product-specs/hp-laserjet-pro-p1108-printer/model/5099195)

## 二、Windows 共享信息

| 项目 | 值 |
|---|---|
| Windows 主机 | `192.168.0.105` |
| SMB 共享名 | `HP LaserJet Professional P1108` |
| SMB 地址 | `smb://192.168.0.105/HP%20LaserJet%20Professional%20P1108` |
| 认证 | `guest`，密码留空（若 Windows 禁止 Guest，则改用有权限的 Windows 账户） |
| Windows 到打印机 | USB |

## 三、MacBook Pro 配置

已验证队列：

| 项目 | 值 |
|---|---|
| 队列名 | `HP_P1108_192_168_0_105` |
| 设备 URI | `smb://192.168.0.105/HP%20LaserJet%20Professional%20P1108` |
| 驱动 | `HP LaserJet Professional P1100, 6.9` |

图形界面添加：

1. 打开“系统设置 → 打印机与扫描仪 → 添加打印机”。
2. 选择“Windows”标签，定位到 `192.168.0.105`。
3. 选择共享打印机 `HP LaserJet Professional P1108`。
4. 用户名填写 `guest`，密码留空。
5. “使用”选择 `HP LaserJet Professional P1100`，然后添加。

命令行添加（需要本机管理员权限）：

```bash
sudo lpadmin \
  -p HP_P1108_192_168_0_105 \
  -E \
  -v 'smb://guest@192.168.0.105/HP%20LaserJet%20Professional%20P1108' \
  -P /Library/Printers/PPDs/Contents/Resources/hp1100.ppd.gz
```

## 四、Mac mini 配置

Mac mini 局域网地址是 `192.168.0.36`，也连接到同一个 Windows SMB 共享。已配置相同队列：

```text
HP_P1108_192_168_0_105
```

查看状态：

```bash
lpstat -p HP_P1108_192_168_0_105
lpstat -v HP_P1108_192_168_0_105
```

## 五、打印测试

```bash
lp -d HP_P1108_192_168_0_105 /usr/share/cups/data/testprint
lpstat -W not-completed -o HP_P1108_192_168_0_105
lpstat -W completed -o HP_P1108_192_168_0_105
```

如果出现 `media-empty-warning`，说明打印机缺纸；放入 A4 纸后作业会继续。

## 六、排障要点

- 不要在“IP”标签中把 `192.168.0.105` 当成 IPP、LPD 或 9100 打印机添加；这里使用的是 SMB 共享。
- 如果找不到共享，先确认 Windows 主机开启“网络发现与文件和打印机共享”。
- 如果 Guest 被 Windows 禁止，使用 Windows 账户登录共享，不要把密码写入本文档。
- 如果打印乱码，检查“使用”是否选成 `HP LaserJet Professional P1100`，不要使用普通 PostScript/PCL 驱动。
- Mac 上的 P1108 驱动属于旧版 HP/Apple 驱动，现代 macOS 需要使用已修复版本；本文只记录连接方式，不保存安装包或管理员密码。
