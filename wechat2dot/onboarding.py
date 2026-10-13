"""Local setup and layered diagnostics; no account is contacted here."""

import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from .platform import (reject_links, private_directory as native_directory, safe_open,
                       check_private_path, sync_directory)

DEPENDENCIES = ("mcp", "anyio", "cryptography", "Pillow", "jsonschema", "httpcore", "standardwebhooks", "portalocker")


def check_path(path):
    return reject_links(path)


def private_directory(path):
    path = check_path(path)
    if path == Path(path.anchor) or path == Path.home() or path == Path.cwd():
        raise ValueError("dedicated_directory_required")
    return native_directory(path)


def new_file(path, value):
    path = check_path(path)
    descriptor = safe_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            destination.write(value)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def prepare(directory, tunnel_id=None, tunnel_client=None):
    """Create new config, preserving all existing installation files."""
    if tunnel_id is not None and not re.fullmatch(r"tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}", tunnel_id):
        raise ValueError("invalid_tunnel_id")
    if (tunnel_id is None) != (tunnel_client is None):
        raise ValueError("tunnel_id_and_client_required_together")
    root = check_path(directory)
    config = root / "bridge.json"
    profile = root / "tunnel.yaml"
    if any(path.exists() or path.is_symlink() for path in (config, profile)):
        raise ValueError("installation_exists")
    client = check_path(tunnel_client) if tunnel_client else None
    if client and (not client.is_file() or not os.access(client, os.X_OK)):
        raise ValueError("tunnel_client_unavailable")
    root = private_directory(root)
    state = private_directory(root / "state")
    locks = private_directory(root / "locks")
    created = []
    try:
        new_file(config, json.dumps({"state_dir": str(state), "lock_dir": str(locks),
                                    "callback_hosts": []}, indent=2) + "\n")
        created.append(config)
        if client:
            # Official tunnel-client parses the string into argv itself; this
            # is not a cmd.exe / PowerShell command. Single-quote Windows paths
            # so its shellword parser preserves backslashes and spaces.
            command = shlex.join([sys.executable, "-m", "wechat2dot", "--config", str(config), "serve"])
            text = ("config_version: 1\ncontrol_plane:\n  base_url: \"https://api.openai.com\"\n"
                    "  tunnel_id: " + json.dumps(tunnel_id) + "\n"
                    "  api_key: " + json.dumps("file:" + str(root / "tunnel-runtime-key")) + "\n"
                    "health:\n  listen_addr: \"127.0.0.1:0\"\n"
                    "  url_file: " + json.dumps(str(root / "health.url")) + "\n"
                    "admin_ui:\n  open_browser: false\nlog:\n  level: warn\n  format: json\n"
                    "mcp:\n  commands:\n    - channel: main\n      command: " + json.dumps(command) + "\n")
            new_file(profile, text)
            created.append(profile)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return {"config": str(config), "profile": str(profile) if client else None,
            "credentials_created": False, "started": False,
            "next": "save-key, login, connect the private plugin, verify callback, subscribe, then resume"}


def approve_callback(config_path, host):
    """Record one independently verified host; never trust callback discovery automatically."""
    if (not isinstance(host, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
            or "." not in host or ".." in host):
        raise ValueError("exact_hostname_required")
    import ipaddress
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("hostname_required")
    from wechat_dot_bridge.cli import acquire_lock, load_config, parser
    args = parser().parse_args(["--config", str(config_path), "status"])
    state, hosts, *_ = load_config(args)
    with acquire_lock(state / "worker.lock"):
        path = check_path(config_path)
        if path.stat().st_size > 16384:
            raise ValueError("unsafe_config")
        check_private_path(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if host not in hosts:
            hosts.append(host)
        if len(hosts) > 3:
            raise ValueError("too_many_hosts")
        value["callback_hosts"] = hosts
        temporary = path.with_name(path.name + ".pending")
        new_file(temporary, json.dumps(value, indent=2) + "\n")
        os.replace(temporary, path)
        sync_directory(path.parent)
    return {"callback_configured": True, "subscription_verified": False, "restart_required": True}


def diagnose(config_path=None):
    missing = []
    for package in DEPENDENCIES:
        try:
            importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
    config_status = "missing"
    callback_status = "unconfigured"
    private_status = "not_checked"
    if config_path:
        try:
            path = check_path(config_path)
            if not path.is_file() or path.stat().st_size > 16384:
                raise ValueError("config_unavailable")
            value = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or not isinstance(value.get("state_dir"), str)
                    or not isinstance(value.get("callback_hosts"), list)):
                raise ValueError("invalid_config")
            config_status = "present"
            callback_status = "configured_unverified" if value["callback_hosts"] else "unconfigured"
            try:
                check_private_path(path)
                for directory in [path.parent, value["state_dir"], value.get("lock_dir", path.parent / "locks")]:
                    check_private_path(directory, directory=True)
                private_status = "windows_acl_ok" if os.name == "nt" else "posix_permissions_ok"
            except (ValueError, OSError):
                private_status = "unsafe_permissions"
        except (OSError, ValueError):
            config_status = "invalid"
    return {
        "project": "wechat2dot", "mode": "offline_diagnostic", "network_requests": 0,
        "environment": {"python_supported": sys.version_info >= (3, 11),
                        "live_runtime_supported": sys.platform.startswith("linux") or sys.platform in {"win32", "darwin"},
                        "storage_security": "windows_acl" if os.name == "nt" else "posix_permissions",
                        "missing_dependencies": missing},
        "layers": {"configuration": config_status, "private_storage": private_status,
                   "wechat_binding": "not_verified", "mcp_tools": "not_verified",
                   "callback": callback_status, "dot_events": "not_verified",
                   "visible_wechat_reply": "not_verified"},
        "live_ready": False,
        "next": "Follow docs/CONNECT_DOT.md on Windows, Linux or macOS."}
