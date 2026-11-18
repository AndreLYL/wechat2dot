"""Owner-side liveness: verify an actual inbound /ping before counting success."""

import asyncio
from datetime import datetime
from .sync.store import digest
import sys


class Keepalive:
    def __init__(self, store, config, ping=None):
        self.s, self.config = store, config
        self.ping = ping or self.mac_ping
        self.check_idle = ping is None

    async def mac_ping(self, *options):
        if sys.platform != "darwin" or not self.config.auto_ping:
            return False
        helper = self.s.directory.parent / "native-ping"
        if not helper.is_file():
            return False
        proc = await asyncio.create_subprocess_exec(
            str(helper),
            self.config.bot_chat,
            *options,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await asyncio.wait_for(proc.wait(), 40) == 0
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False

    async def tick(self):
        s, now = self.s, self.s.clock()
        row = s.db.execute(
            "SELECT created_at,context_id,context_token FROM notification_routes LIMIT 1"
        ).fetchone()
        if not row:
            return
        since, context_id, token = row
        pending = s.get("ping_pending")
        if pending is not None:
            observed = s.db.execute(
                """SELECT context_token FROM messages WHERE text='/ping' AND created_at BETWEEN ? AND ?
                                  AND context_token IS NOT NULL AND id!=?""",
                (pending, pending + 60, s.get("ping_context", "")),
            ).fetchall()
            observed = any(digest(r[0]) != s.get("ping_token") for r in observed)
            if observed or now >= pending + 60:
                s.event("ping_confirmed" if observed else "ping_unconfirmed")
                s.put("ping_pending", None)
            else:
                return
        age = now - since
        if (
            self.config.window - 4 * 3600 <= age < self.config.window
            and s.get("warned_context") != context_id
        ):
            with s._transaction():
                s.notice(
                    "window:" + context_id,
                    "微信连接窗口即将到期，回复任意内容即可刷新。",
                )
                s.put("warned_context", context_id)
        if (
            not self.config.auto_ping
            or age < min(12 * 3600, self.config.window / 2)
            or s.get("next_ping", 0) > now
        ):
            return
        day = datetime.fromtimestamp(now).astimezone().date().isoformat()
        attempts = s.get("ping_day", {})
        count = attempts.get("count", 0) if attempts.get("day") == day else 0
        if count >= 3:
            return
        if self.check_idle and not await self.mac_ping("--eligible"):
            return
        # Reserve before UI work; a crash between keystroke and persistence cannot cause a duplicate.
        with s._transaction():
            s.put("ping_day", {"day": day, "count": count + 1})
            s.put("ping_context", context_id)
            s.put("ping_token", digest(token))
            s.put("ping_pending", now)
            s.put("next_ping", now + 600)
        if not await self.ping():
            s.put("ping_pending", None)
            s.event("ping_ui_unavailable")
