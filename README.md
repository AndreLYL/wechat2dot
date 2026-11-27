# wechat2dot

**把自己的微信接到已有的 DOT 主对话。** 你在手机上问娜娜一句，在 Mac 上继续聊，或者娜娜在云端发来一条续费提醒，只要它写入同一个主对话，桥接就会把新内容送到你的微信。在微信里直接回复，ChatGPT 主对话也能看到你发的话。

[English](docs/README_EN.md) · [原生运行、排错与迁移](docs/NATIVE.md) · [Docker](docs/DOCKER.md) · [0.2 官方模式](docs/GETTING_STARTED.md)

## 第一次使用

**2.0.0a2 是预发布版。** macOS / Linux 可从终端直接安装：

```sh
curl -fsSL https://raw.githubusercontent.com/AndreLYL/wechat2dot/main/install.sh | sh
```

Windows PowerShell 可下载并运行同一仓库的安装程序：

```powershell
$installer = Join-Path $env:TEMP 'wechat2dot-install.ps1'
Invoke-WebRequest 'https://raw.githubusercontent.com/AndreLYL/wechat2dot/main/install.ps1' -OutFile $installer
& $installer
```

安装程序使用 GitHub Release 的固定版本 wheel。已有源码检出时，也可使用下表中的本地命令。需要能持续在线的电脑、本人微信可绑定的 ClawBot，以及已经在 ChatGPT 打开过的 DOT 主对话。

| 系统 | 在仓库目录执行 |
| --- | --- |
| macOS、Linux | `sh ./install.sh` |
| Windows PowerShell | `.\install.ps1` |
| 已有 uv，想直接从检出目录运行 | `uv run --frozen wechat2dot setup` |
| NAS 或服务器上的 Docker | [容器首次配置](docs/DOCKER.md) |

1. 用本人微信扫终端或浏览器显示的二维码，在手机上确认。
2. 按向导打开 ChatGPT，用同一账号输入设备码。如果设备码登录被拒，在 ChatGPT 安全设置里开启设备代码登录。账号有多个 DOT 时，选择你想在微信收到消息的那个。
3. 在刚绑定的微信会话发送 `/status`。看到状态回复后，在手机或 Mac 的 **DOT 主对话** 让它「只回复 桥接验收-37」。打开微信，核对正文和条数。再从微信发一句，回到 ChatGPT 主对话核对自己的消息。

普通回复和写进主对话的云端提醒都走这条链路。新安装从绑定时开始。微信来信支持文字、图片、文件和微信提供的语音转写；文件上限 20 MiB。任务卡片显示开始和结束，审批需要你在微信里明确回复卡片上的命令。

## 微信回复排版

原生模式会为从 DOT 主对话转发到微信的普通回复自动排版。短纯文本照常发送，标题、列表、窄表格和代码块保留结构。宽表格按行展开成带字段名的条目，方便在手机上逐项读完。长回复优先沿段落或列表项分段，尽量让链接和代码块保持完整。

在 iPhone 微信中已实测宽表展开和代码块复制，复制的代码保留换行、缩进和字面符号。

此功能已在 `main` 源码版提供。上面的安装脚本使用 GitHub Release 的固定版本。体验源码版可使用上表中的 `uv run --frozen wechat2dot setup`。

## 微信暂时收不到时

先运行 `wechat2dot status`。看到 `waiting_context`，在机器人会话里发 `/ping` 即可刷新；机器人会先发积压摘要，再按顺序补发保留期内的内容。`/ping` 本身没有文字回执。看到 `uncertain`，请先核对用同一标记发送的那条消息。桥接保留这次记录，避免自动发送第二份。

Mac 自动保活会请求辅助功能权限，只在微信已运行、电脑空闲且屏幕解锁时向已绑定的机器人发 `/ping`。可以用 `setup --no-auto-ping` 关闭。其他平台也支持来信后补发。[状态、窗口与恢复](docs/NATIVE.md)

## 账号与隐私

原生模式使用 ChatGPT 的非公开房间接口及设备码登录。用户授权的登录凭据权限大于单个房间，接口或使用条款可能变化。桥接固定到你选择的账号和主房间，凭据默认存于系统钥匙串；Docker 使用私有卷。待发消息和附件存放在本机私有数据目录。希望使用 MCP Events 和私人 Tunnel 的用户可选择官方模式。[官方模式接入](docs/GETTING_STARTED.md)

采用 MIT 许可证。微信协议、原生房间协议和可选编解码器的来源见[第三方声明](NOTICE.txt)。
