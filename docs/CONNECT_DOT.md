# 配置与疑难排查

第一次安装请从 [README 的第 1 步](../README.md#1-准备账号与电脑)开始。完整的下载、Tunnel、密钥、扫码、插件和第一条微信任务步骤都在首页；本文解释参数和失败时的处理方法。

## 配置参数从哪里来

| 参数/文件 | 来源与使用位置 |
| --- | --- |
| Tunnel ID | Platform Tunnels 创建后返回的 `tunnel_` 标识，传给 `init --tunnel-id`；不是显示名称 |
| Tunnel 客户端路径 | 从官方发布包解压后取得，传给 `init --tunnel-client`；使用绝对路径 |
| Tunnel 运行 key | 同一 Platform 组织中具备 Read + Use 的运行凭据，用 `save-key` 隐藏输入；不用管理 key |
| 私人插件名称 | 在 ChatGPT Plugins 创建时自定；给 DOT 的指令要用相同名字 |
| 回调主机 | 本人 DOT 实际订阅时提供，独立核验后用 `allow-callback` 写入；只填写精确主机名 |
| `runtime/bridge.json` | `init` 自动生成，供所有 `--config` 命令使用 |
| `runtime/tunnel.yaml` | `init` 自动生成的前台 Tunnel 配置；引用密钥文件而非密钥正文 |
| `runtime/guard.json` | 按[后台指南](RELIABILITY.md)生成，供定期恢复检查使用；不是前台 profile |

`init` 生成的最小 `bridge.json` 包含 `state_dir`、`lock_dir` 和 `callback_hosts`。其中目录已设为本机绝对路径。**不要用文档示例覆盖已有文件**，也不要加入模型 API key、Tunnel key 或完整回调 URL；这些不是该配置支持的字段。

修改配置前必须停止前台 Tunnel，或先暂停系统恢复检查再停止受管进程；具体命令见[维护步骤](RELIABILITY.md#maintenance)。保持文件仅本人可访问：Windows 为本人专有 ACL，Linux/macOS 为目录 0700、文件 0600。编辑器应保存为 UTF-8 无 BOM，并保留权限。

<a id="callback"></a>
## 回调主机如何核对

在你自己打开的 DOT 会话中发起订阅，再调用 `get_weixin_status`。初次订阅因白名单为空被拒绝时，`events.observed_callback_host` 会记录最近观察到的主机名，**它是待核验线索，不是自动信任结果**。

核对以下内容：

1. 订阅是你在官方 ChatGPT/DOT 界面主动发起的，目标是你刚连接的私人插件。
2. 在该次订阅的官方工具调用详情中核对目标主机，与状态返回值一致。只记录主机，不复制完整签名 URL。
3. 确认它属于 OpenAI 官方域名，且 HTTPS 证书对该主机有效。遇到不认识的域名、证书错误或无法核实时，先向工作区管理员或官方支持确认，保持白名单为空。

项目此前的私人部署观察到 `connectors.api.openai.com`。这只是历史实例，不保证每个账号都相同，也不能代替你对本次订阅的核验。核验后停止服务，执行：

```sh
uv run wechat2dot --config ./runtime/bridge.json allow-callback VERIFIED_CALLBACK_HOST
```

主机名不含 `https://`、端口、路径或 `*`。重启后在同一个 DOT 重新订阅；以签名 challenge 通过和 `events.active_subscriptions: 1` 为成功标准。不要自己创建回调 URL、签名密钥或订阅 ID。

<a id="network"></a>
## 二维码不出现，或出现 dns_rejected

`dns_rejected` 表示解析结果不符合公网地址限制。有些代理/TUN 会返回 `198.18.x.x` 等 Fake-IP；先检查本机 DNS、代理配置和网络可达性。

如果确认属于这种情况，而且本机能够访问 Google HTTPS DNS，可停止服务，在原来的 `bridge.json` 对象中加入这一项，保留其他字段：

```json
"weixin_dns": "google"
```

这是一个 JSON 字段片段，不是完整配置。相邻字段之间要有逗号，最后一项后面不要加逗号。可选值为 `system`（默认）和 `google`。

- `weixin_dns` 影响微信登录和消息传输。
- `media_dns` 影响附件下载/上传。
- `callback_dns` 影响向 DOT 发送事件回调。

只对实际失败的通道设置对应选项。固定 HTTPS DNS 只解析域名；二维码、验证码和微信凭据不会发给 DNS 服务。微信请求仍验证目标域名的 HTTPS 证书，不会自动切换、接受非公网地址或关闭 TLS 校验。

保存后先检查配置，再重试原步骤：

```sh
uv run wechat2dot --config ./runtime/bridge.json doctor
uv run wechat2dot --config ./runtime/bridge.json status
```

`doctor` 是离线检查，无法证明网络已恢复。`status` 会校验引擎配置并读取本地状态；需要以重新登录、附件传输或真实事件结果验证对应网络。二维码过期则在服务停止时重跑 `login`。

## 已绑定，但没有回复

用独立终端进入项目目录，执行：

```sh
uv run wechat2dot --config ./runtime/bridge.json status
uv run wechat2dot --config ./runtime/bridge.json diagnose
uv run wechat2dot --config ./runtime/bridge.json subscriptions
uv run wechat2dot --config ./runtime/bridge.json queue
```

| 检查 | 如何理解 | 下一步 |
| --- | --- | --- |
| 进程是否运行 | 本地 `enabled: true` 不证明进程活着 | 前台查看原终端；后台按[运行指南](RELIABILITY.md)读新鲜的心跳结果 |
| `bound` | 是否已绑定本人微信 | 为 false 时先完成扫码，不要使用别人的绑定文件 |
| `enabled` | 是否允许收信和发送 | 为 false 时停服务后执行 `resume --confirm-single-consumer`，再启动 |
| `events.active_subscriptions` | 是否有有效本地订阅 | 为 0 时回原 DOT 重新订阅；检查到期时间和 DOT 侧任务是否启用 |
| 待处理消息/诊断 | 来信可能已保存，但 DOT 尚未读取或处理 | 让原 DOT 用 `list_pending_weixin_messages` 检查未完成任务 |
| 发送队列 | `accepted`、失败、限流或 `uncertain` 是不同结果 | 先核对失败类别；`uncertain` 必须到手机查看，不能自动重发 |

来信超过约一分钟仍未被 DOT 读取时，桥接最多发送一次等待提示；这个提示不代表 DOT 已开始执行。桥接完全离线时无法发送它。

短暂网络中断会有界退避重试。证书验证失败等错误会终止；不要通过清库、改变回复 key 或放开认证来“恢复”。若任务需要登录或审批，要回 DOT 完成。

## 保存 key 或扫码遇到问题

- `key_already_exists`：密钥文件已经存在，`save-key` 不覆盖它。不要为了重跑教程删除有效凭据。到期换 key 见[维护](RELIABILITY.md#maintenance)。
- 隐藏输入看不到字符：正常。只在提示出现后粘贴，再按回车；不要把 key 当成 shell 命令执行。
- `requires_phone_verification`：执行 `uv run wechat2dot --config ./runtime/bridge.json login-poll --verification-code`，它会提示隐藏输入，无需在参数后写验证码。
- `already_running`：服务持有单实例锁。停掉前台/后台服务及其恢复检查后再修改，不要删除锁文件绕过检查。
- `unsafe_permissions` / `unsafe_owner`：使用安装时的同一用户和本地私人目录；不要用 `chmod 777` 或共享 ACL。恢复原有本人权限后再检查。

## 求助时提供什么

在 [GitHub Issues](https://github.com/AndreLYL/wechat2dot/issues) 提供系统/CPU 架构、`uv run wechat2dot --version`、客户端版本、对应教程步骤、固定错误码，以及你实际看到了什么。状态输出需自行检查脱敏后再粘贴。

不要上传 `runtime/`、数据库、原始客户端日志、二维码、手机号、密钥、验证码、完整回调 URL 或私人消息。不要把一次 API 受理或离线 demo 当成手机端已收到回复。
