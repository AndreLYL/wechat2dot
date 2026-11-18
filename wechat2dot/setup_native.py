"""Interactive native setup and read-only diagnostics."""

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import time
import webbrowser

from wechat_dot_bridge.cli import acquire_lock
from wechat_dot_bridge.login import start_login, poll_login
from wechat_dot_bridge.transport import configure_network
from .config import Config, fake_dns
from .dot.auth import Auth
from .dot.client import DotClient
from .dot.http import Http
from .native import discover
from .sync.room import RoomReader
from .sync.store import NativeStore, digest
from .vault import protect_weixin, token_store


def bind_weixin(directory, no_browser=False):
    import qrcode

    result = start_login(directory)
    if result.get("bound"):
        return
    raw = json.loads((directory / "weixin-login.json").read_text())
    qr = qrcode.QRCode(border=2)
    qr.add_data(raw["qrcode_img_content"])
    qr.print_ascii(out=sys.stderr, invert=True)
    path = directory / "native-binding-qr.png"
    from .platform import safe_open

    fd = safe_open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "wb") as f:
        qr.make_image().save(f)
    try:
        if not no_browser:
            webbrowser.open(path.as_uri())
        deadline = time.monotonic() + 480
        code = None
        while time.monotonic() < deadline:
            result = poll_login(directory, verify_code=code)
            code = None
            if result["status"] in ("confirmed", "already_bound"):
                if not result.get("bound"):
                    raise ValueError("weixin_qr_not_bound")
                return
            if result["status"] in ("expired", "verify_code_blocked"):
                break
            if result.get("requires_phone_verification"):
                import getpass

                code = getpass.getpass("请输入微信手机验证码：")
            time.sleep(2)
        raise ValueError("wechat_login_expired")
    finally:
        path.unlink(missing_ok=True)


def renew_weixin(store, cfg, no_browser=False):
    import tempfile
    from wechat2dot.platform import private_directory

    old = store._binding()
    with tempfile.TemporaryDirectory(dir=store.directory.parent) as tmp:
        stage = private_directory(Path(tmp))
        bind_weixin(stage, no_browser)
        with closing(NativeStore(stage)) as fresh:
            new = fresh.get_binding()
        if not new or (old["id"], old["owner"]) != (new["id"], new["owner"]):
            raise ValueError("weixin_binding_changed")
        # Keep the existing cursor and inbox/outbox IDs across credential rotation.
        new.pop("cursor", None)
        store.import_binding(new)
        protect_weixin(store, cfg)
        with store._transaction():
            store.db.execute("DELETE FROM notification_routes")
            store.put("closed_context", "awaiting-fresh-context")
            store.put("wx_error", None)
        store.set_enabled(True)


async def authorize(auth, no_browser):
    if auth.state()["logged_in"]:
        return
    challenge = await auth.device_start()
    print(
        f"登录本人 ChatGPT：{challenge.verification_url}，输入 {challenge.user_code}",
        flush=True,
    )
    if not no_browser:
        webbrowser.open(challenge.verification_url)
    await auth.device_complete(challenge)


