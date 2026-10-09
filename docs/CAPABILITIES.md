# 能力与验收边界

“微信使用 DOT 的全部能力”是产品方向。目前接口承载消息和附件，实际应用执行由 DOT 自身工具、授权、任务状态决定。

| 功能 | 当前代码 | wechat2dot 真人验证 |
| --- | --- | --- |
| 本人私聊文字收发 | iLink 收信、SQLite、签名事件、MCP 回复 | 待验收 |
| 中间进度 / 最终结果 / 长文本 | final / reply_key、分段与状态 | 待验收 |
| 图片 / 文件 / 视频 | 入站下载、分块读、暂存与回传 | 待验收 |
| 语音 | 微信转写、原生音频、可选 SILK 转码 | 待验收 |
| 研究、文档、代码、应用操作 | 依赖现有 DOT 工具授权 | 按工具逐项验收 |
| 任意生成文件导出 | text_content、字节分块、可访问 HTTPS | 内部 sandbox 路径不能直接读取 |
| 取消本地处理 / 恢复待办 | 处理租期和待办列表 | 不等同于停止平台正在执行的任务 |
| 审批、验证码、账号登录、电脑接管 | 未实现微信中的原生控制 | 需要回 DOT 完成 |
| 群聊 / 新联系人 / 多租户 / 主动推送 | 未实现；本人已有来信路由 | 不支持 |
| 全量历史 / 微信语音通话 | 未实现 | 不支持 |

单次暂存最多 1 MiB，附件总上限 20 MiB；文本文件可直接 `text_content`。图片读取返回预览；视频传输不代表视频理解。未安装可选 Node/SILK/FFmpeg 时，相关音频用文件回退；Markdown 可退回纯文本。

事件只携带不透明消息 ID，DOT 通过私人 MCP 工具取正文。`queued` 表示已入队，`accepted` 表示腾讯接口受理，两者都不是用户看到了消息的证明。不确定的发送不自动重试。

工具名保留上游兼容命名：`get_weixin_status`、`read_weixin_message`、`reply_weixin_message`、`report_weixin_progress`、`get_weixin_send_status`、`list_pending_weixin_messages`、`read_weixin_attachment`、`stage_weixin_attachment`、`import_weixin_attachment`、`reply_weixin_attachment`、`retry_weixin_operation`。
