# 原生桥运行指南

## 接入与核对

`wechat2dot setup` 会绑定本人的微信和一个已有的 DOT 主房间，在本机记录起点，安装用户服务，最后等待微信发来的 `/status` 及其发送回执。在手机上看到这条回复才算完成收件验收。同一主对话里的云端任务和各设备发起的对话共用房间消息 ID。只在其他房间出现的内容不在此绑定范围内。

安装脚本使用 uv；开发者可从本地目录运行 `uv run --frozen wechat2dot setup`。桌面使用系统凭据库；Linux 无钥匙串的服务器可显式选择 `setup --file-credentials --no-browser`。Docker 默认用私有卷。在 macOS 可用 `--bot-chat '会话的精确名称'` 指明 ClawBot 的会话标题，或者用 `--no-auto-ping` 关闭辅助功能操作。Mac 保活助手的编译需要 Xcode Command Line Tools。无法确认目标会话、存在未发草稿或屏幕锁定时会跳过。

向导的 `--no-service` 适用于前台试运行，随后执行 `wechat2dot run`，另开终端看 `wechat2dot status`。后台安装使用 launchd、systemd 用户服务或 Windows 登录计划任务。原生进程读取同一份 `config.toml`，状态保存在 `state/bridge.sqlite3`，运行期间每隔几秒更新 `status.json`。Mac 默认数据目录是 `~/Library/Application Support/wechat2dot`。可通过 `wechat2dot --home "/私有目录" setup` 指定另一位置；之后每条管理命令也要指定该位置。备份整个目录时使用私有存储，系统钥匙串内的凭据需另外保留或重新登录。

## 排错

| 现象 | 怎么确认或恢复 |
| --- | --- |
| 主对话里有回复，微信里没有 | 运行 `wechat2dot status --json`，核对 `last_dot_read`、`last_weixin_poll`、`outbox` 及历史。用 `wechat2dot doctor --live` 检查域名解析及对已绑定房间的一次实际读取。 |
| `waiting_context` | 在本人微信发一句话或 `/ping`。窗口默认约 24 小时，平台可能提前拒绝。先发积压摘要，再按顺序补发，默认保留 7 天。 |
| 微信限流 | 桥接尊重发送冷却。`ret=-2` 同时结合最近来信年龄诊断，年龄超过 1 小时会等待新上下文。JSON 中 `last_send` 可核对业务码和年龄。 |
| DOT 要求重新登录 | 运行 `wechat2dot login`，使用原账号。 |
| 微信登录失效 | 执行 `wechat2dot service stop`、`wechat2dot login --weixin`、`wechat2dot service start`，再从已绑定会话发送 `/status`。扫码身份须与原绑定一致。 |
| `dot_protocol_changed` | 查看 `status --json`，保留队列，升级到适配接口的版本后重启。微信本地 `/status` 仍可用。 |
| `uncertain` | 先到接收端核对。发往 DOT 的来信还会通过原 request ID 在房间中查证。无法确认时保留待核对记录，避免重复发送。长消息中结果不明的分段会单独列入 JSON 的 `pending_review`，后续确定未发的段继续发送。 |
| 多个本地进程抢同一账号 | 检查旧 guard、Tunnel 和其他主机上使用同一绑定的服务；完成交接后只运行一个收信进程。 |

`/pause` 和 `/resume` 暂停或恢复对话同步。`/help` 查看微信可用命令。只有原请求明确提供的选项会在审批卡上出现。服务发出的 `/ping` 要收到本人微信回传的新上下文才算成功。

## 从 0.2 迁移

1. 记下原安装的固定版本、服务名、Tunnel 及订阅配置。保留原私有运行目录。选择可中断收信的切换时段。
2. 停止旧微信消费者以及能把它再次拉起的 guard。Mac 旧部署还有 keepawake、message-mirror 和受管 Tunnel；逐个核对实际注册的服务。取消该 DOT 的旧 `weixin.message` 订阅。
3. 运行 `wechat2dot migrate --from ~/.local/share/wechat2dot/runtime`，再运行 `wechat2dot setup`。迁移复制 SQLite 快照、绑定、通知策略和去重记录；旧目录作为回滚起点。新进程持有旧消费者互斥锁。迁移后若旧库继续收发，需重新核对交接点。
4. 手机和 Mac 分别在主对话请求不同的短回复，从微信核对正文各出现一次。再从微信向主对话发一条，在 ChatGPT 核对自己的消息、在微信核对 DOT 的回复。验证云端任务时分别记录任务开始、结束、正文和手机收件。
5. 回滚前先停止 `wechat2dot service stop`，保存新队列及待核对记录，然后恢复旧订阅、旧 Tunnel 和旧服务。旧库的游标停在切换时，回滚时先核对切换区间的收发记录。两个库的运行后状态不会自动合并。

生产安装使用经过验收的固定标签。通过安装管理器安装时，`wechat2dot upgrade` 会在升级前备份数据库；pipx 或 Homebrew 安装分别加 `--manager pipx` 或 `--manager brew`。卸载服务用 `wechat2dot uninstall`，数据继续保留。`--purge` 会删除原生运行数据。

## 可选的通知插件与官方模式

云端任务正文已写进主房间时，原生桥直接同步。需要显式投递的任务可以另外配置私人 Tunnel，把 stdio 命令指向同一个安装的 `wechat2dot --home … notification-stdio`。这个进程只提供发送通知、查询状态和暂停通知三个工具。它负责把请求写入同一数据库，原生服务负责发送。前后 10 分钟内，普通镜像与已受理通知正文完全一致时去重；通知字段和幂等键沿用[通知指南](NOTIFICATIONS.md)。迁移保留原有通知开关。

采用完整旧版 MCP Events 路径时使用 `wechat2dot official --config /绝对路径/bridge.json status` 等命令，部署见[官方模式接入](GETTING_STARTED.md)。原生服务与官方消费者不能同时运行在同一绑定上。

## 分发和真机验收

当前预发布版为 `2.0.0a2`。安装脚本和 Homebrew 从 GitHub Release 下载。从源码可以构建 wheel、源码包及 Docker 镜像。发布工作流使用已存在的对应标签，构建、测试并分别发布至 PyPI、GHCR、GitHub Release 和 Homebrew tap。PyPI 需要账号配置 trusted publishing 并在仓库变量中开启 `PUBLISH_PYPI=true`。Homebrew 可由发布者将同一份校验过的 formula 推送至 tap，也可配置专用写入凭据。新安装的发布路径包括 `uv tool install 'wechat2dot==2.0.0a2'`、`pipx install 'wechat2dot==2.0.0a2'` 和 `brew install andrelyl/tap/wechat2dot`，以对应渠道实际出现版本为准。

本地证据见[候选版验收记录](NATIVE_VALIDATION.md)。上线验收还需记录两端可见的实际结果，尤其是 30 小时自动保活、关闭保活 26 小时后的补发、进程在发送期间退出、重启登录、10 分钟断网、首次安装计时，以及 7 天连续运行中每条消息的去向。接口受理与手机实际出现分别记录。
