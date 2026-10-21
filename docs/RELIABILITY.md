# 运行稳定性与恢复

微信收信、Tunnel 连接、事件订阅和 DOT 执行是不同阶段。`enabled:true` 只表示允许收信，不证明进程仍在线；`accepted` 也不证明微信端已经显示回复。

## 使用受管进程

长期部署使用官方 `tunnel-client runtimes connect`，随后执行 `runtimes status ALIAS --json`，核对 `process_running`、`healthy`、`ready`。前台 `run` 适合临时联调，其终端退出后不能作为持续服务使用。

为受管进程建立一份仅本人可读的 `runtime/guard.json`。路径都使用绝对路径，密钥只允许文件引用，文件正文不能写进配置：

```json
{
  "alias": "wechat2dot-local",
  "tunnel_client": "/absolute/path/to/tunnel-client",
  "profile_dir": "/absolute/path/to/private/profiles",
  "profile_name": "wechat2dot-local",
  "tunnel_id": "tunnel_REPLACE_ME",
  "runtime_key_file": "/absolute/path/to/private/tunnel-runtime-key",
  "bridge_config": "/absolute/path/to/private/bridge.json",
  "mcp_command": "'/absolute/path/to/python' -m wechat2dot --config '/absolute/path/to/private/bridge.json' serve"
}
```

先用上述相同参数执行官方 `runtimes connect` 注册别名，再执行一次检查：

```sh
python -m wechat2dot.runtime_guard --settings /absolute/path/to/private/guard.json
```

守护检查通过官方进程身份机制复用存活进程，只在它已退出时重新连接，并在启动后重新检查健康状态。临时网络未就绪时不会强杀存活进程。检查不读写消息处理结果，不修改订阅，不重发回复；异常输出只保留固定错误类别。

检查结果保存在同一私人目录的 `last-runtime-check.json`，包含检查时间、进程与桥接健康、待办数量。有效订阅缺失时，不会仅凭 Tunnel 在线就报告整个桥接就绪。

可将这个单次检查交给系统服务管理器定期执行：Windows 使用本人登录会话下的任务计划，Linux 使用本人 systemd timer，macOS 使用本人 LaunchAgent。每分钟检查一次，并阻止同一检查并发运行。主机关机、休眠、退出用户会话或凭据过期仍会影响服务；这不是云端全天候托管。

## 订阅与待办

离线后让原 DOT 重新订阅 `weixin.message`，核对签名验证和有效订阅，再调用 `list_pending_weixin_messages` 检查尚未完成的来信。相同订阅恢复保留旧事件身份，只恢复未完成消息；完成的处理和已受理回复不重新播放。

如果希望当前本人订阅持续到明确取消，可在停掉受管运行与守护检查后执行：

```sh
python -m wechat2dot --config /absolute/path/to/private/bridge.json subscription-forever
```

它只设置当前绑定和当前回调的本地订阅策略，不扩大插件访问范围，不保证平台任务永久运行；取消订阅会清除此策略。仍需通过原 DOT 的状态读取和真实来信核验平台侧任务。恢复受管运行后再恢复守护检查。

## 网络中断与反馈

短暂 TLS EOF 等连接中断归入网络故障，事件使用稳定 ID、有界退避重试；证书校验错误、禁止的地址和重定向继续终止。达到重试上限后保留失败状态，通过原 DOT 的重新订阅和待办恢复处理，不能无限循环。

来信超过约一分钟仍未被 DOT 读取时，桥接最多发送一次等待状态，说明消息已保存。该状态不完成任务、不取消事件、不代表 DOT 执行失败；用户结果已入队、存在不确定发送或限流时不会额外催发。桥接完全离线时无法发出此提示。

发送状态为 `uncertain` 的回复必须由本人先在微信核对，不能自动重发。

依据：[官方客户端受管运行指南](https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md)、[MCP Events 订阅与恢复](https://developers.openai.com/plugins/build/mcp-events)。
