# Contributing

欢迎中文或英文 issue / PR。优先处理安装失败、真人验收和可复现的消息问题。请先检查 [能力边界](docs/CAPABILITIES.md)，反馈只包含固定错误类别和脱敏状态，不上传运行目录或原始日志。

Linux / Python 3.11+：

```sh
uv sync --frozen --extra setup
uv run python -m unittest discover -s tests -v
uv run python -m unittest discover -s prototype_tests -v
uv run ruff check wechat2dot wechat_dot_bridge prototype_tests tests
uv run wechat2dot demo
uv run python -m build --no-isolation
```

可选媒体测试使用 Node 24：`npm ci --ignore-scripts --prefix wechat_dot_bridge/codecs`；FFmpeg 设置 `WECHAT_BRIDGE_FFMPEG` 为实际二进制路径。CI 安装这些依赖。没有编解码器不应影响基础文字收发。

Windows 只运行 `prototype_tests` 的可移植部分以及 `demo --preview`；完整引擎需要 Linux。新增测试验证实际行为，例如去重、恢复和配置拒绝，避免只重复实现。

保留 MIT 与第三方 notices；模块重命名、迁移、协议或媒体改动必须兼顾持久状态兼容。不要把 `uncertain` 发送改成自动重试。公共仓库只放示例和合成 fixture。
