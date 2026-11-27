"""The single native worker. Socket triggers REST reconciliation, transport loops run independently."""

import asyncio
from contextlib import ExitStack, closing
import hashlib
import json
import os
import signal
import sys
import time

from wechat_dot_bridge.cli import acquire_lock
from wechat_dot_bridge.media import MediaWorker, configure_network as media_network
from wechat_dot_bridge.net_transport import cancel_all, reset_cancellation
from wechat_dot_bridge.transport import WeixinWorker, configure_network
from .config import Config, atomic_file
from .dot.auth import Auth, AuthRequired, AuthUnavailable
from .dot.client import (
    AccountChanged,
    DotClient,
    DotError,
    DotProtocolError,
    binding_for,
)
from .dot.http import Http, NetworkError
from .dot.live import DotLive
from .keepalive import Keepalive
from .platform import private_directory
from .sync.incoming import Incoming
from .sync.cards import update_cards
from .sync.room import RoomReader
from .sync.store import NativeStore
from .vault import token_store
from .weixin import NotificationDispatch, Outgoing


ROOM_IDLE_RECHECK = 15
ROOM_ACTIVE_WINDOW = 120


async def discover(client, auth, config, choose=None):
    _, account = await auth.access()
    if config.account_id and config.account_id != account:
        raise AccountChanged("account_changed")
    dots = await client.list_dots(account_id=account)
    if config.dot_id:
        dots = [d for d in dots if d["id"] == config.dot_id]
    if not dots:
        raise DotError("主 Dot 不可用", code="no_dot")
    if len(dots) > 1:
        if choose is None:
            raise DotError("select_dot", code="select_dot")
        dots = [choose(dots)]
    b = binding_for(dots[0], account)
    if config.room_id and b.room_id != config.room_id:
        raise AccountChanged("room_changed")
    return b


