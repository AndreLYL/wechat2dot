# wechat2dot

**在微信里给你现有的 OpenAI dot 发任务，接收进度和结果。**

[![CI](https://github.com/AndreLYL/wechat2dot/actions/workflows/ci.yml/badge.svg)](https://github.com/AndreLYL/wechat2dot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

中文 · [English](docs/README_EN.md) · [连接 DOT](docs/CONNECT_DOT.md) · [能力边界](docs/CAPABILITIES.md) · [路线图](docs/ROADMAP.md)

> **Alpha 原型。** 已提供收发引擎、11 个 MCP 工具、事件订阅、安装入口和离线演示。wechat2dot 自己的真人微信 ↔ DOT 验收尚未完成；你的账号还需要具备私人插件、MCP Events 和 Secure MCP Tunnel 权限。

```mermaid
flowchart LR
    W[本人微信私聊] <-->|Tencent iLink| B[wechat2dot / 持久消息队列]
    B -->|签名事件：消息 ID| D[你现有的 dot]
    D -->|通过私人 Tunnel 读取 / 回复| B
    D --> T[dot 已授权的工具与应用]
```

微信入口复用现有 dot 的工具和权限。桥接不调用另一个模型，不要求模型 API key；Tunnel 使用自己的运行凭据，dot 的正常用量仍适用。项目与 OpenAI、腾讯没有官方合作关系。

## 先体验，无需账号

Python 3.11+，先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)：

```sh
git clone https://github.com/AndreLYL/wechat2dot.git
cd wechat2dot
uv sync --frozen --extra setup
uv run wechat2dot doctor
uv run wechat2dot demo
```

Linux 的 `demo` 使用真实消息存储、MCP 工具和事件签名核心，模拟微信及 DOT 的网络边界，检查进度、最终回复、去重与重启恢复。Windows / macOS 默认输出协议预览，明确标注 `engine_e2e: not_run`。两者都不扫码、不联网到微信或 DOT。

已安装 Docker 的 Windows / macOS 用户可以运行 Linux 演示：

```sh
docker build -t wechat2dot:local .
docker run --rm --network none wechat2dot:local demo
```

## 接上自己的 DOT

真实服务运行在 Linux 或 Linux 容器中。按 [接入指南](docs/CONNECT_DOT.md)依次核对权限、创建私人配置、扫码绑定、连接 Tunnel、订阅事件、恢复收信，然后完成微信端真人验收。Docker 路径见 [Docker 指南](docs/DOCKER.md)。

## 第一版能做什么

| 能力 | 实现与限制 |
| --- | --- |
| 文字任务 | 本人私聊收信 → 事件 → 原 dot 读取 → 回复；SQLite 持久队列 |
| 进度、长回复 | `final:false` 中间回复；最终结果；长文本分段与发送状态 |
| 图片、文件、视频 | 接收、读取、暂存、回传；每个附件最多 20 MiB |
| 语音 | 保留音频及微信提供的转写；没有转写时不自动声称识别成功 |
| 断线恢复 | 待处理任务、消息去重、失败状态；不确定发送需人工核对 |
| 工具能力 | 由原 dot 的授权决定；首次实测后才能确认具体应用可用 |

群聊、任意主动推送、微信内原生权限审批、电脑接管、完整历史迁移和语音通话暂未实现。生成文件可通过文本 / 分块字节 / 服务可访问的 HTTPS 地址回传；内部 `sandbox:` 路径不能直接导出。详见 [能力矩阵](docs/CAPABILITIES.md)。

## 开源基础与贡献

收发与 MCP 核心基于 MIT 项目 [cd233ljx/wechat-dot-bridge](https://github.com/cd233ljx/wechat-dot-bridge)，基线为 `67c726e15ae2de8b60d293d6f5e81107bd0b6ebc`。保留 `wechat_dot_bridge` 模块名及原测试以维护兼容性。wechat2dot 新增用户入口、环境诊断、协议 / 引擎演示、容器部署和面向贡献者的验证路线。协议与部分媒体处理来源于 [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin)。完整许可证见 [NOTICE.txt](NOTICE.txt)。

欢迎提交脱敏安装反馈和可复现修复，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。优先目标是让第一批用户真正接通；5,000 stars 是长期传播目标，进度取决于实际使用体验。我们不会把上游的实测记录当作本项目的实测证明。
