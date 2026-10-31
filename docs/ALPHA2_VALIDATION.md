# Alpha 2 发布准备验证记录

日期：2026-10-31（北京时间）。基线：main `3addfa6ffa853a914912cd8740f0cc99a99e64e8`。
版本：`0.2.0a2` / `v0.2.0-alpha.2`。仅修改版本、文档、发布检查和 CI 发布流程，不修改消息运行行为。

## 本地实际执行

执行环境：Linux x86_64、Python 3.12.14、Node 24.22.1、uv 0.13.4。

- `uv sync --frozen --all-extras --python 3.12` 成功；锁文件仅更新本项目版本，不更新已锁定的第三方版本。
- `npm ci --ignore-scripts --prefix wechat_dot_bridge/codecs` 成功。指定 `WECHAT_BRIDGE_FFMPEG` 为 imageio-ffmpeg 0.6.0 提供的可执行文件，`WECHAT2DOT_REQUIRE_CODECS=1`。
- Linux engine suite：390 个测试通过，无跳过。首次未完成 codec/FFmpeg 环境配置的运行有 1 个转码错误；按 CI 配置环境后全部通过。
- native/onboarding suite：29 个测试，28 通过、1 跳过（Windows 隐藏控制台专属测试，本机无法执行）。
- `scripts/verify-upgrade.py`：升级、备份恢复、分片恢复和不确定发送保留检查通过，全部为合成状态。
- `wechat2dot demo`：生产核心离线 fixture 通过，0 网络请求，未连接真实 DOT 或微信。
- ruff、源码与文档链接检查、完整本地 Git 历史凭据扫描通过；无私密状态进入产物。
- `python -m build --no-isolation`：生成 sdist，再由 sdist 构建 wheel。
- 安装包检查核对版本、依赖元数据、运行模块、源码资源、全部文档、MIT 与上游许可证、wheel RECORD 及私密文件排除。
- 在独立 Python 3.12 环境安装 wheel 的 `[setup]` extra，允许解析包声明的依赖范围；`uv pip check`、从源码目录外执行 `doctor` / `demo` 和两个 CLI 入口检查通过。此检查覆盖 wheel 安装，不保证所有未来依赖组合。

本机无 Docker，也未运行 Windows/macOS。四个平台/Python 组合、Docker 和对应 fresh-wheel 检查由本次提交的 GitHub Actions 另行执行。发布工作依赖整个 engine 矩阵与 container job 全部成功，并将该次 CI 链接追加到 GitHub 发布说明。基线 main 的历史 CI 成功不能替代本版 CI。

## 真人验收

本轮发布准备未执行新的真人微信端到端验收。仅引用 docs/VALIDATION.md 中 2026-10-21 与 10-25 的 Windows 私人部署记录：文字、图片、CSV、微信转写输入及文字回复。Linux/macOS 真人接入、原音理解、生成语音回复、断线无重复发送、长文本与外部应用任务仍待验收。不得将单元测试、离线 fixture、API 接受或历史部署验收改写为本次安装包真人可用性证明。

## 发布机制

仅 main 的 push 提交消息含 `[publish-alpha2]` 时触发一次性发布 job；固定版本为 `v0.2.0-alpha.2`。必须与包版本一致，且发布提交位于 main 历史；使用 Linux 3.11 已检查的 CI 构建产物，不在发布 job 重新构建。先创建指向该次通过 CI 提交的新标签，再创建全新草稿、上传 wheel / sdist / SHA256SUMS，下载复核后改为公开 prerelease；不使用覆盖选项，不更改历史 Release，不标记稳定 Latest。若同名标签或 Release 已存在则创建失败并停止，留待人工检查。

最终下载校验值以 Release 的 SHA256SUMS 为准。本地构建与 CI 构建的 ZIP 时间戳等可能不同，不能用本地校验值验证另一组 CI 字节。
