"""wechat2dot operator interface. Live network operations are explicit commands."""

import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__

ENGINE_COMMANDS = {"serve", "status", "diagnose", "pause", "resume", "subscriptions", "queue", "latency",
                   "storage", "import-binding", "login-start", "login-poll", "backup", "restore-backup",
                   "retry-send", "retry-attachment", "resolve-send", "cancel-subscription", "subscription-forever",
                   "cleanup-history", "backup-managed"}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wechat2dot", description="Use WeChat to reach your existing dot.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default=os.environ.get("WECHAT2DOT_CONFIG"))
    parser.add_argument("command", choices=["init", "doctor", "demo", "save-key", "allow-callback", "login", *sorted(ENGINE_COMMANDS)])
    args, remaining = parser.parse_known_args(argv)
    try:
        if args.command in ENGINE_COMMANDS:
            if not sys.platform.startswith("linux"):
                raise ValueError("live_runtime_requires_linux_or_docker")
            if not args.config and "--help" not in remaining:
                raise ValueError("config_required")
            from wechat_dot_bridge.cli import main as engine
            prefix = ["--config", args.config] if args.config else []
            return engine([*prefix, args.command, *remaining])
        if args.command == "doctor":
            if remaining:
                parser.error("doctor does not accept extra arguments")
            from .onboarding import diagnose
            result = diagnose(args.config)
        elif args.command == "init":
            command = argparse.ArgumentParser(prog="wechat2dot init")
            command.add_argument("--directory", default="runtime")
            command.add_argument("--tunnel-id")
            command.add_argument("--tunnel-client")
            values = command.parse_args(remaining)
            from .onboarding import prepare
            result = prepare(values.directory, values.tunnel_id, values.tunnel_client)
        elif args.command == "demo":
            command = argparse.ArgumentParser(prog="wechat2dot demo")
            command.add_argument("--preview", action="store_true", help="Show protocol structure without running the Linux engine")
            values = command.parse_args(remaining)
            from .demo import run_demo, preview
            result = preview() if values.preview or not sys.platform.startswith("linux") else run_demo()
        elif args.command == "save-key":
            command = argparse.ArgumentParser(prog="wechat2dot save-key")
            command.add_argument("--directory", default="runtime")
            values = command.parse_args(remaining)
            if not sys.platform.startswith("linux"):
                raise ValueError("live_setup_requires_linux_or_docker")
            import getpass
            import warnings
            from .onboarding import private_directory, new_file
            root = private_directory(values.directory)
            if (root / "tunnel-runtime-key").exists():
                raise ValueError("key_already_exists")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass("Tunnel runtime key (hidden): ")
            if not 20 <= len(key) <= 1024 or any(c.isspace() for c in key):
                raise ValueError("invalid_key")
            new_file(root / "tunnel-runtime-key", key)
            result = {"key_saved": True, "key_printed": False}
        elif args.command == "allow-callback":
            if not args.config or not sys.platform.startswith("linux"):
                raise ValueError("linux_and_config_required")
            command = argparse.ArgumentParser(prog="wechat2dot allow-callback")
            command.add_argument("hostname")
            values = command.parse_args(remaining)
            from .onboarding import approve_callback
            result = approve_callback(args.config, values.hostname)
        else:
            if remaining:
                parser.error("login does not accept extra arguments; use login-poll --verification-code if requested")
            if not args.config or not sys.platform.startswith("linux"):
                raise ValueError("linux_and_config_required")
            result = _login(args.config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as error:
        category = str(error) if isinstance(error, ValueError) and str(error).replace("_", "").isalnum() else "local_operation_failed"
        print(json.dumps({"error": category}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error": "operation_failed", "hint": "Check doctor and the connection guide."}), file=sys.stderr)
        return 2


def _login(config):
    import time
    from wechat_dot_bridge.cli import acquire_lock, load_config, parser
    from wechat_dot_bridge.login import start_login, poll_login
    import qrcode
    args = parser().parse_args(["--config", config, "login-start"])
    state, *_ = load_config(args)
    with acquire_lock(state / "worker.lock"):
        result = start_login(state)
        if result.get("bound"):
            return result
        login = json.loads((state / "weixin-login.json").read_text(encoding="utf-8"))
        qr = qrcode.QRCode(border=2)
        qr.add_data(login["qrcode_img_content"])
        qr.make(fit=True)
        print("Scan this private QR with your WeChat. Do not share this terminal.", file=sys.stderr)
        qr.print_ascii(out=sys.stderr, invert=True)
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline:
            result = poll_login(state)
            if result["status"] in {"confirmed", "expired", "verify_code_blocked", "already_bound"} or result.get("requires_phone_verification"):
                return result
            time.sleep(2)
        return {"status": "wait", "next": "login-poll", "bound": False}
