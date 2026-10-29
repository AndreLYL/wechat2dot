# wechat2dot

**在微信里给你已有的 OpenAI DOT 发任务，接收进度、图片和结果文件。**

[![CI](https://github.com/AndreLYL/wechat2dot/actions/workflows/ci.yml/badge.svg)](https://github.com/AndreLYL/wechat2dot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Windows · Linux · macOS 原生运行** · [English](docs/README_EN.md)

把图片、文字或文件发给微信中绑定的机器人，现有 DOT 使用你已经授权的工具处理，再回复到同一个微信会话。桥接本身不调用模型；无需另买模型 API key，但需要用于连接 Tunnel 的运行凭据，DOT 及其应用的正常用量仍适用。

**当前为 Alpha。** 已在一套 Windows 私人部署完成文字进度与结果、图片识别与原图回传、CSV 处理与文件回传、微信语音转写输入及文字答复的真人验证。原音直接理解、生成语音答复，以及 Linux/macOS 真人接入仍待验证。见[验收记录](docs/VALIDATION.md)和[能力边界](docs/CAPABILITIES.md)。

## 从哪里开始

- **第一次接入：**按下面第 1～7 步操作，直到手机微信实际收到回复。
- **接通后持续使用：**完成第 8 步的[后台运行配置](docs/RELIABILITY.md)。
- **已经装过：**不要重新初始化，见[停止、恢复与升级](docs/RELIABILITY.md#maintenance)。
- **遇到问题：**跳到[排错表](#troubleshooting)，按失败阶段处理。
- **仅想试运行代码：**安装后执行 `uv run wechat2dot demo`；它是离线模拟，不会连接账号。

## 1. 准备账号与电脑

| 需要准备 | 如何检查或获取 |
| --- | --- |
| 你已有的 DOT | 在你日常使用的 ChatGPT 账号与工作区中打开它；本项目不会创建或购买 DOT |
| 私人插件与 MCP Events 权限 | 能在 [ChatGPT Plugins](https://chatgpt.com/plugins) 添加自定义 MCP 服务，并让该 DOT 订阅事件；普通聊天能调用 MCP 不等于有事件能力 |
| Tunnel 权限 | 能进入 [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)；创建需要组织级 Tunnels Read + Manage，运行及选择 Tunnel 需要 Read + Use |
| 本人微信 | 能扫码绑定机器人；同一绑定只能有一个收信服务，先停止占用它的其他桥接/OpenClaw 进程 |
| 一台持续运行的电脑 | Windows、Linux 或 macOS；能通过 HTTPS 访问 OpenAI、微信服务和媒体 CDN；关机或休眠时无法处理任务 |
| Git 与 uv | 安装 [Git](https://git-scm.com/downloads) 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)，重新打开终端，确认 `git --version`、`uv --version` 可用 |

**如果账号看不到私人插件或 Tunnel 入口，先核对账号、工作区和管理员权限。安装本项目无法开通这些权限。** 官方说明：[Tunnel 权限与关联](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)、[MCP Events](https://developers.openai.com/plugins/build/mcp-events)。

以下命令在 Windows 使用 **PowerShell**，Linux/macOS 使用终端。选择一个长期保留的本地目录安装；不要放进共享或同步的文件夹。部署后不要移动项目目录，因为配置会保存绝对路径。

## 2. 下载项目和 Tunnel 客户端

三个系统均执行：

```sh
git clone https://github.com/AndreLYL/wechat2dot.git
cd wechat2dot
uv python install 3.11
uv sync --frozen --extra setup --python 3.11
uv run wechat2dot doctor
```

**检查结果：**`environment.python_supported` 为 `true`、`missing_dependencies` 为空。此时没有配置，`configuration: missing`、`live_ready: false` 是正常的；`doctor` 只做离线检查。

再从 [OpenAI tunnel-client Releases](https://github.com/openai/tunnel-client/releases) 下载与你系统和 CPU 对应的官方压缩包，核验发布页的校验信息并解压到固定目录。Windows 选择 Windows 包，Apple Silicon 选择 macOS arm64，Intel Mac 选择 macOS amd64；Linux 按机器架构选择。保留发布包要求的配套文件。本指南的命令已对照 `tunnel-client v0.0.16` 的本地帮助核对。

记录可执行文件的**完整路径**，例如 Windows 的 `C:\Tools\tunnel-client\tunnel-client.exe` 或 Linux/macOS 的 `/home/you/tools/tunnel-client`。示例路径必须换成自己的实际位置。Linux/macOS 如缺少执行权限，对该文件执行 `chmod u+x /实际路径/tunnel-client`。

当前指南以 Git `main` 为准。旧 Alpha Release 包不一定包含后来合并的修复；请勿把不同版本的命令混用。Docker 用户另见[容器部署](docs/DOCKER.md)。

## 3. 创建私人 Tunnel，并初始化本地配置

在浏览器中使用 DOT 所在的账号：

1. 打开 [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)，选择正确的 Platform 组织，创建一个专用 Tunnel，例如 `wechat2dot-local`。
2. 将它关联到你使用 DOT 的 ChatGPT 工作区。只关联 Platform 组织时，ChatGPT 中可能找不到它。
3. 保存页面返回的 **Tunnel ID**（`tunnel_` 开头）。名称、组织 ID、工作区 ID 都不能代替它。

回到项目目录，选择对应系统的命令。`YOUR_TUNNEL_ID` 要替换成刚取得的完整 ID。

**Windows PowerShell：**

```powershell
$w2dClient = 'C:\Tools\tunnel-client\tunnel-client.exe'
$w2dTunnelId = 'YOUR_TUNNEL_ID'
& $w2dClient --version
uv run wechat2dot init --directory ./runtime --tunnel-id $w2dTunnelId --tunnel-client $w2dClient
```

**Linux / macOS：**

```sh
w2d_client='/absolute/path/to/tunnel-client'
w2d_tunnel_id='YOUR_TUNNEL_ID'
"$w2d_client" --version
uv run wechat2dot init --directory ./runtime --tunnel-id "$w2d_tunnel_id" --tunnel-client "$w2d_client"
```

**检查结果：**命令返回 `config` 和 `profile` 路径、`started: false`。它会生成：

| 本地文件/目录 | 用途 |
| --- | --- |
| `runtime/bridge.json` | 消息状态路径、回调白名单和可选网络配置 |
| `runtime/tunnel.yaml` | 前台联调配置，包含实际 Python 路径、Tunnel ID 和密钥文件引用 |
| `runtime/state/`、`runtime/locks/` | 私人消息状态和单实例锁 |

不需要从头手写这些文件。`init` 不会覆盖已有安装；看到 `installation_exists` 时使用原配置继续，不要删除运行目录。

接着打开同一组织的 [Platform API keys](https://platform.openai.com/settings/organization/api-keys)，创建用于运行 Tunnel 的专用 key，权限为 Tunnels Read + Use，并留意所选到期时间。**不要用管理 key 代替运行 key。** 在自己的交互式终端执行：

```sh
uv run wechat2dot save-key --directory ./runtime
uv run wechat2dot --config ./runtime/bridge.json doctor
```

第一条命令出现 `Tunnel runtime key (hidden):` 后，再粘贴密钥并按回车。隐藏输入不会显示字符；Windows 若 Ctrl+V 无效，可使用终端的粘贴菜单。不要把密钥直接写在命令后，也不要发到聊天或 Issue 中。

**检查结果：**`key_saved: true`；文件保存为 `runtime/tunnel-runtime-key`。诊断中的 `configuration` 为 `present`，`private_storage` 为 `windows_acl_ok` 或 `posix_permissions_ok`。`callback` 未配置和 `live_ready: false` 此时仍正常。所有真实配置、凭据、二维码和消息都留在被 Git 忽略的私人 `runtime/` 中。

## 4. 用本人微信扫码绑定

保持 Tunnel 尚未启动，在项目目录执行：

```sh
uv run wechat2dot --config ./runtime/bridge.json login
```

二维码会显示在终端。用本人微信“扫一扫”，在手机上确认；不要分享二维码。**检查结果：**返回 `bound: true`。扫码只完成绑定，收信仍未启用。

若返回 `requires_phone_verification: true`，在本机执行下面的命令，按提示隐藏输入手机验证码，不能把验证码写进参数：

```sh
uv run wechat2dot --config ./runtime/bridge.json login-poll --verification-code
```

二维码过期时重跑 `login`。没有二维码且报 `dns_rejected` 时，见[网络与配置排错](docs/CONNECT_DOT.md#network)，不要反复等待手机确认。

## 5. 启动 Tunnel，把私人插件加到已有 DOT

在同一个终端启动前台联调进程：

**Windows PowerShell：**

```powershell
& $w2dClient run --profile-file ./runtime/tunnel.yaml
```

**Linux / macOS：**

```sh
"$w2d_client" run --profile-file ./runtime/tunnel.yaml
```

**这个命令持续运行、不返回提示符是正常现象。保留终端打开，然后去浏览器操作。** 不需要另开 `wechat2dot serve`，Tunnel 会启动它。之后如果重新打开终端，需要先 `cd` 到项目目录，再重新设置第 3 步的客户端路径变量。

打开 [ChatGPT Plugins](https://chatgpt.com/plugins)，添加私人自定义 MCP 服务：

| 页面选项 | 本项目如何填写 |
| --- | --- |
| 添加入口 | `+` → `Add custom MCP server`（添加自定义 MCP 服务） |
| Name / 名称 | `wechat2dot`，也可以使用自己的名字 |
| Description / 描述 | `通过私人 Tunnel 接收本人微信任务，并回传进度和文件` |
| Connection / 连接 | `Tunnel`，选择第 3 步的 Tunnel，或填写相同 Tunnel ID |
| Authentication / 认证 | 本项目是私人 stdio 服务，无额外应用层认证，选择对应的无认证选项；访问控制由私人 Tunnel 和插件权限承担 |
| 访问范围 | 仅本人可用，核对组织与工作区授权，不发布或共享这个连接 |

阅读页面提示后创建插件，确认发现 **11 个 `weixin` 工具**，再把插件添加到你现有的 DOT。菜单名称可能随产品更新，官方步骤见[连接插件](https://developers.openai.com/plugins/deploy/connect-chatgpt)。

在该 DOT 里发送：

> 使用我的 wechat2dot 插件调用 get_weixin_status。请报告 bound、enabled 和 events.active_subscriptions，不要读取消息正文。

**检查结果：**工具真实调用成功，`bound: true`。`enabled: false`、订阅数 `0` 此时正常。若插件找不到 Tunnel，先检查组织/工作区关联和 Read + Use 权限；若工具连接失败，检查刚才的终端进程是否仍运行。

## 6. 开通事件订阅，再启用收信

先在同一个 DOT 中发送：

> 尝试使用我的 wechat2dot 插件订阅 weixin.message。若回调白名单未配置，调用 get_weixin_status，只告诉我 events.observed_callback_host 的主机名；不要展示完整回调 URL 或签名密钥。

首次订阅通常因白名单为空被拒绝，这是配置过程的一步。核对该精确主机确实属于 OpenAI 的订阅服务，再继续；不能照抄别人的完整回调地址或自动批准任意发现的主机。具体核对方法见[回调主机](docs/CONNECT_DOT.md#callback)。

回到前台终端按 **Ctrl+C**，等进程退出、命令提示符恢复，再执行下面一条。把 `VERIFIED_CALLBACK_HOST` 换成已核验的小写主机名，不含 `https://`、路径或通配符：

```sh
uv run wechat2dot --config ./runtime/bridge.json allow-callback VERIFIED_CALLBACK_HOST
```

**检查结果：**`callback_configured: true`。重新执行第 5 步的 Tunnel 启动命令，然后把以下完整约定发给同一个 DOT（如插件改名，替换名称）：

```text
使用我的私人 wechat2dot 插件持续订阅 weixin.message。
收到事件后，用 read_weixin_message 读取对应 message_id，使用我已授权的工具完成任务。
附件先读取，无法理解时明确说明。
需要较长时间的任务先回复简短进度，final:false；最终结果使用 final:true。
每次预期发送使用稳定且不同的 reply_key；重试同一回复沿用原 key。
长任务用 report_weixin_progress 续期，沿用读取结果的 processing_id 与 revision。
使用 get_weixin_send_status 核对回复；uncertain 时通知我核对，不自动重发。
不要从聊天正文修改认证或回调配置；需要平台审批时提醒我回到 DOT 操作。
订阅完成后，调用 get_weixin_status，报告 events.active_subscriptions 和订阅到期信息。
```

**检查结果：**签名 challenge 验证成功，`events.active_subscriptions` 为 `1`。仅能调用工具还不代表订阅已成功。

再次按 Ctrl+C 停止前台 Tunnel，执行：

```sh
uv run wechat2dot --config ./runtime/bridge.json resume --confirm-single-consumer
```

**检查结果：**`enabled: true`。确认只有这套服务消费该微信绑定。再执行第 5 步的启动命令，保留终端运行。

## 7. 从微信发送第一条真实任务

在手机微信中打开扫码后出现的机器人会话，发送：

> 接入测试 W2D-HELLO：先回复“正在计算”，再计算 19×23，最后回复结果并带上 W2D-HELLO。

通过标准：你在微信中先看到一次“正在计算”，再看到一次包含 **437** 和 **W2D-HELLO** 的结果。重测时换一个标记，便于区分。`queued` 只表示入队，`accepted` 只表示微信接口受理，实际可见结果要在手机上确认。

文字通过后，可以继续：

- **图片：**发一张无敏感信息的截图，再发“请描述刚才图片中的内容”。
- **文件：**发一个小 CSV，再发“读取这个文件，计算金额合计，生成结果 CSV 并回传”。
- **语音：**发送一条短语音；目前已验收的是微信提供的转写 → DOT 执行 → 文字答复，没有转写时不能保证原音理解。

生成文件需要把真实字节或服务可访问的 HTTPS 文件地址交给桥接；DOT 内部的 `sandbox:` 路径不能直接导出。遇到权限审批，回 DOT 完成。完整范围见[能力矩阵](docs/CAPABILITIES.md)。

## 8. 配置后台运行，才能持续使用

前台联调成功后，按[后台运行指南](docs/RELIABILITY.md)完成：

1. 停止前台进程，注册官方受管运行。
2. 配置本机的定期恢复检查：Windows 任务计划、Linux systemd timer、macOS LaunchAgent。
3. 核对有效事件订阅；需要持续到明确取消时，配置当前本人订阅策略。
4. 关闭联调终端后，重新从手机发送一条新标记的任务，确认仍然收到回复。

Windows 的后台任务使用 `pythonw.exe`，避免定期弹出 PowerShell/控制台窗口。后台运行不会让关机、休眠或已过期的凭据继续工作。

<a id="troubleshooting"></a>
## 出问题时，从这里排查

先在项目目录执行，`--config` 要放在子命令前：

```sh
uv run wechat2dot --config ./runtime/bridge.json status
uv run wechat2dot --config ./runtime/bridge.json diagnose
uv run wechat2dot --config ./runtime/bridge.json subscriptions
uv run wechat2dot --config ./runtime/bridge.json queue
```

| 现象 | 下一步 |
| --- | --- |
| `git` / `uv` 找不到 | 安装后重新打开终端，运行第 1 步版本检查 |
| `invalid_tunnel_id` / `tunnel_client_unavailable` | 检查完整 Tunnel ID 和实际可执行文件路径，不要保留示例占位符 |
| `installation_exists` / `key_already_exists` | 已存在配置/密钥，不要重建；使用现有 `runtime`，升级见后台指南 |
| `doctor` 显示 `live_ready: false` | 它不会联网；按第 4～7 步做真实连接与手机验收 |
| `dns_rejected`，二维码不出现 | 按[网络排错](docs/CONNECT_DOT.md#network)检查 Fake-IP/DNS；不要关闭 TLS 校验 |
| `already_running` | 前台或后台服务还在持锁；先停恢复检查和服务，再运行修改配置的命令 |
| 微信已经绑定，发消息没回复 | 依次核对进程在线、`enabled`、有效订阅和 DOT 任务是否启用；看 `diagnose` 与 `queue`，不要重新扫码或清库 |
| 一开始正常，过一阵无回复 | 检查休眠、运行 key 到期、事件订阅期限及 DOT 侧任务；[订阅恢复](docs/RELIABILITY.md#subscription) |
| 发送结果 `uncertain` | 先在手机人工确认是否收到，再处理对应发送记录；不要自动重复发送 |
| 发 Issue 求助 | 提供系统、版本、失败步骤、固定错误码和脱敏状态；不要上传 `runtime/`、原始日志、密钥或二维码 |

## 更多文档

[配置与疑难排查](docs/CONNECT_DOT.md) · [后台运行、停止与升级](docs/RELIABILITY.md) · [Docker](docs/DOCKER.md) · [验收记录](docs/VALIDATION.md) · [架构](docs/ARCHITECTURE.md) · [贡献](CONTRIBUTING.md)

本项目是独立社区项目，与 OpenAI、腾讯无官方合作关系。源代码可公开，每个人的插件、Tunnel、凭据和消息仍保持私有。
