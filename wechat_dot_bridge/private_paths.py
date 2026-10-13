"""Private state paths shared by persistence and offline administration.

These checks do not open a Store or initialize the database schema.
"""

import os
from pathlib import Path
import stat

from .errors import BridgeError
from wechat2dot.platform import private_directory, safe_open, check_private_fd, check_private_path


def _private_directory(path):
    directory = Path(os.path.abspath(os.fspath(path)))
    try:
        directory = private_directory(directory)
    except ValueError:
        raise BridgeError("unsafe_state_directory") from None
    except BridgeError:
        raise
    except OSError:
        raise BridgeError("state_directory_unavailable") from None
    return directory


def _private_database_file(directory):
    path = directory / "bridge.sqlite3"
    try:
        fd = safe_open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            check_private_fd(fd)
        finally:
            os.close(fd)
        for suffix in ("-wal", "-shm", "-journal"):
            auxiliary = Path(str(path) + suffix)
            if auxiliary.exists() or auxiliary.is_symlink():
                check_private_path(auxiliary)
    except BridgeError:
        raise
    except (OSError, ValueError):
        raise BridgeError("unsafe_database_file") from None
    return path