async def service_loop(root, config, s, stack):
    state = s.directory
    configure_network(config.dns)
    media_network(None, config.dns)
    http = Http(dns=config.dns)
    auth = Auth(token_store(config), http)
    dot = DotClient(http, auth, max_upload_bytes=config.inbound_file_bytes)
    if not (
        config.account_id and config.dot_id and config.room_id and s.get("members")
    ):
        raise ValueError("setup_required")
    from .dot.client import Binding

    reset_cancellation()
    members = s.get("members")
    b = Binding(
        config.account_id,
        config.dot_id,
        config.room_id,
        tuple(members[0]),
        tuple(members[1]),
        "DOT",
    )
    s.pin(b)
    s.recover()
    s.put("socket", False)
    # A persisted protocol stop is cleared by this startup's verified discovery and page read.
    stop = asyncio.Event()
    wake = asyncio.Event()
    force = True
    checked_room = False
    retry_until = 0
    last_sweep = 0
    last_active = None
    pending_room_event = False
    health = {"wx": time.monotonic(), "dot": time.monotonic()}
    reader = RoomReader(s, dot, b)
    inbox = Incoming(s, dot, b, config.window)
    out = Outgoing(s, dot, b, config.window, config.retention)
    keep = Keepalive(s, config)

    def connection():
        return stack.enter_context(closing(NativeStore(state)))

    poller = WeixinWorker(connection())
    media = MediaWorker(connection(), max_file_bytes=config.inbound_file_bytes)
    direct = NotificationDispatch(connection(), out.client)

    def resync():
        nonlocal force
        force = True
        wake.set()

    def event(room, kind):
        nonlocal last_active, pending_room_event
        if room == b.room_id:
            last_active = time.monotonic()
            if kind != "inbound":
                pending_room_event = True
            wake.set()

    live = DotLive(dot, event, resync)

    def fault(code):
        if s.get("dot_error") != code:
            with s._transaction():
                s.put("dot_error", code)
                s.event(code)
                if code in ("auth_required", "account_changed", "dot_protocol_changed"):
                    s.notice(
                        "dot-error:" + code + ":" + str(int(s.clock())),
                        "DOT 需要重新登录，请运行 wechat2dot login。"
                        if code == "auth_required"
                        else "DOT 接口或房间绑定发生变化，房间收发已暂停，请查看 status。",
                    )

    async def room_loop():
        nonlocal force, checked_room, retry_until, last_sweep, last_active
        nonlocal pending_room_event
        failures = 0
        while not stop.is_set():
            wake.clear()
            read_for_event, pending_room_event = pending_room_event, False
            health["dot"] = time.monotonic()
            try:
                if not checked_room:
                    verified = await discover(dot, auth, config)
                    # Track legitimate membership changes only after rediscovering the pinned room.
                    reader.b = inbox.b = out.b = verified
                    s.put("members", [verified.dot_members, verified.human_members])
                sweep = force or time.monotonic() - last_sweep >= 1800
                force = False
                previous_head = s.get("head")
                await reader.read(full=sweep)
                checked_room = True
                head_changed = s.get("head") != previous_head
                if head_changed:
                    last_active = time.monotonic()
                if sweep:
                    s.prune_sent_references(config.retention)
                    last_sweep = time.monotonic()
                if s.get("dot_error") != "dot_protocol_changed" or not s.get(
                    "protocol_stop"
                ):
                    s.put("dot_error", None)
                if s.get("protocol_stop"):
                    return
                retry_until = 0
                failures = 0
                waiting = s.db.execute(
                    "SELECT 1 FROM native_seen WHERE state IN ('observing','streaming') LIMIT 1"
                ).fetchone()
                active_window = ROOM_ACTIVE_WINDOW if live.connected else 900
                active = last_active is not None and time.monotonic() - last_active < active_window
                delay = 2 if waiting or active else ROOM_IDLE_RECHECK
                if read_for_event and not head_changed and not waiting:
                    # The socket notification can precede visibility in the room read.
                    delay = 1
            except (
                AuthRequired,
                AccountChanged,
                DotProtocolError,
                NetworkError,
                AuthUnavailable,
                DotError,
            ) as exc:
                checked_room = False
                if isinstance(exc, DotProtocolError):
                    s.put("protocol_stop", True)
                    fault("dot_protocol_changed")
                    return
                code = (
                    "auth_required"
                    if isinstance(exc, AuthRequired)
                    else "account_changed"
                    if isinstance(exc, AccountChanged)
                    else "dot_unavailable"
                )
                fault(code)
                failures += 1
                delay = min(120, 2 ** min(failures, 7))
                retry_until = time.monotonic() + delay
            try:
                await asyncio.wait_for(wake.wait(), delay)
                if retry_until > time.monotonic():
                    await asyncio.sleep(retry_until - time.monotonic())
            except asyncio.TimeoutError:
                pass

    async def wx_loop():
        while not stop.is_set():
            health["wx"] = time.monotonic()
            success = await asyncio.to_thread(poller.poll_once)
            if success:
                s.put("wx_ok", s.clock())
                s.put("wx_error", None)
                if s.get("wx_login_notified"):
                    s.put("wx_login_notified", False)
            else:
                binding = s.get_binding()
                code = (binding or {}).get("last_error") or "weixin_unavailable"
                if s.get("wx_error") != code or (
                    code == "auth_required" and not s.get("wx_login_notified")
                ):
                    s.event(code)
                    if code == "auth_required" and sys.platform == "darwin":
                        s.put("wx_login_notified", True)
                        await asyncio.create_subprocess_exec(
                            "/usr/bin/osascript",
                            "-e",
                            'display notification "请重新扫码登录微信" with title "wechat2dot"',
                        )
                s.put("wx_error", code)
            if poller.poll_added:
                event(b.room_id, "inbound")
            await asyncio.sleep(0.2 if success else 5)

    async def download_media():
        while not stop.is_set():
            download = asyncio.create_task(asyncio.to_thread(media.tick))
            try:
                await asyncio.shield(download)
            except asyncio.CancelledError:
                # Shutdown cancels network I/O before waiting for SQLite state to settle.
                await download
                raise
            await asyncio.sleep(0.4)

    async def receive():
        if (
            checked_room
            and retry_until <= time.monotonic()
            and not s.get("protocol_stop")
        ):
            await inbox.tick(reader.read)
        else:
            s.accept_incoming()
            await local_commands()

    next_card = 0

    async def ship():
        nonlocal next_card
        if not s.get("protocol_stop") or s.db.execute(
            "SELECT 1 FROM native_out WHERE priority<1 AND state='queued'"
        ).fetchone():
            await out.tick()
        if not s.get("paused") and not s.get("closed_context"):
            await asyncio.to_thread(direct.tick)
        if (
            time.monotonic() >= next_card
            and checked_room
            and not s.get("dot_error")
            and not s.get("protocol_stop")
        ):
            next_card = time.monotonic() + 30
            await update_cards(s, dot, reader.b)

    async def transfer(step):
        nonlocal checked_room
        while not stop.is_set():
            try:
                await step()
            except DotProtocolError:
                fault("dot_protocol_changed")
                s.put("protocol_stop", True)
            except (AuthRequired, AccountChanged) as exc:
                checked_room = False
                fault(
                    "auth_required"
                    if isinstance(exc, AuthRequired)
                    else "account_changed"
                )
                resync()
            except (DotError, AuthUnavailable, NetworkError):
                await asyncio.sleep(3)
            await asyncio.sleep(0.4)

    async def local_commands():
        for row in (
            s.db.execute("""SELECT i.mid,m.text FROM native_in i JOIN messages m ON m.id=i.mid
                                  WHERE i.state='queued' ORDER BY m.rowid""").fetchall()
        ):
            if row["text"].strip().lower() in (
                "/ping",
                "/help",
                "/status",
                "/resume",
                "/pause",
            ):
                await inbox.command(row["mid"], row["text"])

    async def housekeeping():
        ping_due = 0
        while not stop.is_set():
            s.put("heartbeat", s.clock())
            s.put("socket", live.connected)
            if time.monotonic() - health["wx"] > 300 or (
                not s.get("protocol_stop") and time.monotonic() - health["dot"] > 300
            ):
                raise RuntimeError("worker_watchdog")
            if time.monotonic() >= ping_due:
                ping_due = time.monotonic() + 30
                await keep.tick()
                s.db.execute("DELETE FROM native_direct WHERE at<?", (s.clock() - 600,))
            atomic_file(
                root / "status.json",
                json.dumps(s.status(config.window), ensure_ascii=False),
            )
            await asyncio.sleep(5)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    s.put("protocol_stop", False)
    jobs = [
        asyncio.create_task(room_loop()),
        asyncio.create_task(wx_loop()),
        asyncio.create_task(download_media()),
        asyncio.create_task(transfer(receive)),
        asyncio.create_task(transfer(ship)),
        asyncio.create_task(housekeeping()),
        asyncio.create_task(live.run()),
    ]
    waiter = asyncio.create_task(stop.wait())
    try:
        live_jobs = set(jobs)
        while not waiter.done() and live_jobs:
            done, _ = await asyncio.wait(
                live_jobs | {waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done - {waiter}:
                live_jobs.remove(task)
                task.result()
    finally:
        poller.client.cancel()
        out.client.cancel()
        cancel_all()
        for task in [waiter, *jobs]:
            task.cancel()
        await asyncio.gather(waiter, *jobs, return_exceptions=True)
        await http.close()
        s.put("socket", False)
        s.put("heartbeat", 0)
        atomic_file(
            root / "status.json",
            json.dumps(s.status(config.window), ensure_ascii=False),
        )


def run(root):
    from pathlib import Path
    from .migrate import source_fingerprint

    config = Config.load(root)
    if config.mode != "native":
        raise ValueError("official_mode_selected")
    with ExitStack() as stack:
        s = stack.enter_context(closing(NativeStore(root / "state")))
        for directory in dict.fromkeys(
            [s.directory, *([Path(config.legacy_state)] if config.legacy_state else [])]
        ):
            stack.enter_context(acquire_lock(directory / "worker.lock"))
        b = s.get_binding()
        if not b:
            raise ValueError("wechat_login_required")
        lock_name = "account-" + hashlib.sha256(b["id"].encode()).hexdigest() + ".lock"
        for directory in dict.fromkeys(
            [
                private_directory(root / "locks"),
                *(
                    []
                    if Path("/.dockerenv").exists()
                    else [Path.home() / ".local/state/wechat-dot-bridge/locks"]
                ),
                *([Path(config.old_locks)] if config.old_locks else []),
            ]
        ):
            stack.enter_context(acquire_lock(directory / lock_name))
        if config.legacy_state and source_fingerprint(
            Path(config.legacy_state)
        ) != s.get("migration_fingerprint"):
            raise ValueError("legacy_state_changed_since_migration")
        if sys.platform == "darwin" and config.caffeinate:
            import subprocess

            proc = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]
            )
            stack.callback(proc.terminate)
        asyncio.run(service_loop(root, config, s, stack))
