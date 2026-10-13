# 立项调研：2026-10-09

GitHub 已有直接连接现有 DOT 与微信的项目，因此机会在于安装体验、明确的能力边界、可靠恢复与可复现的真人演示。不能按“没有竞争者”宣传。

| 项目 | 源码中的主要路线 | 对 wechat2dot 的意义 |
| --- | --- | --- |
| [cd233ljx/wechat-dot-bridge](https://github.com/cd233ljx/wechat-dot-bridge) | Python / Linux；iLink、持久收发、MCP Events、私人 Tunnel、媒体工具 | MIT 复用基础；上游作者有真人记录，但本项目仍需独立验收 |
| [RerrentLinden/dots-wechat-bridge](https://github.com/RerrentLinden/dots-wechat-bridge) | Python；签名事件、ack / final 回复阶段、恢复 | 参考阶段性回复与操作指引 |
| [xavierliang/wechat-dot-bridge](https://github.com/xavierliang/wechat-dot-bridge) | Node / Docker / OAuth；接入与验证分阶段 | 多人部署需要认证，首版选择单人私有模式 |
| [Ericwong5021/dots-wechat](https://github.com/Ericwong5021/dots-wechat) | 微信适配、DOT 事件与配置探索 | 不把未验收链路当稳定能力 |
| [Pluviobyte/dot2api](https://github.com/Pluviobyte/dot2api) | 事件任务队列、claim / complete，文字 API 入口 | 说明事件桥接路线可复用；不直接等于微信 / 多媒体方案 |
| [chenhg5/cc-connect](https://github.com/chenhg5/cc-connect) | 多种编码代理 / IM 平台、会话生命周期 | 同类生态；不是现有 OpenAI DOT 的等价替代 |

调研帮助识别协议与产品边界；当前实现设计见 [架构说明](ARCHITECTURE.md)。微信协议以 [官方 openclaw-weixin](https://github.com/Tencent/openclaw-weixin) 为依据。

实现路线：微信 iLink 收信落库；只向已核验的 OpenAI callback 发送消息 ID；现有 DOT 通过私人 Tunnel 使用 MCP 工具读正文、运行已授权任务、提交回复；本地 outbox 持久发送。此架构能承载广泛任务，但不能自动转移 DOT 原生审批、登录、电脑接管或文件内部路径。

调研是指定时间与版本的快照，项目实现会变化；对外发布前需重新核验事实。Star 数不作为可靠性证据。
