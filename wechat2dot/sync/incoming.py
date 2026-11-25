"""Owner messages are consumed in arrival order with a persisted POST reservation."""

import json
import mimetypes

from wechat_dot_bridge.errors import BridgeError
from wechat_dot_bridge.store import MAX_INBOUND_BYTES
from wechat2dot.dot.auth import AuthRequired, AuthUnavailable
from wechat2dot.dot.client import AccountChanged, DotError, DotProtocolError
from wechat2dot.dot.http import (
    FileTooLarge,
    FileUnavailable,
    NetworkError,
    NotConnected,
)
from .cards import approval_view


def with_quote_hint(text, hint):
    if not text:
        return hint
    remaining = MAX_INBOUND_BYTES - len(text.encode("utf-8")) - 2
    if remaining <= 0 or not hint:
        return text
    excerpt = hint.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
    return text + "\n\n" + excerpt if excerpt else text


class Incoming:
    def __init__(self, store, client, binding, window):
        self.s, self.client, self.b, self.window = store, client, binding, window

    def finish(self, mid, state, notice=None):
        with self.s._transaction():
            self.s.db.execute("UPDATE native_in SET state=? WHERE mid=?", (state, mid))
            if notice:
                self.s.notice("in:" + mid, notice)
            if state in ("uncertain", "failed"):
                self.s.event("dot_" + state, mid)

    async def command(self, mid, text):
        # Replying to a WeChat message is conversation content, including slash commands.
        if self.s.has_quote(mid):
            return False
        pieces = text.strip().split()
        if not pieces:
            return False
        name = pieces[0].lower()
        if name in ("/accept", "/accept-conv", "/accept-always", "/decline"):
            if len(pieces) != 2:
                self.finish(mid, "handled", "请按审批卡片上的命令和编号回复。")
            else:
                await self.approve(mid, name, pieces[1])
            return True
        if len(pieces) != 1 or name not in (
            "/status",
            "/pause",
            "/resume",
            "/help",
            "/ping",
        ):
            return False
        with self.s._transaction():
            if name in ("/pause", "/resume"):
                self.s.put("paused", name == "/pause")
            notices = {
                "/pause": "已暂停房间同步。",
                "/resume": "已恢复房间同步。",
                "/help": "/status 查看状态，/pause 暂停，/resume 恢复，/ping 刷新连接。审批请按卡片上的命令操作。",
            }
            if name == "/status":
                from wechat2dot.status import render

                notices[name] = render(self.s.status(self.window))
            if name in notices:
                self.s.notice("command:" + mid, notices[name])
            self.s.db.execute(
                "UPDATE native_in SET state='handled' WHERE mid=?", (mid,)
            )
        return True

    async def approve(self, mid, name, code):
        s, b = self.s, self.b
        row = s.db.execute(
            "SELECT * FROM native_approvals WHERE code=? AND state='open'", (code,)
        ).fetchone()
        if not row:
            self.finish(mid, "handled", "审批编号无效或已处理。")
            return
        original = json.loads(row["request"])
        details = await self.client.approval_details(
            b.room_id, row["mid"], b.account_id
        )
        current = details.get("elicitation")
        view = approval_view(current)
        remember = {"/accept-conv": "session", "/accept-always": "always"}.get(name)
        if (
            not view
            or details.get("can_respond") is not True
            or not details.get("request_fingerprint")
            or current.get("params") != original.get("params")
            or any(
                current.get(k) != original.get(k)
                for k in ("request_id", "thread_id", "turn_id")
            )
            or (remember and remember not in view.persist)
        ):
            self.finish(mid, "handled", "审批已变化或不支持该选项，请到 ChatGPT 核对。")
            return
        with s._transaction():
            count = s.db.execute(
                "UPDATE native_approvals SET state='submitting' WHERE code=? AND state='open'",
                (code,),
            ).rowcount
            if not count:
                return
            s.db.execute(
                "UPDATE native_in SET state='approval_reserved' WHERE mid=?", (mid,)
            )
        action = "decline" if name == "/decline" else "accept"
        try:
            r = await self.client.decide(
                b.room_id,
                row["mid"],
                b.account_id,
                details["request_fingerprint"],
                action,
                view.content if action == "accept" else None,
                remember,
            )
            accepted = (
                r.status in (200, 201, 204)
                and isinstance(r.body, dict)
                and r.body.get("status") in ("applied",)
            )
        except (NetworkError, AuthRequired, DotError):
            accepted = False
        with s._transaction():
            s.db.execute(
                "UPDATE native_approvals SET state=? WHERE code=?",
                ("submitted" if accepted else "uncertain", code),
            )
        self.finish(
            mid,
            "handled" if accepted else "uncertain",
            "已提交审批决定。" if accepted else "审批结果待核对，请在 ChatGPT 查看。",
        )

    async def tick(self, read_room):
        s, b = self.s, self.b
        s.accept_incoming()
        rows = s.db.execute("""SELECT i.*,m.text FROM native_in i JOIN messages m ON m.id=i.mid
               WHERE i.state IN ('queued','verify') ORDER BY m.rowid""").fetchall()
        # Commands remain available when paused or a remote queue is unavailable.
        if not rows:
            return False
        row = rows[0]
        for candidate in rows:
            if candidate["state"] == "queued" and candidate["text"].strip().lower() in (
                "/status",
                "/help",
                "/ping",
                "/resume",
                "/pause",
            ):
                if await self.command(candidate["mid"], candidate["text"]):
                    return True
        mid = row["mid"]
        if (
            s.get("paused")
            or s.get("dot_error")
            in ("dot_protocol_changed", "auth_required", "account_changed")
            or row["retry"] > s.clock()
        ):
            return False
        if row["state"] == "verify":
            await read_room(full=True)
            state = s.db.execute(
                "SELECT state FROM native_in WHERE mid=?", (mid,)
            ).fetchone()[0]
            if state == "verify":
                self.finish(
                    mid,
                    "uncertain",
                    "有一条微信来信在 DOT 的发送结果待核对。请到主对话查看。",
                )
            return True
        if await self.command(mid, row["text"]):
            return True
        quote = s.quote_context(mid)
        if quote.refresh:
            # The original POST may have been accepted before its message ID was known.
            await read_room(full=False)
            quote = s.quote_context(mid)
        text = s.incoming_text(mid, row["text"])
        file_ids = []
        try:
            attachments = s.attachment_metadata(mid)
            files = [item for item in attachments if item["kind"] != "voice"]
            if attachments and not files and text in ("", "[微信附件]"):
                self.finish(mid, "failed", "这条语音没有微信转写，请发文字或重新录音。")
                return True
            if any(item["status"] in ("pending", "downloading") for item in files):
                return False
            for item in files:
                if item["status"] != "ready":
                    raise BridgeError(item["error"] or "attachment_unavailable")
            for item in files:
                attachment = s.read_attachment(item["attachment_id"])
                mime = mimetypes.guess_type(item["name"])[0] or "application/octet-stream"
                file_ids.append(
                    await self.client.upload_file(
                        b.room_id, item["name"], mime, attachment["data"], b.account_id
                    )
                )
        except (FileTooLarge, FileUnavailable, BridgeError) as error:
            too_large = isinstance(error, FileTooLarge) or (
                isinstance(error, BridgeError) and error.code == "media_too_large"
            )
            notice = (
                "附件超过传输大小上限，请在 ChatGPT 主对话中上传。"
                if too_large
                else "附件无法读取或上传，请在 ChatGPT 主对话中上传。"
            )
            self.finish(mid, "failed", notice)
            return True
        fallback_text = with_quote_hint(text, quote.hint)
        with s._transaction():
            s.db.execute(
                "UPDATE native_in SET state='sending',attempts=attempts+1 WHERE mid=?",
                (mid,),
            )
        try:
            request = (row["request_id"], b.account_id, file_ids)
            if quote.target:
                referenced_text = fallback_text if quote.selected or not (text or file_ids) else text
                r = await self.client.send(
                    b.room_id, referenced_text, *request,
                    reply_to=quote.target,
                )
            else:
                r = await self.client.send(b.room_id, fallback_text, *request)
            # Retry only a rejected reference or empty caption, using the same file and request IDs.
            if (
                r.status in (400, 422)
                and not (isinstance(r.body, dict) and r.body.get("id"))
                and (quote.target or (not fallback_text and file_ids))
            ):
                r = await self.client.send(
                    b.room_id, fallback_text or "[微信附件]", *request
                )
        except (NotConnected, AuthUnavailable):
            # The room POST was not written, or no access token was available to issue one.
            with s._transaction():
                s.db.execute(
                    "UPDATE native_in SET state='queued',retry=? WHERE mid=?",
                    (s.clock() + 2 ** min(7, row["attempts"] + 1), mid),
                )
                s.event("dot_connect_retry", mid)
            return False
        except (NetworkError, DotProtocolError):
            s.event("dot_post_result_unknown", mid)
            self.finish(mid, "verify")
            raise
        except (AuthRequired, AccountChanged):
            self.finish(mid, "queued")
            raise
        s.put("last_dot_post", {"http": r.status, "time": s.clock()})
        if (
            r.status in (200, 201)
            and isinstance(r.body, dict)
            and r.body.get("request_id") == row["request_id"]
        ):
            with s._transaction():
                s.db.execute("UPDATE native_in SET state='accepted' WHERE mid=?", (mid,))
                s.remember_incoming_reference(row["request_id"], r.body.get("id"))
        elif r.status == 429:
            from wechat_dot_bridge.send_recovery import retry_delay

            delay = retry_delay(r.headers.get("Retry-After"), s.clock()) or 2 ** min(
                8, row["attempts"] + 1
            )
            s.db.execute(
                "UPDATE native_in SET state='queued',retry=? WHERE mid=?",
                (s.clock() + delay, mid),
            )
        elif r.status >= 500 or 200 <= r.status < 300 or (
            r.status in (400, 422) and isinstance(r.body, dict) and r.body.get("id")
        ):
            self.finish(mid, "verify")
        else:
            self.finish(mid, "failed", "DOT 未接受这条微信来信，请检查状态。")
        return True
