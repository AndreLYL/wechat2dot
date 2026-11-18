"""Standard per-user locations for 2.x state; WECHAT2DOT_HOME overrides them (Docker, tests)."""
import os
from pathlib import Path

import platformdirs

from .platform import private_directory


def data_dir() -> Path:
    override = os.environ.get("WECHAT2DOT_HOME")
    path = Path(override) if override else Path(platformdirs.user_data_dir("wechat2dot", appauthor=False))
    return private_directory(path.absolute())


def chatgpt_token_file() -> Path:
    return data_dir() / "chatgpt-login.json"
