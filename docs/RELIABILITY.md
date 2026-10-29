# 后台运行、停止与升级

先完成 [README 的首次接入](../README.md)，确认手机实际收到回复，再按本文切换到后台。以下操作在原项目目录、原安装用户下进行；前台和后台不能同时使用同一绑定。

本文使用官方 `tunnel-client v0.0.16` 的受管命令，配合项目已有的 `wechat2dot.runtime_guard`。Windows、Linux、macOS 的启动配置分开提供；目前只有 Windows 的私人部署完成真人验收，其他系统仍需按本文最后的标准自行验收。

## 1. 从前台切换，生成守护配置

在前台 Tunnel 终端按 **Ctrl+C**，等它退出。如果原来已有系统恢复任务，先按[维护步骤](#maintenance)暂停它。尚有正在执行的任务时，先等待完成或在原 DOT 中确认处理方式。

在项目目录选择一个系统的代码块，修改**第一行的客户端绝对路径**后执行。代码只读取自动生成的 `tunnel.yaml` 中的 ID 和启动命令，创建私有 `native-profiles` 与 `guard.json`，不会读取密钥正文，也不会启动服务。已有 `guard.json` 时会退出而不覆盖。

**Windows PowerShell：**

```powershell
$w2dClient = 'C:\Tools\tunnel-client\tunnel-client.exe'
@'
import json
from pathlib import Path
import sys
from wechat2dot.onboarding import new_file, private_directory
from wechat2dot.runtime_guard import load_settings

root = Path("runtime").absolute()
client = Path(sys.argv[1]).expanduser().absolute()
if not client.is_file():
    raise SystemExit("Tunnel client path does not exist")
if (root / "guard.json").exists():
    raise SystemExit("guard.json already exists; keep the current settings")
lines = (root / "tunnel.yaml").read_text(encoding="utf-8").splitlines()
def profile_value(prefix):
    return json.loads(next(line[len(prefix):] for line in lines if line.startswith(prefix)))
settings = {
    "alias": "wechat2dot-local",
    "tunnel_client": str(client),
    "profile_dir": str(private_directory(root / "native-profiles")),
    "profile_name": "wechat2dot-local",
    "tunnel_id": profile_value("  tunnel_id: "),
    "bridge_config": str(root / "bridge.json"),
    "runtime_key_file": str(root / "tunnel-runtime-key"),
    "mcp_command": profile_value("      command: "),
}
new_file(root / "guard.json", json.dumps(settings, indent=2) + "\n")
load_settings(root / "guard.json")
print("guard.json created and validated; runtime not started")
'@ | uv run python - "$w2dClient"
```

**Linux / macOS：**

```sh
w2d_client='/absolute/path/to/tunnel-client'
uv run python - "$w2d_client" <<'PY'
import json
from pathlib import Path
import sys
from wechat2dot.onboarding import new_file, private_directory
from wechat2dot.runtime_guard import load_settings

root = Path("runtime").absolute()
client = Path(sys.argv[1]).expanduser().absolute()
if not client.is_file():
    raise SystemExit("Tunnel client path does not exist")
if (root / "guard.json").exists():
    raise SystemExit("guard.json already exists; keep the current settings")
lines = (root / "tunnel.yaml").read_text(encoding="utf-8").splitlines()
def profile_value(prefix):
    return json.loads(next(line[len(prefix):] for line in lines if line.startswith(prefix)))
settings = {
    "alias": "wechat2dot-local",
    "tunnel_client": str(client),
    "profile_dir": str(private_directory(root / "native-profiles")),
    "profile_name": "wechat2dot-local",
    "tunnel_id": profile_value("  tunnel_id: "),
    "bridge_config": str(root / "bridge.json"),
    "runtime_key_file": str(root / "tunnel-runtime-key"),
    "mcp_command": profile_value("      command: "),
}
new_file(root / "guard.json", json.dumps(settings, indent=2) + "\n")
load_settings(root / "guard.json")
print("guard.json created and validated; runtime not started")
PY
```

检查输出为 `guard.json created and validated; runtime not started`。此步骤只适用于 README 的 `init` 生成的前台配置；若你手工改写过 YAML 格式或移动过项目目录，先核对实际路径和启动命令，不要继续复制陈旧设置。

## 2. 注册并启动官方受管进程

以下命令三平台相同，读取刚才的私有配置，通过官方 `runtimes connect` 启动；密钥只作为文件引用传入。Windows 调用使用项目的隐藏控制台包装，避免后续子进程弹窗。

```sh
uv run python -c "from wechat2dot.runtime_guard import load_settings, _invoke; _, s = load_settings('runtime/guard.json'); _invoke([s['tunnel_client'], 'runtimes', 'connect', '--alias', s['alias'], '--tunnel-id', s['tunnel_id'], '--profile', s['profile_name'], '--profile-dir', s['profile_dir'], '--runtime-api-key', 'file:' + s['runtime_key_file'], '--mcp-command', s['mcp_command'], '--json']); print('Managed runtime launch requested; check readiness next')"
uv run python -m wechat2dot.runtime_guard --settings ./runtime/guard.json
```

第二条命令是一次恢复检查。启动初期可能尚未就绪，等待数秒再执行它，查看 `running`、`healthy`、`bridge_healthy` 和 `ready`。最终 `ready: true` 才表示这次检查通过；进程启动成功本身不代表 DOT 已经处理微信任务。

结果同时写入 `runtime/last-runtime-check.json`。退出码为 0 表示就绪或已有检查在运行，2 表示未就绪，需结合结果判断。失败时不会打印官方客户端的原始日志尾部。

受管进程现在能在关闭这个终端后继续运行，但主机重启后还需要下面的系统恢复检查。选择你的操作系统完成第 3 步。

## 3A. Windows：任务计划程序，无前台窗口

用安装时的同一 Windows 用户打开“任务计划程序”，选择“创建任务”（不是创建基本任务）。以下的 `REPO_DIR` 全部替换为项目完整路径，例如 `C:\Apps\wechat2dot`；路径可以包含空格。

| 设置位置 | 填写内容 |
| --- | --- |
| 常规 → 名称 | `wechat2dot-runtime-guard` |
| 常规 → 安全选项 | 当前安装用户；“只在用户登录时运行”；不勾选“使用最高权限运行” |
| 触发器 1 | 当前用户登录时 |
| 触发器 2 | 一次，开始时间设为现在；勾选重复任务，每 **1 分钟**重复，持续时间“无限期”；启用 |
| 操作 → 程序或脚本 | 通过“浏览”选择 `REPO_DIR\.venv\Scripts\pythonw.exe` |
| 操作 → 添加参数 | `-m wechat2dot.runtime_guard --settings "REPO_DIR\runtime\guard.json"` |
| 操作 → 起始于 | `REPO_DIR`，此栏不要额外加引号 |
| 条件 | 需要笔记本电池供电时运行，就取消“只有在交流电源下才启动”和“切换电池时停止”；无需设置唤醒电脑 |
| 设置 | 允许按需运行、错过计划后尽快运行；任务已运行时“不启动新实例”；超过 3 分钟停止 |

保存后右键该任务 → **运行**，等待本次执行结束。用 PowerShell 检查：

```powershell
Get-ScheduledTaskInfo -TaskName 'wechat2dot-runtime-guard' | Format-List LastRunTime,LastTaskResult,NextRunTime
Get-Content ./runtime/last-runtime-check.json
```

应看到最近的运行时间、最终 `LastTaskResult: 0` 和心跳 `ready: true`。运行过程中显示运行中状态不算最终失败。任务动作必须是 `pythonw.exe`，不要在外面套 `powershell.exe`、`cmd.exe` 或批处理。

这是**当前用户登录会话**的后台任务，锁屏时可以运行，退出登录、休眠或关机会影响它。每分钟检查只在进程退出时重新连接，不会每分钟重启健康服务。

## 3B. Linux：本人 systemd timer

此方法要求系统有 systemd 用户服务。创建 `~/.config/systemd/user/`，分别保存下面两个文件。所有 `REPO_DIR` 换成项目绝对路径；unit 文件不展开这个占位符，也不自动读取你的 shell 变量。

文件 `~/.config/systemd/user/wechat2dot-runtime-guard.service`：

```ini
[Unit]
Description=Check the wechat2dot managed runtime

[Service]
Type=oneshot
WorkingDirectory=REPO_DIR
ExecStart="REPO_DIR/.venv/bin/python" -m wechat2dot.runtime_guard --settings "REPO_DIR/runtime/guard.json"
TimeoutStartSec=180
KillMode=process
```

文件 `~/.config/systemd/user/wechat2dot-runtime-guard.timer`：

```ini
[Unit]
Description=Check wechat2dot every minute

[Timer]
OnStartupSec=20s
OnUnitInactiveSec=60s
Unit=wechat2dot-runtime-guard.service

[Install]
WantedBy=timers.target
```

启用并检查：

```sh
systemctl --user daemon-reload
systemctl --user enable --now wechat2dot-runtime-guard.timer
systemctl --user start wechat2dot-runtime-guard.service
systemctl --user list-timers wechat2dot-runtime-guard.timer
cat ./runtime/last-runtime-check.json
```

检查记录应更新且 `ready: true`。这里的 unit 只管理单次检查；`KillMode=process` 保留由官方客户端管理的后续运行进程，否则 oneshot 结束时 systemd 可能清理刚启动的进程。停止桥接必须使用下文的官方 `runtimes stop`，仅停止 timer 不会停止桥接。这是有意的生命周期分工，见 [systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)。

这是用户服务，默认随用户管理器的生命周期运行；如需服务器无人登录时也运行，应由该主机管理员按其策略配置用户 lingering 或服务账号，再单独验收，不假设登录用户的 timer 等于整机开机服务。

## 3C. macOS：本人 LaunchAgent

创建 `~/Library/LaunchAgents/`，把下列内容保存为 `~/Library/LaunchAgents/com.wechat2dot.runtime-guard.plist`。将每处 `REPO_DIR` 替换成实际绝对路径，不使用 `~`。XML 中路径含 `&` 时写为 `&amp;`。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.wechat2dot.runtime-guard</string>
  <key>ProgramArguments</key>
  <array>
    <string>REPO_DIR/.venv/bin/python</string>
    <string>-m</string><string>wechat2dot.runtime_guard</string>
    <string>--settings</string><string>REPO_DIR/runtime/guard.json</string>
  </array>
  <key>WorkingDirectory</key><string>REPO_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict>
</plist>
```

在本人图形登录会话的终端执行：

```sh
chmod 600 "$HOME/Library/LaunchAgents/com.wechat2dot.runtime-guard.plist"
plutil -lint "$HOME/Library/LaunchAgents/com.wechat2dot.runtime-guard.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.wechat2dot.runtime-guard.plist"
launchctl print "gui/$(id -u)/com.wechat2dot.runtime-guard"
cat ./runtime/last-runtime-check.json
```

检查 `plutil` 无语法错误、任务已载入、心跳更新且 `ready: true`。`AbandonProcessGroup` 让官方客户端启动的后台进程在单次检查结束后保留；卸载检查不等于停止桥接，仍需执行下文的官方停止命令。参见 [Apple launchd.plist](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5)。

已载入时不要反复 bootstrap；修改配置先按下文 bootout，再重新载入。该 LaunchAgent 随本人图形登录会话运行，退出登录和休眠会影响服务。

<a id="subscription"></a>
## 4. 订阅期限与手机验收

进程在线和事件订阅有效是不同条件。让原 DOT 调用 `get_weixin_status` 确认 `events.active_subscriptions: 1`，并核对平台侧对应事件任务已启用。

订阅默认有期限；ChatGPT 应在到期前刷新。支持显式参数时，`ttlMs: null` 可请求无到期订阅，成功后 `refreshBefore: null`。如果平台界面没有提供该参数，希望当前本人订阅一直有效到明确取消，可以：

1. 按[维护步骤](#maintenance)暂停系统恢复检查、停止受管进程。
2. 在项目目录执行下面的命令；它要求当前已有本人有效订阅：
   ```sh
   uv run wechat2dot --config ./runtime/bridge.json subscription-forever
   ```
3. 重新运行第 2 步的恢复检查，再恢复系统任务。
4. 回原 DOT 核验有效订阅及平台任务，然后用新标记完成一次微信测试。

该命令只调整当前绑定、当前回调的本地订阅策略，不扩大访问范围，也不保证平台任务永久运行。取消订阅会清除此策略。

**最终验收：**关闭联调终端，从手机发送一条全新标记的任务并确认结果出现一次；再在你方便时测试重启并重新登录后的恢复。心跳健康和接口 `accepted` 不能代替手机验收。

<a id="maintenance"></a>
## 日常停止、恢复和升级

### 先暂停恢复检查

只执行自己系统的操作，并等待正在执行的单次检查结束：

- **Windows：**在任务计划程序中禁用 `wechat2dot-runtime-guard`，确认状态不再是“正在运行”。不要先停 Tunnel，否则定时检查可能马上把它启动。
- **Linux：**执行 `systemctl --user stop wechat2dot-runtime-guard.timer`，再用 `systemctl --user is-active wechat2dot-runtime-guard.service` 等待单次检查结束。临时维护不必 disable。
- **macOS：**执行 `launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.wechat2dot.runtime-guard.plist"`，确认检查进程退出。

然后在项目目录停止受管运行（三平台相同，Windows 隐藏控制台）：

```sh
uv run python -c "from wechat2dot.runtime_guard import load_settings, _invoke; _, s = load_settings('runtime/guard.json'); _invoke([s['tunnel_client'], 'runtimes', 'stop', s['alias'], '--json']); print('Managed runtime stop completed')"
```

修改配置、暂停收信、备份、更新依赖等操作在停止后进行。只有前台部署时，按 Ctrl+C 退出即可，不执行受管停止命令。

### 暂停收信 / 恢复收信

```sh
uv run wechat2dot --config ./runtime/bridge.json pause
```

恢复时先核对是否仍为唯一收信服务，再执行：

```sh
uv run wechat2dot --config ./runtime/bridge.json resume --confirm-single-consumer
```

重新启动：

```sh
uv run python -m wechat2dot.runtime_guard --settings ./runtime/guard.json
```

Windows 重新启用并运行原计划任务；Linux 执行 `systemctl --user start wechat2dot-runtime-guard.timer`；macOS 再执行第 3C 步 bootstrap。不要同时重新运行前台 `run`。

### 更新项目

停止检查与受管进程后，先备份消息状态：

```sh
uv run wechat2dot --config ./runtime/bridge.json backup-managed ./runtime/backups --keep 3
git status --short
git pull --ff-only origin main
uv sync --frozen --extra setup --python 3.11
uv run wechat2dot --config ./runtime/bridge.json doctor
```

如果备份失败、工作区有自己的改动或 Git 不能快进，先处理该问题，不要执行重置、清库或覆盖配置。备份命令保存引擎状态快照，不代替对运行配置和凭据的私人备份。真实备份也不能提交 Git。

确认诊断正常后，按上面的恢复步骤启动并验收微信回复。不要再次执行 `init`、重新扫码或移动项目目录。升级 Tunnel 客户端时先核验其官方发布包；若路径改变，同时核对守护配置的 `tunnel_client`。

### 运行 key 到期

在停止状态下，到同一组织创建新的 Read + Use 运行 key。本机私人备份原 key 文件，移走旧的 `runtime/tunnel-runtime-key` 后，再运行 `uv run wechat2dot save-key --directory ./runtime` 隐藏输入新 key。保留相同文件路径，按恢复步骤启动并检查成功后，在官方页面撤销不再使用的旧 key。不要把新旧密钥发到聊天或 Issue。

### 卸载

先在原 DOT 取消事件订阅，暂停系统检查并停止受管运行；再删除对应的计划任务、systemd unit/timer 或 LaunchAgent 配置。保留私人运行目录直到确认不再需要待办、发送记录和备份。删除代码或状态不是普通重启步骤。

## 恢复机制的边界

守护检查只在官方受管进程已退出时重新连接。临时网络未就绪时不会强杀存活进程；不会修改消息处理结果、重发回复或替你创建订阅。它通过官方进程身份与锁复用进程，不按陈旧 PID 杀进程。

短暂 TLS EOF 等网络故障使用有界退避重试；证书错误、禁止的地址和重定向仍会终止。历史完成任务和已受理回复不重新播放。`uncertain` 必须先在微信人工核对。

若失去订阅，回**原 DOT**重新订阅，调用 `list_pending_weixin_messages` 检查未完成来信；同时核对 DOT 平台任务启用情况。本地订阅持久化不代表平台事件任务永久存活。

官方依据：[受管客户端](https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md)、[MCP Events 生命周期](https://developers.openai.com/plugins/build/mcp-events)。
