# wechat2dot 0.2.0 Alpha 2

让你的 ChatGPT 助手住进微信。此版本基于 main 的 `3addfa6ffa853a914912cd8740f0cc99a99e64e8`，纳入 Alpha 1 之后的全部 12 个提交，并统一版本号、接入文档和发布资产检查。Python 包版本为 `0.2.0a2`，Git 标签为 `v0.2.0-alpha.2`。仍为预发布，不是稳定版。

## 本版变化

- 微信登录与轮询支持显式 HTTPS DNS 配置，改善 fake-IP 路由环境中的连接；仍执行 TLS 主机名和公共地址校验。
- 瞬时 TLS 断连按网络中断处理；证书验证失败仍停止，不弱化证书检查。
- 后台运行守护复用仍存活但网络未就绪的进程，只启动已停止的运行时；更新失败心跳和桥接就绪检查。
- 等待提示与 DOT 读取/进度回复原子协调；Windows 守护及后代控制台保持隐藏。
- 补齐首次接入、后台运行、停止恢复和升级说明。发送结果不确定时保留人工复核，不自动重放。
- 发布前核对版本、依赖元数据、全部运行模块、文档、许可证及 wheel RECORD；CI 全部通过后才创建新预发布，不覆盖历史资产。

## 下载与首次接入

首次接入推荐 [本版 Releases](https://github.com/AndreLYL/wechat2dot/releases/tag/v0.2.0-alpha.2) 中的 **`wechat2dot-0.2.0a2.tar.gz`**，核验同页 `SHA256SUMS` 后解压。该源码包包含 README、docs、scripts 和 uv.lock；不要将旧 Alpha 1 的包与新版文档混用。

也可以固定标签下载：

```sh
git clone --branch v0.2.0-alpha.2 --depth 1 https://github.com/AndreLYL/wechat2dot.git
cd wechat2dot
```

源码包解压进入 `wechat2dot-0.2.0a2`，或上述 Git 检出后，在该目录执行：

```sh
uv python install 3.11
uv sync --frozen --extra setup --python 3.11
uv run wechat2dot doctor
uv run wechat2dot demo
```

`doctor` 和 `demo` 均为离线检查；无配置时 `live_ready: false` 正常。继续按[同版本完整接入指南](https://github.com/AndreLYL/wechat2dot/blob/v0.2.0-alpha.2/README.md)或源码包内 README 第 3～8 步完成绑定、回调核验、事件订阅和手机实际收信。后台服务不应使用会重新解析项目的安装命令，按指南使用固定可执行文件和配置路径。

wheel 适用于已理解部署目录与配置的用户，可在独立 Python 环境安装下载的 `wechat2dot-0.2.0a2-py3-none-any.whl[setup]`。wheel 不包含完整文档或 scripts；依赖由安装器另行下载，不是离线整合包。尚未发布 PyPI，不要使用无法指定来源的同名安装命令。Node/SILK 与 FFmpeg 为可选本地转码依赖；官方 tunnel-client 是用户另行提供的程序，不捆绑在包内。

需要：本人现有 DOT；同账号/工作区可添加私人插件、订阅 MCP Events；可使用 Secure MCP Tunnel；创建 Tunnel 需要 Read + Manage，运行需要 Read + Use；本人微信扫码绑定；一台持续开机并能访问 OpenAI、微信与媒体服务的电脑。安装本项目不会开通账号权限。每个绑定只能运行一个收信服务。已有部署先按[维护指南](https://github.com/AndreLYL/wechat2dot/blob/v0.2.0-alpha.2/docs/RELIABILITY.md#maintenance)停止并备份，保留私人状态，不重新初始化。

## 验证证据与边界

| 证据层 | 结论与适用范围 |
| --- | --- |
| main 原提交 CI | `3addfa6` 的 [CI #25](https://github.com/AndreLYL/wechat2dot/actions/runs/40932828134) 已通过；这是发布准备前的提交，不替代本版 CI |
| 本版代码与安装包 | 必须通过本版发布提交的 CI，包含 Linux 3.11/3.14、Windows/macOS 3.12、Docker、构建和全新 wheel 安装离线检查；具体结果以本版 CI 运行链接为准 |
| 已有真人验收 | 2026-10-21、10-25 的一套 Windows 私人部署：文字进度与结果、图片识别及原图回传、CSV 处理及结果文件回传、微信语音转写输入后文字答复；详见[固定版本验收记录](https://github.com/AndreLYL/wechat2dot/blob/v0.2.0-alpha.2/docs/VALIDATION.md) |
| 尚未完成真人验收 | Linux/macOS 接入；原音直接理解；生成语音回复；断线恢复无重复发送；长文本；DOT 外部应用工具完成实际任务 |

历史真人验收不是对本次 wheel 的重新真人验收。离线转码成功不等于原音理解或语音回复成功。CI/fixture、API accepted、手机实际可见结果是不同证据；不据此承诺长期可用率、三平台真人接入或所有 DOT 界面能力均能从微信操作。原生审批与电脑控制仍需原界面。

## 许可证与校验

项目为 MIT，保留上游、Tencent 协议工作及 silk-wasm 的来源和许可证，见包内 LICENSE、NOTICE.txt、licenses。依赖单独安装并适用各自许可证；FFmpeg 不捆绑。社区项目，非 OpenAI 或腾讯官方产品。

Linux：`sha256sum -c SHA256SUMS`。macOS：`shasum -a 256 -c SHA256SUMS`。Windows PowerShell：`Get-FileHash .\wechat2dot-0.2.0a2.tar.gz -Algorithm SHA256`，与 SHA256SUMS 对比。校验值针对该 Release 实际上传的字节，不应混用其他构建的校验值。
