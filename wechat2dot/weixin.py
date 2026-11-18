"""Native adapter over the existing Tencent iLink poll, media and verified send transport."""

import asyncio
import json

from wechat_dot_bridge.errors import BridgeError
from wechat_dot_bridge.media import MediaWorker, safe_name
from wechat_dot_bridge.transport import IlinkClient, WeixinWorker, _application_error
from wechat2dot.dot.http import FileTooLarge, FileUnavailable, NetworkError
from wechat2dot.dot.client import AccountChanged, DotError, DotProtocolError
from .sync.store import digest


class Outgoing:
    def __init__(
        self, store, dot, binding, window=86400, retention=7 * 86400, client=None
    ):
        self.s, self.dot, self.b = store, dot, binding
        self.window, self.retention = window, retention
        self.client = client or IlinkClient()

    def route(self):
        owner = self.s._binding()
        return self.s._notification_context(owner)

    def refresh_window(self, context):
        s = self.s
        now = s.clock()
        token = digest(context["context_token"]) if context else "missing"
        age = now - context["created_at"] if context else self.window
        closed = s.get("closed_context")
        if age >= self.window or closed == token:
            s.put("closed_context", token)
            s.db.execute(
                "UPDATE native_out SET state='waiting_context' WHERE state='queued'"
            )
            return False
        if closed:
            count, first, last = (
                s.db.execute("""SELECT count(DISTINCT source),min(stamp),max(stamp)
                 FROM native_out WHERE state='waiting_context' AND priority=1""").fetchone()
            )
            expired = s.db.execute(
                """SELECT count(DISTINCT source) FROM native_out
                  WHERE state='waiting_context' AND priority=1 AND stamp<?""",
                (now - self.retention,),
            ).fetchone()[0]
            s.db.execute(
                """UPDATE native_out SET state='expired',body='null' WHERE state='waiting_context'
                            AND stamp<?""",
                (now - self.retention,),
            )
            s.db.execute(
                "UPDATE native_out SET state='queued',retry=0 WHERE state='waiting_context'"
            )
            s.put("closed_context", None)
            if count:
                from datetime import datetime

                fmt = (
                    lambda t: datetime.fromtimestamp(t)
                    .astimezone()
                    .strftime("%m-%d %H:%M")
                )
                s.notice(
                    "catchup:" + token,
                    f"你离开期间 DOT 发了 {count} 条（{fmt(first)} 至 {fmt(last)}）。超过保留期 {expired} 条。",
                    -1,
                )
                s.event("catchup_summary")
        return True

    async def tick(self):
        s, now = self.s, self.s.clock()
        binding = s.get_binding()
        if not binding or not binding["enabled"] or binding["status"] != "ready":
            return False
        with s._transaction():
            ctx = self.route()
            if (
                not self.refresh_window(ctx)
                or max(s.get("wx_retry", 0), binding.get("send_retry_at", 0)) > now
            ):
                return False
            row = s.db.execute(
                """SELECT o.* FROM native_out o WHERE o.state='queued'
                AND (o.priority<1 OR ?=0)
                AND NOT EXISTS (SELECT 1 FROM native_out prev WHERE prev.source=o.source
                    AND prev.part<o.part AND prev.state IN ('queued','sending','waiting_context'))
                ORDER BY o.priority,o.stamp,o.source,o.part LIMIT 1""",
                (int(bool(s.get("paused") or s.get("protocol_stop"))),),
            ).fetchone()
            if not row or row["retry"] > now:
                return False
            if (
                row["priority"] == 1
                and s.db.execute(
                    """SELECT 1 FROM native_seen WHERE state IN ('observing','streaming') AND stamp<? LIMIT 1""",
                    (row["stamp"],),
                ).fetchone()
            ):
                return False
            if s.get("paused") and row["source"].startswith("catchup:"):
                return False
            if row["kind"] == "text" and row["part"] == 0 and row["priority"] == 1:
                text_record = s.db.execute(
                    "SELECT digest FROM native_text WHERE source=?", (row["source"],)
                ).fetchone()
                if text_record and any(
                    digest(r[0]) == text_record[0]
                    for r in s.db.execute(
                        "SELECT text FROM native_direct WHERE at BETWEEN ? AND ?",
                        (now - 600, now),
                    )
                ):
                    s.db.execute(
                        "UPDATE native_out SET state='accepted',body='null' WHERE source=? AND kind='text' AND state='queued'",
                        (row["source"],),
                    )
                    s.event("direct_notification_matched", row["source"])
                    return True
        body = json.loads(row["body"])
        reply = {
            "client_id": row["id"],
            "peer": binding["owner"],
            "context_token": ctx["context_token"],
        }
        if row["kind"] == "file":
            try:
                data, name, mime = await self.dot.download_file(
                    self.b.room_id, body["file_id"], self.b.account_id
                )
            except (FileTooLarge, FileUnavailable) as exc:
                with s._transaction():
                    s.db.execute(
                        "UPDATE native_out SET state='failed' WHERE id=?", (row["id"],)
                    )
                    s.event(
                        "file_too_large"
                        if isinstance(exc, FileTooLarge)
                        else "file_unavailable",
                        row["id"],
                    )
                    s.notice(
                        "file-limit:" + row["id"],
                        "DOT 的附件超过 20 MiB，请在主对话查看。"
                        if isinstance(exc, FileTooLarge)
                        else "DOT 附件已无法下载，请在主对话查看。",
                    )
                return True
            except (AccountChanged, DotProtocolError):
                raise
            except (NetworkError, DotError):
                s.event("dot_attachment_retry", row["id"])
                s.db.execute(
                    "UPDATE native_out SET retry=? WHERE id=?", (now + 30, row["id"])
                )
                return False
            reply["attachment"] = {
                "data": data,
                "name": safe_name(name or body["name"]),
                "kind": "image"
                if (mime or body.get("mime") or "").startswith("image/")
                else "file",
            }
        else:
            reply["text"] = body
        with s._transaction():
            changed = s.db.execute(
                """UPDATE native_out SET state='sending',attempts=attempts+1
                       WHERE id=? AND state='queued'""",
                (row["id"],),
            ).rowcount
            if not changed:
                return False
        delay, http, ret = 0, None, None
        try:
            response = await asyncio.to_thread(self.client.send, binding, reply)
            error = _application_error(response, sendmessage=True)
            http, ret = 200, response.get("ret", response.get("errcode"))
        except BridgeError as e:
            error = e.code
            http, delay = getattr(e, "http_status", None), getattr(e, "retry_delay", 0)
        age = max(0, now - ctx["created_at"])
        with s._transaction():
            retry = 0
            if not error:
                state = "accepted"
            elif error == "context_required" or (
                error == "possible_context_expired" and age > 3600
            ):
                state = "waiting_context"
                s.put("closed_context", digest(ctx["context_token"]))
                s.db.execute(
                    "UPDATE native_out SET state='waiting_context' WHERE state='queued'"
                )
            elif error in (
                "http_rate_limited",
                "rate_limited",
                "possible_context_expired",
                "dns_rejected",
            ):
                limited = error in ("http_rate_limited", "rate_limited")
                if limited:
                    state = (
                        "queued" if now < row["stamp"] + self.retention else "expired"
                    )
                else:
                    state = "queued" if row["attempts"] < 2 else "failed"
                retry = now + max(delay or 0, 30 * (2 ** min(2, row["attempts"])))
                if error in ("http_rate_limited", "rate_limited"):
                    s.put("wx_retry", retry)
                    s.db.execute(
                        "UPDATE binding SET send_retry_at=max(send_retry_at,?)",
                        (retry,),
                    )
            elif error == "auth_required":
                state = "queued"
                s.put("wx_error", "auth_required")
                s.db.execute(
                    "UPDATE binding SET status='auth_required',last_error='auth_required',enabled=0"
                )
            else:
                state = (
                    "uncertain"
                    if error
                    in (
                        "network_error",
                        "network_timeout",
                        "response_invalid",
                        "network_cancelled",
                        "response_too_large",
                    )
                    else "failed"
                )
            if (
                state in ("accepted", "waiting_context")
                or error == "possible_context_expired"
            ):
                s.put(
                    "last_send_evidence",
                    {
                        "http": http,
                        "ret": ret,
                        "age_seconds": int(age),
                        "result": state,
                    },
                )
            s.db.execute(
                "UPDATE native_out SET state=?,retry=?,body=CASE WHEN ?='accepted' THEN 'null' ELSE body END WHERE id=?",
                (state, retry, state, row["id"]),
            )
            if state == "accepted" and row["kind"] == "text":
                s.db.execute(
                    """UPDATE native_text SET accepted_at=? WHERE source=? AND NOT EXISTS
                             (SELECT 1 FROM native_out WHERE source=? AND kind='text' AND state!='accepted')""",
                    (now, row["source"], row["source"]),
                )
            if error:
                s.event(error, row["id"])
        return True


class NotificationDispatch:
    """Consume only opted-in MCP notifications. An old Events reply must never be sent in native mode."""

    def __init__(self, store, client):
        self.worker = WeixinWorker(self, client)
        self.store = store

    def claim_notification(self):
        s = self.store
        claim = s.claim_notification()
        if not claim:
            return None
        if s.db.execute(
            "SELECT 1 FROM native_text WHERE digest=? AND accepted_at BETWEEN ? AND ?",
            (digest(claim["text"]), s.clock() - 600, s.clock()),
        ).fetchone():
            with s._transaction():
                s.db.execute(
                    """UPDATE notifications SET status='accepted',error=NULL,attempts=attempts-1
                             WHERE id=? AND status='sending'""",
                    (claim["notification_id"],),
                )
                s.event("mirror_already_accepted", claim["notification_id"])
            return None
        return claim

    def claim_send(self):
        return None

    def __getattr__(self, key):
        return getattr(self.store, key)

    def tick(self):
        return self.worker.send_once()


def workers(store):
    return WeixinWorker(store), MediaWorker(store)
