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

## 七、macOS 大于 15.0 的驱动安装问题

本实验室的 MacBook Pro 为 macOS 15.1.1（Apple Silicon）。Apple 官方 HP 驱动包中包含 P1108 所需的 `hp1100.ppd.gz` 和 `rastertozjs`，但安装器的 `Distribution` 文件把系统版本上限写成了 `15.0`。因此在 macOS 15.1 及更高版本上，原始安装器可能拒绝安装。[Apple HP 5.1.1 说明](https://support.apple.com/en-us/106385)

实际处理过程：

1. 从 Apple 官方 HP 驱动 DMG 中取出 `HewlettPackardPrinterDrivers.pkg`，并确认包签名为 Apple Software。
2. 将 `Distribution` 中的版本门槛从 `15.0` 调整为更高版本，生成修复版安装包。
3. 第一次安装还遇到 HP `preinstall` Perl 脚本的 `C.UTF-8` locale panic；将 `preinstall`、`postinstall` 及其 Perl 子脚本改为先使用 `LC_ALL=C`、`LANG=C`、`LC_CTYPE=C` 再执行，重新打包后安装成功。
4. 修复只改变安装器检查和脚本运行环境，驱动载荷仍来自 Apple 官方包；因此修复包本身显示为未签名是预期结果。

已验证的驱动文件：

```text
/Library/Printers/PPDs/Contents/Resources/hp1100.ppd.gz
/Library/Printers/hp/laserjet/p1100_1560_1600series/hpRaster.bundle/Contents/MacOS/rastertozjs
```

后续在 macOS 15.1 及更高版本重装时，应使用经过上述处理的安装包；不要把管理员密码、Cookie 或修复包提交到 Git。

## 八、最终修复包下载

最终修复包（约 584 MB）已上传到百度网盘：

[下载 HewlettPackardPrinterDrivers-P1108-fixed-locale.pkg](https://pan.baidu.com/s/1xhRA1yRT87wV62Hp_elvtg?pwd=8888)

提取码：`8888`

SHA-256：

```text
169a3a5d457713c76380eec485ac1918e6a68278e32aaaca3ad0c1251f9100b5
```

该文件是基于 Apple 官方 HP 驱动包生成的本地修复版，不要将安装包本体提交到 Git；Git 中只记录此下载链接和安装处理说明。
