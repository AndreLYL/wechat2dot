"""Owner-only, atomic configuration and status files."""

import json
import os
from dataclasses import dataclass, asdict, fields
from pathlib import Path
import sys
import tomllib

from .platform import check_private_path, private_directory, safe_open, sync_directory

DEFAULT_INBOUND_FILE_MIB = 20


def atomic_file(path, contents):
    from uuid import uuid4

    private_directory(path.parent)
    if path.exists():
        check_private_path(path)
    tmp = path.with_name("." + path.name + "-" + uuid4().hex)
    fd = safe_open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(contents)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class Config:
    dot_id: str = ""
    account_id: str = ""
    room_id: str = ""
    mode: str = "native"
    window: int = 86400
    retention: int = 604800
    auto_ping: bool = sys.platform == "darwin"
    caffeinate: bool = sys.platform == "darwin"
    bot_chat: str = "微信ClawBot"
    dns: str = "system"
    old_locks: str = ""
    legacy_state: str = ""
    credentials: str = "file" if Path("/.dockerenv").exists() else "keyring"
    inbound_file_mib: int = DEFAULT_INBOUND_FILE_MIB

    @property
    def inbound_file_bytes(self) -> int:
        return self.inbound_file_mib * 1024 * 1024

    def validate(self):
        if (
            self.mode not in ("native", "official")
            or self.dns not in ("system", "google")
            or self.credentials not in ("file", "keyring")
        ):
            raise ValueError("invalid_config")
        if any(type(x) is not int or x <= 0 for x in (self.window, self.retention)):
            raise ValueError("invalid_window")
        if type(self.inbound_file_mib) is not int or not 1 <= self.inbound_file_mib <= 64:
            raise ValueError("invalid_inbound_file_mib")
        if any(type(v) is not bool for v in (self.auto_ping, self.caffeinate)):
            raise ValueError("invalid_config")
        if any(
            not isinstance(v, str)
            for k, v in asdict(self).items()
            if k not in ("window", "retention", "auto_ping", "caffeinate", "inbound_file_mib")
        ):
            raise ValueError("invalid_config")
        return self

    def save(self, root):
        self.validate()
        settings = asdict(self)
        if self.inbound_file_mib == DEFAULT_INBOUND_FILE_MIB:
            # Older releases reject options they do not recognize.
            del settings["inbound_file_mib"]
        atomic_file(
            root / "config.toml",
            "".join(
                f"{k} = {json.dumps(v, ensure_ascii=False)}\n"
                for k, v in settings.items()
            ),
        )

    @classmethod
    def load(cls, root, required=True):
        path = root / "config.toml"
        if not path.exists() and not required:
            return cls()
        check_private_path(path)
        with path.open("rb") as f:
            data = tomllib.load(f)
        if set(data) - {f.name for f in fields(cls)}:
            raise ValueError("unknown_config_option")
        return cls(**data).validate()


def fake_dns():
    import ipaddress
    import socket

    for host in ("ilinkai.weixin.qq.com", "chatgpt.com"):
        for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(result[4][0]).is_global:
                return True
    return False
