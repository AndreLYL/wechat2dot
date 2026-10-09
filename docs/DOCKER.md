# Docker：在 Windows / macOS 运行 Linux 核心

需要已安装 Docker Engine / Docker Desktop 并启用 Linux 容器。项目不自动安装 Docker，不开放宿主机端口。

## 离线演示

```sh
docker build -t wechat2dot:local .
docker run --rm --network none wechat2dot:local demo
```

容器演示执行生产核心，但微信 / DOT 边界使用合成数据。`real_dot_connected:false` 是预期结果。镜像不带 Node、FFmpeg 或 SILK；可选转码缺失时音频回退为文件。

## 真实接入

先完成 [接入指南第 0 步](CONNECT_DOT.md) 的账号权限核对。从 [官方 Releases](https://github.com/openai/tunnel-client/releases/latest) 下载**适合容器架构的 Linux** tunnel-client，核对官方校验信息，放到仓库下 `tunnel/tunnel-client`。不要使用 Windows 的 .exe。Linux / macOS 宿主机需赋予该文件执行权限；Windows 若容器内不具备执行权限，则需要在 Linux 文件系统 / WSL 存放二进制后重新挂载。

以下命令在仓库目录执行，`YOUR_TUNNEL_ID` 必须替换为控制台返回值：

```sh
docker compose build
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway init --directory /data --tunnel-id YOUR_TUNNEL_ID --tunnel-client /tunnel/tunnel-client
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway save-key --directory /data
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway doctor
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway login
docker compose up -d
```

命名卷 `runtime` 保存 Linux 的 0700 / 0600 状态，不直接把 Windows 目录当作私人状态目录。镜像和 Compose 均以 UID 10001 运行。`save-key` 使用交互终端隐藏输入；不要通过环境变量或参数传 key。

此时收信仍暂停。在现有 DOT 连接私人插件、尝试订阅并核验回调主机后：

```sh
docker compose stop
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway allow-callback VERIFIED_HOSTNAME
docker compose up -d
```

在原 DOT 重新订阅并确认有效，然后启用收信：

```sh
docker compose stop
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway resume --confirm-single-consumer
docker compose up -d
```

继续做 [真人验收](VALIDATION.md)。只有微信端实际出现回复才算闭环。不要同时启动另一个消费者。

## 停止

在 DOT 取消订阅，然后：

```sh
docker compose stop
docker compose run --rm --entrypoint /app/.venv/bin/wechat2dot gateway pause
```

`docker compose down` 保留命名卷；不要随意使用 `down -v`，它会删除凭据、消息和去重状态。首次 alpha 的 CI 验证镜像构建与离线演示，不代表某台机器的 Docker/Tunnel/微信真实链路已接通。
