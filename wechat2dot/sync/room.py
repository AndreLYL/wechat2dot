"""Read-only room reconciliation. Socket events are wakeups, never the source of content."""

import asyncio
import math
import re

from wechat_dot_bridge.message_format import split_text
from wechat2dot.dot.client import (
    PAGE_LIMIT,
    DotProtocolError,
    attachments_of,
    in_progress,
    message_text,
    oldest_first,
    sender_of,
    timestamp_of,
)
from .cards import approval_view, linked_tasks
from .store import digest, packed


def weixin_text(text):
    # Preserve code blocks and URLs; make Markdown links readable in plain text.
    chunks = re.split(r"(```[\s\S]*?```)", text)
    for i in range(0, len(chunks), 2):
        chunks[i] = re.sub(
            r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", chunks[i]
        )
        chunks[i] = re.sub(r"(?m)^#{1,6}\s+", "", chunks[i])
        chunks[i] = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", chunks[i])
    return "".join(chunks)


class RoomReader:
    def __init__(self, store, client, binding):
        self.s, self.client, self.b = store, client, binding
        self.lock = asyncio.Lock()

    async def read(self, full=False):
        async with self.lock:
            return await self._reconcile(full)

    async def _reconcile(self, full):
        s, b = self.s, self.b
        baseline = s.get("baseline")
        head = s.get("head")
        pending = {
            r[0]
            for r in s.db.execute(
                "SELECT id FROM native_seen WHERE state IN ('streaming','observing')"
            )
        }
        before = None
        fetched = {}
        link_time = None
        while True:
            page = await self.client.messages(
                b.room_id, PAGE_LIMIT, before=before, account_id=b.account_id
            )
            for item in page:
                ts = timestamp_of(item)
                if (
                    not isinstance(item.get("id"), str)
                    or not item["id"]
                    or ts is None
                    or not math.isfinite(ts)
                ):
                    raise DotProtocolError("unrecognized room message")
                if not isinstance(item.get("content"), dict) or not isinstance(
                    sender_of(item), str
                ):
                    raise DotProtocolError("unrecognized room content")
            if not page:
                break
            oldest = page[0]["id"]
            if oldest in fetched:
                raise DotProtocolError("room pagination did not advance")
            fetched.update((item["id"], item) for item in page)
            if len(page) < PAGE_LIMIT:
                break
            if baseline is None:
                if link_time is None:
                    link_time = timestamp_of(page[-1])
                if timestamp_of(page[0]) < link_time:
                    break
                before = oldest
                continue
            if timestamp_of(page[0]) < baseline["time"] or (
                not full and head in fetched and pending.issubset(fetched)
            ):
                break
            before = oldest
        now = s.clock()
        items = oldest_first(list(fetched.values()))
        with s._transaction():
            if baseline is None:
                # Exclude known ids at the boundary without excluding a new id at the same timestamp.
                boundary = timestamp_of(items[-1]) if items else now
                s.put(
                    "baseline",
                    {
                        "time": boundary,
                        "ids": [i["id"] for i in items if timestamp_of(i) == boundary],
                    },
                )
            else:
                for item in items:
                    stamp = timestamp_of(item)
                    if stamp < baseline["time"] or item["id"] in baseline["ids"]:
                        continue
                    if sender_of(item) in b.human_members and isinstance(
                        item.get("request_id"), str
                    ):
                        s.db.execute(
                            """UPDATE native_in SET state='accepted' WHERE request_id=?
                                     AND state IN ('sending','verify','uncertain')""",
                            (item["request_id"],),
                        )
                    if sender_of(item) in b.dot_members and not item.get("deleted_at"):
                        self.observe(item, stamp, now)
            if full and baseline is not None:
                # A full sweep can positively establish that an unfinished item disappeared.
                for pending in s.db.execute(
                    "SELECT id FROM native_seen WHERE state IN ('observing','streaming')"
                ).fetchall():
                    candidate = fetched.get(pending[0])
                    if candidate is None or candidate.get("deleted_at"):
                        s.db.execute(
                            "UPDATE native_seen SET state='removed' WHERE id=?",
                            (pending[0],),
                        )
                        s.event("source_message_removed", pending[0])
            if items:
                s.put("head", items[-1]["id"])
            s.put("dot_ok", now)
        return len(items)

    def observe(self, item, stamp, now):
        s, mid = self.s, item["id"]
        text, files = message_text(item), attachments_of(item)
        approval = item["content"].get("elicitation")
        prompt = approval_view(approval) if approval else None
        tasks = linked_tasks(item)
        if prompt:
            code = digest(packed([self.b.room_id, mid, approval.get("request_id")]))[
                :16
            ]
            text = (text + "\n" + prompt.describe(code)).strip()
        elif approval:
            text = (text + "\n此请求需在 ChatGPT 中查看和处理。").strip()
        version = digest(packed([text, files, tasks]))
        row = s.db.execute("SELECT * FROM native_seen WHERE id=?", (mid,)).fetchone()
        if row and row["state"] == "frozen":
            if row["digest"] != version:
                s.event("changed_after_freeze", mid)
                s.db.execute(
                    "UPDATE native_seen SET digest=? WHERE id=?", (version, mid)
                )
            return
        state = "streaming" if in_progress(item) else "observing"
        if not row:
            s.db.execute(
                "INSERT INTO native_seen VALUES(?,?,?,?,?,?)",
                (mid, stamp, version, now, now, state),
            )
            return
        if state == "streaming" or row["state"] != state or version != row["digest"]:
            s.db.execute(
                "UPDATE native_seen SET digest=?,observed=?,state=? WHERE id=?",
                (version, now, state, mid),
            )
            return
        if now - row["observed"] < 1:
            return
        if not text and not files and not tasks:
            if now - row["first_seen"] < 120:
                return
            text = "DOT 发来了一条暂不支持的消息，请到主对话查看。"
            s.event("empty_or_unsupported", mid)
        for task in tasks:
            s.db.execute("INSERT OR IGNORE INTO native_cards VALUES(?, 'new')", (task,))
        if prompt:
            s.db.execute(
                "INSERT OR IGNORE INTO native_approvals VALUES(?,?,?,'open')",
                (code, mid, packed(approval)),
            )
        direct = (
            s.db.execute(
                "SELECT 1 FROM native_direct WHERE text=? AND at BETWEEN ? AND ?",
                (text, now - 600, now),
            ).fetchone()
            if text
            else None
        )
        parts = (
            []
            if not text or direct
            else [("text", part) for part in split_text(weixin_text(text))]
        )
        if text and not direct:
            s.db.execute(
                "INSERT OR IGNORE INTO native_text(source,digest) VALUES(?,?)",
                (mid, digest(text)),
            )
        if direct:
            s.event("direct_notification_matched", mid)
        parts.extend(("file", file) for file in files)
        for n, (kind, body) in enumerate(parts):
            s.enqueue(mid, n, stamp, kind, body)
        s.db.execute("UPDATE native_seen SET state='frozen' WHERE id=?", (mid,))