async def setup(root, opts):
    from .migrate import migrate
    from .service import manage

    old = Path.home() / ".local/share/wechat2dot/runtime"
    if (
        not (root / "state/bridge.sqlite3").exists()
        and old.joinpath("bridge.json").is_file()
    ):
        print("已找到 0.2 私有运行目录，迁移需要先停止旧的微信收信进程。", flush=True)
        migrate(root, old)
    cfg = Config.load(root, required=False)
    if opts.file_credentials:
        cfg.credentials = "file"
    if opts.dot_id:
        cfg.dot_id = opts.dot_id
    if opts.bot_chat:
        cfg.bot_chat = opts.bot_chat
    if opts.no_auto_ping:
        cfg.auto_ping = False
    try:
        if fake_dns():
            cfg.dns = "google"
    except OSError:
        cfg.dns = "google"
    configure_network(cfg.dns)
    cfg.save(root)
    http = Http(dns=cfg.dns)
    try:
        auth = Auth(token_store(cfg, migrate=True), http)
        with (
            closing(NativeStore(root / "state")) as s,
            acquire_lock(s.directory / "worker.lock"),
        ):
            binding = s.db.execute("SELECT status FROM binding LIMIT 1").fetchone()
            if binding is None:
                bind_weixin(s.directory, opts.no_browser)
            elif binding["status"] == "auth_required":
                renew_weixin(s, cfg, opts.no_browser)
            protect_weixin(s, cfg)
            s.set_enabled(True)
            if not cfg.legacy_state:
                s.configure_notifications(enabled=True)
            await authorize(auth, opts.no_browser)

            def choose(dots):
                for i, d in enumerate(dots, 1):
                    print(f"{i}. {d.get('display_name') or 'DOT'}")
                selected = int(input("选择要连接的 DOT 编号："))
                if not 1 <= selected <= len(dots):
                    raise ValueError("invalid_selection")
                return dots[selected - 1]

            dot = DotClient(http, auth)
            b = await discover(dot, auth, cfg, choose)
            s.pin(b)
            s.put("members", [b.dot_members, b.human_members])
            await RoomReader(s, dot, b).read()
            cfg.dot_id, cfg.account_id, cfg.room_id = b.dot_id, b.account_id, b.room_id
            cfg.save(root)
        if sys.platform == "darwin" and cfg.auto_ping and not opts.no_service:
            import subprocess

            helper = root / "native-ping"
            import hashlib
            import tempfile

            source = Path(__file__).with_name("mac_ping.swift")
            fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
            with closing(NativeStore(root / "state")) as local:
                ready = helper.is_file() and local.get("helper_digest") == fingerprint
                if not ready:
                    with tempfile.TemporaryDirectory(dir=root) as scratch:
                        output = Path(scratch) / "native-ping"
                        compiled = subprocess.run(
                            ["/usr/bin/swiftc", str(source), "-o", str(output)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        if compiled.returncode == 0:
                            os.replace(output, helper)
                            local.put("helper_digest", fingerprint)
                            ready = True
            if ready:
                subprocess.run([str(helper), "--permission"], check=False)
                from .keepalive import Keepalive

                with closing(NativeStore(root / "state")) as store:
                    print("保活只在确认会话、空闲且已解锁时操作。", flush=True)
                    keeper = Keepalive(store, cfg)
                    now = store.clock()
                    if await keeper.mac_ping("--demo"):
                        store.put("ping_pending", now)
                        route = store.db.execute(
                            "SELECT context_id,context_token FROM notification_routes LIMIT 1"
                        ).fetchone()
                        store.put("ping_context", route[0] if route else "")
                        store.put("ping_token", digest(route[1]) if route else None)
            else:
                print(
                    "Mac 自动保活未安装（需要 Xcode Command Line Tools），使用上下文提醒和补发。",
                    flush=True,
                )
        if opts.no_service or Path("/.dockerenv").exists():
            return {
                "configured": True,
                "service_started": False,
                "next": "wechat2dot run，然后在本人微信发送 /status",
            }
        handshake_started = time.time()
        manage(root, "install")
        print("请在已绑定的微信会话发送 /status。正在等待首次往返…", flush=True)
        deadline = time.monotonic() + 150
        with closing(NativeStore(root / "state")) as s:
            while time.monotonic() < deadline:
                health = s.status(cfg.window)
                receipt = s.db.execute(
                    """SELECT 1 FROM native_out o JOIN native_in i ON o.source='command:'||i.mid
                                        JOIN messages m ON m.id=i.mid WHERE lower(trim(m.text))='/status'
                                        AND m.created_at>=? AND o.state='accepted'""",
                    (int(handshake_started),),
                ).fetchone()
                if receipt and health["healthy"]:
                    return {
                        "configured": True,
                        "service_started": True,
                        "weixin_status_accepted": True,
                    }
                await asyncio.sleep(2)
        raise ValueError("service_started_waiting_for_status")
    finally:
        await http.close()


async def doctor(root, live):
    cfg = Config.load(root, required=False)
    report = {
        "configured": bool(cfg.room_id),
        "credentials": cfg.credentials,
        "dns": cfg.dns,
        "mode": cfg.mode,
    }
    if not (root / "state/bridge.sqlite3").exists():
        return dict(report, healthy=False, issues=["setup_required"])
    with closing(NativeStore(root / "state")) as s:
        report.update(s.status(cfg.window))
        report["bound"] = bool(s.get_binding())
    if live:
        from wechat_dot_bridge.net_transport import resolve_addresses

        for name, host in [
            ("weixin_dns", "ilinkai.weixin.qq.com"),
            ("dot_dns", "chatgpt.com"),
        ]:
            try:
                report[name] = bool(
                    await asyncio.to_thread(resolve_addresses, host, dns=cfg.dns)
                )
            except Exception:
                report[name] = False
        http = Http(dns=cfg.dns)
        try:
            auth = Auth(token_store(cfg), http)
            dot = DotClient(http, auth)
            b = await discover(dot, auth, cfg)
            await dot.messages(b.room_id, limit=1, account_id=b.account_id)
            report["dot_read"] = True
        except Exception as e:
            report["dot_read"] = False
            report["dot_error"] = getattr(e, "code", None) or type(e).__name__
        finally:
            await http.close()
    return report
