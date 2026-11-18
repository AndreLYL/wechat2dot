# Changelog

## 2.0.0a2

- 微信来信使用稳定的 UUID 作为房间请求标识。在真实主房间验证时，首版的 68 字符标识被接口拒绝。升级保留已尝试消息的原始标识用于对账。
- DNS、建立连接或获取登录凭据阶段失败时，保留来信并退避重试；可能已发送的请求继续核对。状态记录最近一次房间 POST 的 HTTP 状态。

## 2.0.0a1（本地预发布候选）

- 绑定一个已有 DOT 主房间，按成员识别助手消息，并把本人微信来信以本人身份送回该房间。
- 提供持久队列、消息对账、附件、任务状态、显式审批及上下文恢复。Mac 可选通过微信客户端发送保活消息。
- 新增原生安装、服务、迁移和通知专用 MCP 入口。0.2 的官方接入方式保留。见[原生运行指南](docs/NATIVE.md)。


## Unreleased

## 0.2.0a2 — 2026-10-31

- Replaced the overview-only README with a complete first-run guide: prerequisites, credentials, QR binding, private plugin setup, callback verification, event subscription and visible WeChat acceptance.
- Added copyable managed-runtime setup, Windows Task Scheduler settings, Linux systemd and macOS LaunchAgent examples, and maintenance/recovery instructions.
- Updated the current validation status: one Windows deployment has passed text, image, CSV and WeChat-transcription-to-text checks. Raw-audio understanding, generated voice replies and live Linux/macOS validation remain unverified.
- Includes the previously merged transient network recovery, waiting notice, runtime guard and hidden Windows console fixes. These changes were absent from 0.2.0a1 and are included in this release.

## 0.2.0a1

- Native Windows, Linux and macOS runtime with a shared OS boundary.
- Windows owner-only ACLs and handle checks; native process locks across platforms.
- Real engine demo on every platform; native stdio/EOF, login-state, media/backup and configuration tests.
- Product-focused documentation and optional Docker deployment.
- At this release, live WeChat / dot validation was still pending.

## 0.1.0a1

- Introduced iLink messaging, durable queues, media and MCP Events.
- Added wechat2dot onboarding CLI, hidden runtime-key entry, QR login, explicit callback approval and offline diagnostics.
- Added clearly labelled protocol preview and production-engine offline demo.
- Added container deployment, cross-platform onboarding checks and Linux engine CI.
- At this release, live validation of this project's deployment was still pending.
