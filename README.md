# wechat2dot

**在微信里给你已有的 OpenAI DOT 发任务，让它把进度和结果发回同一个微信会话。**

比如，给机器人发一个 CSV，再说「把金额加起来，把结果表发回这里」。你可以离开电脑，直接在手机上收结果。DOT 使用你已经授权的工具。桥接在你自己的电脑上运行。

[English](docs/README_EN.md) · [手动安装](docs/GETTING_STARTED.md) · [已有安装的升级与恢复](docs/RELIABILITY.md#maintenance) · [排错](#troubleshooting)

## 先选你要的能力

| 你想做什么 | 从哪里开始 |
| --- | --- |
| 在微信里发文字、图片或文件，让 DOT 回复 | 选 Alpha 2 发布版。微信发送第一条任务即可验收。[安装指南](docs/GETTING_STARTED.md) |
| 让 DOT 每天主动把简报发到本人微信 | 选含主动通知的固定源码版本。先接通微信，再启用通知，并在 DOT 创建会调用通知工具的定时任务。[版本与安装命令](docs/GETTING_STARTED.md) · [主动通知](docs/NOTIFICATIONS.md) |
| 将手机、Mac、云端所有 DOT 对话自动转发到微信 | 这超出本仓库的能力。普通回复留在原会话，只有指定了微信出口的任务会主动往微信发。 |

## 快速开始

准备一台能保持在线的 Windows、macOS 或 Linux 电脑、本人微信，以及有私人插件、MCP Events 和 Tunnel 权限的 DOT 账号。先确认账号能打开 [ChatGPT 插件](https://chatgpt.com/plugins) 和 [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)。如果有旧桥接在使用同一微信绑定，先按[迁移与恢复指南](docs/RELIABILITY.md)安排切换。

### 让 Agent 帮你装

把下面这段话交给能在目标电脑执行命令的 Codex、Claude Code 或 Cursor。需要主动提醒时，在末尾加一句「我也需要定时主动通知」。

```text
请帮我安装 https://github.com/AndreLYL/wechat2dot，连接我已有的 DOT。
先检查系统、Git、uv 和现有安装。已有配置就从当前步骤继续，保留绑定和消息记录。
按仓库首页的逐步安装指南选择版本，装好依赖和官方 tunnel-client，准备本机配置。
需要我操作时，请告诉我打开哪个页面、选哪个字段。我会在本机隐藏输入框保存 Tunnel 运行密钥，并用本人微信扫码。
帮我接通私人插件、核验回调主机并确认事件订阅生效。等我从微信发出一条测试并收到结果，再配置后台运行。
如果我要求主动提醒，再验证一条从 DOT 发起、手机微信实际收到的提醒。
```

### 自己装

按[完整安装指南](docs/GETTING_STARTED.md)操作。Windows PowerShell 和 macOS/Linux 的命令都有示例。你会看到三个关键结果：

1. **微信绑定成功。** 本人扫码后，本机显示 `bound: true`。
2. **DOT 能接到微信任务。** 在原 DOT 调用 `get_weixin_status`，确认 `enabled: true` 且 `events.active_subscriptions` 为 `1`。
3. **手机收到结果。** 在微信给机器人发「接入测试 W2D-HELLO：计算 19×23，回复结果和测试标记」。在同一个微信会话看到 **437** 和 **W2D-HELLO**。再按[后台运行指南](docs/RELIABILITY.md)完成常驻，并关掉联调终端复测。

## 卡住时先看哪一步

<a id="troubleshooting"></a>

| 你看到的现象 | 下一步 |
| --- | --- |
| 密钥已经保存，Tunnel 却提示 401 | 确认 key 属于该 Tunnel 的组织，有 Tunnels Read + Use 权限，并检查有效期。`key_saved` 只确认保存成功。[密钥与配置](docs/CONNECT_DOT.md) |
| 微信已扫码，发消息没人回 | 用 DOT 查询启用状态和有效订阅数，再检查电脑是否在线、处理任务是否启用。[已绑定但没有回复](docs/CONNECT_DOT.md#已绑定但没有回复) |
| 微信能聊天，定时简报没来 | 先看 DOT 任务是否执行、是否调用通知工具，再查这一次通知的状态。`context_required` 表示需要你在微信发一条新消息。[主动通知状态](docs/NOTIFICATIONS.md) |
| 一条提醒显示 `failed / rate_limited` | 该次发送已结束。这个标签可能来自 HTTP 429，也可能来自微信业务返回码。含新版恢复逻辑的部署会分别显示 HTTP 429 退避和等待新微信上下文。[如何区分](docs/NOTIFICATIONS.md#send-errors) |
| 显示 `already_running` 或发送状态 `uncertain` | 前者表示另一个进程持锁，后者需要先在微信核对那条消息。具体步骤见[排错表](docs/GETTING_STARTED.md#troubleshooting)。 |

Windows 私人部署已验收过文字、图片、CSV 和微信语音转写。各平台及媒体类型的验证范围见[验收记录](docs/VALIDATION.md)和[能力矩阵](docs/CAPABILITIES.md)。

[配置说明](docs/CONNECT_DOT.md) · [Docker](docs/DOCKER.md) · [架构](docs/ARCHITECTURE.md) · [贡献](CONTRIBUTING.md) · [MIT 许可证](LICENSE)
