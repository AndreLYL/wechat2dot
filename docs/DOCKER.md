# 用 Docker 常驻原生桥

需要 Docker Engine 或 Docker Desktop 的 Linux 容器。从 2.0 源码仓库目录执行。三个命令共用名为 `wechat2dot` 的卷，持有同一份登录凭据、绑定和队列。

```sh
docker compose build
docker compose run --rm bridge setup --no-browser
docker compose up -d
```

向导在终端显示微信二维码和设备码登录地址，分别用自己的微信和 ChatGPT 确认。容器以 UID 10001 运行；进程需要出站 HTTPS，无需映射端口，凭据以私有文件保存在卷中。镜像在发布渠道可用后，也可用 `docker run --rm -it -v wechat2dot:/data ghcr.io/andrelyl/wechat2dot:2.0.0a2 setup --no-browser` 完成首次配置。

在微信发送 `/status`，核对手机收到的回复。查看最近成功拉取、房间读取和待发队列：

```sh
docker compose exec bridge /app/.venv/bin/wechat2dot status --json
docker compose logs --tail 50 bridge
```

更新前停止容器并备份整个私有卷，再重新构建和启动。`docker compose stop` 保留卷和状态。窗口过期时，在微信发 `/ping` 即可触发补发。已有 0.2 容器请先按[迁移说明](NATIVE.md)处理。同一绑定只能由一个常驻进程拉取。

使用 MCP Events、Tunnel 和旧版任务工具时，请使用[独立的官方模式 Compose 配置](DOCKER_OFFICIAL.md)。
