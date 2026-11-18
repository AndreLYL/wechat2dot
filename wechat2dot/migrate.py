"""Offline snapshot migration. Never start a second consumer of an existing binding."""

from contextlib import closing, ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from wechat_dot_bridge.cli import acquire_lock, load_config, parser
from .config import Config
from .platform import check_private_path, private_directory, safe_open
from .sync.store import NativeStore


def source_fingerprint(directory):
    file = check_private_path(directory / "bridge.sqlite3")
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        h = hashlib.sha256()
        for q in (
            "SELECT id,owner,token,cursor FROM binding ORDER BY id",
            "SELECT client_id,status,attempts FROM replies ORDER BY client_id",
            "SELECT id,status,attempts FROM notifications ORDER BY id",
            "SELECT id FROM messages ORDER BY id",
            "SELECT * FROM notification_policy ORDER BY account,peer",
        ):
            for row in db.execute(q):
                h.update(json.dumps(row).encode())
        return h.hexdigest()


def migrate(root, source):
    source = check_private_path(Path(source).absolute(), directory=True)
    cfg_path = check_private_path(source / "bridge.json")
    params = parser().parse_args(["--config", str(cfg_path), "status"])
    state, _, locks, *_ = load_config(params)
    state, locks = Path(state), Path(locks)
    dest = private_directory(root / "state")
    if state.resolve() == dest.resolve():
        raise ValueError("source_is_destination")
    target = dest / "bridge.sqlite3"
    with ExitStack() as stack:
        stack.enter_context(acquire_lock(dest / "worker.lock"))
        stack.enter_context(acquire_lock(state / "worker.lock"))
        original = check_private_path(state / "bridge.sqlite3")
        db = stack.enter_context(
            closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True))
        )
        owner = db.execute("SELECT id FROM binding").fetchone()
        if not owner:
            raise ValueError("source_not_bound")
        lockname = "account-" + hashlib.sha256(owner[0].encode()).hexdigest() + ".lock"
        stack.enter_context(acquire_lock(locks / lockname))
        fingerprint = source_fingerprint(state)
        config = Config.load(root, required=False)
        if target.exists():
            with closing(NativeStore(dest)) as installed:
                if installed.get("migration_fingerprint") == fingerprint:
                    return {"migrated": True, "reused": True}
                unclaimed = (
                    str(state) == config.legacy_state
                    and installed.get("migration_fingerprint")
                    and not installed.get("identity")
                    and not installed.get("baseline")
                )
                if not unclaimed:
                    raise ValueError("destination_already_has_state")
                # A staged snapshot can be refreshed only if nothing, including the
                # optional notification process, has changed that snapshot locally.
                if source_fingerprint(dest) != installed.get("migration_fingerprint"):
                    raise ValueError("destination_already_has_state")
                installed.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            stage = private_directory(Path(tmp) / "private")
            staged = stage / "bridge.sqlite3"
            os.close(safe_open(staged, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600))
            with closing(sqlite3.connect(staged)) as snapshot:
                db.backup(snapshot)
                if snapshot.execute("pragma integrity_check").fetchone() != ("ok",):
                    raise ValueError("backup_invalid")
            with closing(NativeStore(stage)) as s:
                s.put("migration_fingerprint", fingerprint)
                s.put(
                    "in_cursor",
                    s.db.execute(
                        "SELECT coalesce(max(rowid),0) FROM messages"
                    ).fetchone()[0],
                )
                s.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            config.old_locks = str(locks)
            config.legacy_state = str(state)
            config.dns = params.weixin_dns
            config.save(root)
            os.replace(staged, target)
    return {"migrated": True, "source_preserved": True}
