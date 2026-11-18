"""Explicit operator actions for native installations."""

import argparse
import asyncio
from contextlib import closing
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .config import Config
from .paths import data_dir
from .sync.store import NativeStore

COMMANDS = {
    "setup",
    "run",
    "service",
    "migrate",
    "upgrade",
    "uninstall",
    "notification-stdio",
    "official",
}


def execute(name, argv):
    root = data_dir()
    p = argparse.ArgumentParser(prog="wechat2dot " + name)
    if name == "setup":
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--no-service", action="store_true")
        p.add_argument("--no-auto-ping", action="store_true")
        p.add_argument(
            "--file-credentials",
            action="store_true",
            help="Store session in private local files",
        )
        p.add_argument("--dot-id")
        p.add_argument(
            "--bot-chat", help="Exact title of the already bound ClawBot conversation"
        )
        from .setup_native import setup

        return asyncio.run(setup(root, p.parse_args(argv)))
    if name == "official":
        from .cli import main

        if "--config" not in argv:
            raise ValueError("legacy_config_required")
        return main(argv)
    if name == "migrate":
        p.add_argument("--from", dest="source", type=Path, required=True)
        from .migrate import migrate

        return migrate(root, p.parse_args(argv).source)
    if name == "service":
        p.add_argument(
            "action", choices=("install", "start", "stop", "status", "uninstall")
        )
        action = p.parse_args(argv).action
        from .service import manage

        return {"action": action, "result": manage(root, action)}
    if name == "doctor":
        p.add_argument("--live", action="store_true")
        from .setup_native import doctor

        return asyncio.run(doctor(root, p.parse_args(argv).live))
    if name == "status":
        p.add_argument("--json", action="store_true")
        structured = p.parse_args(argv).json
        config = Config.load(root, required=False)
        if not (root / "state/bridge.sqlite3").exists():
            data = dict(
                healthy=False,
                issues=["setup_required"],
                context_age_seconds=None,
                outbox={},
                inbox={},
            )
        else:
            with closing(NativeStore(root / "state")) as s:
                data = s.status(config.window)
        if structured:
            return data
        from .status import render

        print(render(data))
        return 0
    if name == "uninstall":
        p.add_argument("--purge", action="store_true")
        purge = p.parse_args(argv).purge
        from .service import manage
        from .platform import check_private_path
        from wechat_dot_bridge.cli import acquire_lock

        cfg = Config.load(root)
        manage(root, "uninstall")
        if purge:
            known = {
                "config.toml",
                "chatgpt-login.json",
                "chatgpt-login.lock",
                "state",
                "locks",
                "backups",
                "status.json",
                "native-task.xml",
                "native-ping",
            }
            if {f.name for f in root.iterdir()} - known:
                raise ValueError("unexpected_data_directory_contents")
            with (
                closing(NativeStore(root / "state")) as s,
                acquire_lock(s.directory / "worker.lock"),
            ):
                if cfg.credentials == "keyring":
                    import keyring
                    from .vault import token_store

                    vault = token_store(cfg)
                    b = s._binding()
                    if (
                        keyring.get_password(vault.service, "weixin:" + b["id"])
                        is not None
                    ):
                        keyring.delete_password(vault.service, "weixin:" + b["id"])
                    vault.clear()
            check_private_path(root, directory=True)
            shutil.rmtree(root)
        return {"service_removed": True, "data_preserved": not purge}
    if name == "upgrade":
        p.add_argument("--manager", choices=("uv", "pipx", "brew"), default="uv")
        manager = p.parse_args(argv).manager
        from wechat_dot_bridge.operations import backup
        from wechat_dot_bridge.cli import acquire_lock
        from .service import manage
        from .platform import private_directory

        if not shutil.which(manager):
            raise ValueError("upgrade_manager_unavailable")
        if manager == "uv":
            directory = subprocess.check_output(
                ["uv", "tool", "dir"], text=True
            ).strip()
            managed = Path(directory) / "wechat2dot"
        elif manager == "pipx":
            directory = subprocess.check_output(
                ["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"], text=True
            ).strip()
            managed = Path(directory) / "wechat2dot"
        else:
            directory = subprocess.check_output(
                ["brew", "--prefix", "wechat2dot"], text=True
            ).strip()
            managed = Path(directory) / "libexec"
        if managed.resolve() != Path(sys.prefix).resolve():
            raise ValueError("upgrade_manager_does_not_own_installation")
        started = manage(root, "status")
        if started:
            manage(root, "stop")
        try:
            with (
                closing(NativeStore(root / "state")) as s,
                acquire_lock(s.directory / "worker.lock"),
            ):
                directory = private_directory(root / "backups")
                backup(s, directory / f"upgrade-{time.time_ns()}.sqlite3")
                command = {
                    "uv": ["uv", "tool", "upgrade", "wechat2dot"],
                    "pipx": ["pipx", "upgrade", "wechat2dot"],
                    "brew": ["brew", "upgrade", "wechat2dot"],
                }[manager]
                subprocess.run(command, check=True)
        finally:
            if started:
                manage(root, "start")
        return {"backup_created": True, "upgraded": True, "service_restarted": started}
    if name == "login":
        p.add_argument(
            "--weixin",
            action="store_true",
            help="Renew the current owner's WeChat binding",
        )
        p.add_argument("--no-browser", action="store_true")
    opts = p.parse_args(argv)
    if name == "run":
        from .native import run

        run(root)
    elif name == "notification-stdio":
        from .notification_mcp import serve

        asyncio.run(serve(root))
    elif name == "login":
        from .dot.auth import Auth
        from .dot.http import Http
        from .vault import token_store
        from .setup_native import authorize

        cfg = Config.load(root, required=False)

        if opts.weixin:
            from .setup_native import renew_weixin
            from wechat_dot_bridge.cli import acquire_lock
            from wechat_dot_bridge.transport import configure_network

            configure_network(cfg.dns)
            with (
                closing(NativeStore(root / "state")) as s,
                acquire_lock(s.directory / "worker.lock"),
            ):
                renew_weixin(s, cfg, opts.no_browser)
            return {"wechat_login_renewed": True}

        async def flow():
            http = Http(dns=cfg.dns)
            try:
                await authorize(
                    Auth(token_store(cfg, migrate=True), http), opts.no_browser
                )
            finally:
                await http.close()

        asyncio.run(flow())
        return {"logged_in": True}
    return 0
