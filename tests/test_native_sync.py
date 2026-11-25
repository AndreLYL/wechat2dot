"""End-to-end native transfer at the two transport boundaries. All accounts are fixtures."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from wechat2dot.config import Config
from wechat2dot.dot.client import Binding, DotProtocolError
from wechat2dot.dot.http import NetworkError, Response
from wechat2dot.keepalive import Keepalive
from wechat2dot.sync.cards import approval_view, update_cards
from wechat2dot.sync.incoming import Incoming
from wechat2dot.sync.room import RoomReader, weixin_text
from wechat2dot.sync.store import NativeStore
from wechat2dot.weixin import Outgoing, NotificationDispatch
from wechat_dot_bridge.errors import BridgeError

B = Binding("account", "dot", "room", ("agent",), ("owner",), "test")
CODEX_URI = "codex://threads/00000000-0000-4000-8000-000000000001"


class NativeLinkFormatting(unittest.TestCase):
    def test_codex_addresses_are_copyable_without_touching_code_or_web_targets(self):
        code = f"`{CODEX_URI}`"
        web_url = f"https://example.com/?next=({CODEX_URI})"
        preserved = (
            web_url,
            f"[网页]({web_url})",
            f"<{web_url}>",
            f"`[会话]({CODEX_URI})`",
            f"```text\n**[会话]({CODEX_URI})**\n```",
            f"~~~~text\n{CODEX_URI}\n~~~~",
        )
        for original, expected in (
            (f"[会话](<http://{CODEX_URI}>)", f"会话（{code}）"),
            (f"[http://{CODEX_URI}]({CODEX_URI})", code),
            (f"<{CODEX_URI}>", code),
            (f"链接https://{CODEX_URI}。", f"链接{code}。"),
            (f"**{CODEX_URI}**", code),
            (code, code),
            *((item, item) for item in preserved),
        ):
            with self.subTest(original=original):
                self.assertEqual(weixin_text(original), expected)
                self.assertEqual(weixin_text(expected), expected)


class Room:
    def __init__(self):
        self.items = []
        self.sent = []
        self.behavior = None

    async def messages(self, room, limit, before=None, account_id=None):
        assert room == B.room_id and account_id == B.account_id
        end = (
            next(i for i, x in enumerate(self.items) if x["id"] == before)
            if before
            else len(self.items)
        )
        return self.items[max(0, end - limit) : end]

    async def send(self, room, text, request_id, account_id, files):
        self.sent.append((text, request_id, files))
        if self.behavior:
            raise self.behavior
        return Response(201, {"request_id": request_id}, {})

    async def download_file(self, *args):
        return b"test bytes", "test.txt", "text/plain"


class Wx:
    def __init__(self):
        self.calls = []
        self.result = {}

    def send(self, b, r):
        self.calls.append(r)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve() / "state"
        self.now = 1800000000.0
        self.room = Room()
        self.wx = Wx()
        self.open()
        self.s.import_binding(
            {
                "id": "wx",
                "owner": "me",
                "token": "fixture",
                "base_url": "https://ilinkai.weixin.qq.com",
            }
        )
        self.s.set_enabled(True)
        self.s.pin(B)
        self.inbound("/ping", "first", "context1")
        await self.inbox.tick(self.reader.read)
        self.add("old", "old")
        await self.reader.read()
        self.now += 1

    def open(self):
        self.s = NativeStore(self.path, clock=lambda: self.now)
        self.addCleanup(self.s.close)
        self.reader = RoomReader(self.s, self.room, B)
        self.out = Outgoing(self.s, self.room, B, client=self.wx)
        self.inbox = Incoming(self.s, self.room, B, 86400)

    def add(self, mid, text, **opts):
        it = dict(
            id=mid,
            created_at=self.now,
            account_user_id="agent",
            content={"text": text},
            **opts,
        )
        self.room.items.append(it)
        return it

    def inbound(self, text, key, ctx):
        self.s.ingest(
            "wx",
            [
                dict(
                    source_id=key,
                    peer="me",
                    text=text,
                    created_at=self.now,
                    context_token=ctx,
                )
            ],
            key,
        )

    async def stable(self):
        await self.reader.read()
        self.now += 2
        await self.reader.read()

    async def test_outgoing_parts_keep_complete_links_inside_both_limits(self):
        titled = '[官方 [发布页]](https://example.com/releases/Version_(2.9) "说明")'
        autolink = "<https://example.com/Docs_(v2)?x=1>"
        bare = "https://example.com/Docs_(v2)"
        for i, (prefix, source, expected_link) in enumerate(
            (
                ("x" * 3798 + "\n", titled, titled),
                ("文" * 2727 + "\n", autolink, autolink),
                ("x" * 3798 + "\n", bare + "。", bare),
                ("文" * 2727 + "\n", f"<{CODEX_URI}>", f"`{CODEX_URI}`"),
            )
        ):
            with self.subTest(link=expected_link):
                self.wx.calls.clear()
                self.add(f"link-{i}", prefix + source)
                await self.stable()
                while await self.out.tick():
                    pass
                parts = [call["text"] for call in self.wx.calls]
                self.assertEqual(len(parts), 2)
                self.assertEqual(parts[0], prefix)
                self.assertIn(expected_link, parts[1])
                self.assertEqual("".join(parts), weixin_text(prefix + source))
                self.assertTrue(
                    all(len(part) <= 3800 and len(part.encode()) <= 8192 for part in parts)
                )

    async def test_outgoing_oversized_link_still_honors_payload_limits(self):
        url = "https://example.com/" + "路径" * 1500
        self.add("oversized-link", url)
        await self.stable()
        while await self.out.tick():
            pass
        parts = [call["text"] for call in self.wx.calls]
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), url)
        self.assertTrue(
            all(len(part) <= 3800 and len(part.encode()) <= 8192 for part in parts)
        )

    async def test_initial_link_and_final_text(self):
        it = self.add("stream", "partial", status="streaming")
        await self.stable()
        self.assertFalse(await self.out.tick())
        it["content"]["text"] = "**完整**"
        it["status"] = "completed"
        await self.stable()
        await self.out.tick()
        self.assertEqual([r["text"] for r in self.wx.calls], ["完整"])
        it["content"]["text"] = "edited"
        await self.stable()
        self.assertFalse(await self.out.tick())
        self.assertEqual(self.s.history()["changed_after_freeze"], 1)

    async def test_weixin_attachment_and_voice_transcript_enter_primary_room(self):
        def inbound_media(source, kind, name, body):
            self.s.ingest(
                "wx",
                [
                    dict(
                        source_id=source,
                        peer="me",
                        text=body,
                        created_at=self.now,
                        context_token="new",
                        attachments=[dict(kind=kind, name=name)],
                    )
                ],
                source,
            )

        inbound_media("photo", "image", "photo.png", "look")
        self.room.upload_file = AsyncMock(return_value="file-room")
        self.assertFalse(await self.inbox.tick(self.reader.read))
        aid = self.s.db.execute(
            "SELECT id FROM attachments WHERE kind='image'"
        ).fetchone()[0]
        self.s.finish_attachment(aid, b"fixture-png")
        await self.inbox.tick(self.reader.read)
        self.room.upload_file.assert_awaited_once_with(
            "room", "photo.png", "image/png", b"fixture-png", "account"
        )
        self.assertEqual(self.room.sent[-1][2], ["file-room"])
        inbound_media("voice", "voice", "audio", "语音转写")
        await self.inbox.tick(self.reader.read)
        self.assertEqual(self.room.sent[-1][0], "语音转写")
        self.assertEqual(self.room.sent[-1][2], [])

    async def test_native_inbox_accepts_more_than_legacy_mcp_capacity(self):
        batch = [
            dict(
                source_id=str(n),
                peer="me",
                text="offline",
                created_at=self.now + n,
                context_token="c",
            )
            for n in range(180)
        ]
        self.assertEqual(self.s.ingest("wx", batch, "last-page"), 180)
        self.assertEqual(self.s.accept_incoming(), 100)
        self.assertEqual(self.s.accept_incoming(), 80)
        self.assertEqual(self.s.ingest("wx", batch, "last-page"), 0)
        self.assertEqual(self.s.status()["inbox"]["queued"], 180)
        self.assertEqual(
            self.s.db.execute(
                "select count(*) from message_work where state!='completed'"
            ).fetchone()[0],
            0,
        )

    async def test_streaming_then_one_complete_read_is_not_final(self):
        item = self.add("unfinished", "same", status="streaming")
        await self.stable()
        item["status"] = "completed"
        self.now += 3
        await self.reader.read()
        self.assertFalse(await self.out.tick())
        self.now += 2
        await self.reader.read()
        self.assertTrue(await self.out.tick())
        self.assertEqual(self.wx.calls[0]["text"], "same")

    async def test_reverse_notification_dedup(self):
        self.add("mirrored", "same text")
        await self.stable()
        await self.out.tick()
        self.s.configure_notifications(enabled=True)
        queued = self.s.queue_notification("same text", "later", self.now + 150)
        NotificationDispatch(self.s, self.wx).tick()
        self.assertEqual(len(self.wx.calls), 1)
        self.assertEqual(
            self.s.notification_status(queued["notification_id"])["status"], "accepted"
        )

    async def test_task_start_and_terminal_once(self):
        item = self.add("task-ref", "")
        item["content"]["attachments"] = [{"type": "thread", "thread_id": "attached"}]
        await self.stable()
        self.room.task_status = AsyncMock(
            side_effect=[
                ("整理文件", "inProgress"),
                ("整理文件", "inProgress"),
                ("整理文件", "completed"),
            ]
        )
        for _ in range(4):
            await update_cards(self.s, self.room, B)
            while await self.out.tick():
                pass
        self.assertEqual(
            [m["text"] for m in self.wx.calls],
            ["任务：整理文件（进行中）", "已完成：整理文件"],
        )
        self.assertEqual(self.room.task_status.await_count, 3)

    async def test_unavailable_attachment_does_not_block_later_message(self):
        from wechat2dot.dot.http import FileUnavailable

        self.room.download_file = AsyncMock(side_effect=FileUnavailable())
        item = self.add("a", "")
        item["content"]["attachments"] = [{"type": "file", "file_id": "gone"}]
        self.now += 1
        self.add("z", "next")
        await self.stable()
        while await self.out.tick():
            pass
        self.assertEqual(self.s.status()["outbox"]["failed"], 1)
        self.assertEqual(self.wx.calls[-1]["text"], "next")

    async def test_ping_echo_reusing_old_context_does_not_confirm_refresh(self):
        self.now += 43201
        keeper = Keepalive(self.s, Config(auto_ping=True), AsyncMock(return_value=True))
        await keeper.tick()
        self.now += 1
        self.inbound("/ping", "echo", "context1")
        self.now += 62
        await keeper.tick()
        self.assertNotIn("ping_confirmed", self.s.history())
        self.assertEqual(self.s.history()["ping_unconfirmed"], 1)

    async def test_backfill_pages_including_human_filter(self):
        for i in range(76):
            self.now += 1
            it = self.add(str(i), str(i))
            if i % 2:
                it["account_user_id"] = "owner"
        await self.reader.read(full=True)
        self.now += 2
        await self.reader.read(full=True)
        while await self.out.tick():
            pass
        self.assertEqual(
            [r["text"] for r in self.wx.calls], [str(i) for i in range(0, 76, 2)]
        )

    async def test_unfinished_pure_file_and_missing_content(self):
        f = self.add("file", "")
        f["content"]["attachments"] = [
            {"type": "file", "file_id": "abc", "file": {"name": "test.txt"}}
        ]
        self.add("unknown", "")
        await self.stable()
        await self.out.tick()
        self.assertEqual(self.wx.calls[0]["attachment"]["data"], b"test bytes")
        self.now += 121
        await self.reader.read()
        await self.out.tick()
        self.assertIn("主对话", self.wx.calls[-1]["text"])

    async def test_accepted_part_never_replayed_and_uncertain_not_retried(self):
        self.add("long", "x" * 8000)
        await self.stable()
        self.assertTrue(await self.out.tick())
        import subprocess, sys

        # Terminate a real sender exactly after the mock iLink transport sees part two.
        program = """
import asyncio, os, signal, sys
from pathlib import Path
from wechat2dot.sync.store import NativeStore
from wechat2dot.weixin import Outgoing
class Interrupted:
    def send(self, binding, reply):
        Path(sys.argv[1]).joinpath('observed-id').write_text(reply['client_id'])
        if os.name == 'posix':
            os.kill(os.getpid(), signal.SIGKILL)
        os._exit(72)
s=NativeStore(Path(sys.argv[1]), clock=lambda: float(sys.argv[2]))
asyncio.run(Outgoing(s, None, None, client=Interrupted()).tick())
"""
        crashed = subprocess.run(
            [sys.executable, "-c", program, str(self.path), str(self.now)], timeout=15
        )
        self.assertNotEqual(crashed.returncode, 0)
        attempted = self.path.joinpath("observed-id").read_text()
        self.assertEqual(
            attempted,
            self.s.db.execute("SELECT id FROM native_out WHERE part=1").fetchone()[0],
        )
        self.s.close()
        self.open()
        self.s.recover()
        self.assertEqual(
            dict(self.s.db.execute("select part,state from native_out")),
            {0: "accepted", 1: "uncertain", 2: "queued"},
        )
        # The interrupted part remains under review; known unsent later parts still continue.
        self.assertTrue(await self.out.tick())
        self.assertFalse(await self.out.tick())
        self.assertEqual(len(self.wx.calls), 2)
        self.assertEqual(self.s.status()["outbox"], {"accepted": 2, "uncertain": 1})
        self.assertEqual(self.s.status()["pending_review"][0]["part"], 2)

    async def test_closed_window_summary_and_expiry(self):
        self.now += 86401
        self.add("backlog", "hello")
        await self.stable()
        self.assertFalse(await self.out.tick())
        self.assertEqual(self.s.status()["outbox"], {"waiting_context": 1})
        self.inbound("/ping", "fresh", "context2")
        await self.inbox.tick(self.reader.read)
        await self.out.tick()
        await self.out.tick()
        self.assertIn("1 条", self.wx.calls[0]["text"])
        self.assertEqual(self.wx.calls[1]["text"], "hello")
        self.now += 8 * 86400
        self.add("missed", "too old")
        await self.stable()
        await self.out.tick()
        self.now += 8 * 86400
        self.inbound("/ping", "fresh3", "context3")
        await self.out.tick()
        self.assertIn("超过保留期 1 条", self.wx.calls[-1]["text"])

    async def test_rejection_by_age_and_real_rate_limit(self):
        self.add("retry", "hello")
        await self.stable()
        self.wx.result = {"ret": -2}
        await self.out.tick()
        self.assertEqual(self.s.status()["outbox"], {"queued": 1})
        self.assertFalse(await self.out.tick())
        self.now += 3700
        await self.out.tick()
        self.assertEqual(self.s.status()["outbox"], {"waiting_context": 1})
        self.inbound("/ping", "fresh", "context2")
        self.wx.result = BridgeError("http_rate_limited")
        self.wx.result.http_status = 429
        self.wx.result.retry_delay = 91
        await self.out.tick()
        self.assertGreaterEqual(self.s.get("wx_retry"), self.now + 91)

    async def test_owner_message_echo_reconciliation_after_restart(self):
        self.inbound("hello", "message", "c")
        self.room.behavior = NetworkError()
        with self.assertRaises(NetworkError):
            await self.inbox.tick(self.reader.read)
        request = self.room.sent[0][1]
        echo = self.add("echo", "hello", request_id=request)
        echo["account_user_id"] = "owner"
        self.s.close()
        self.open()
        self.s.recover()
        await self.inbox.tick(self.reader.read)
        self.assertEqual(
            self.s.db.execute(
                "select state from native_in where request_id=?", (request,)
            ).fetchone()[0],
            "accepted",
        )
        self.assertEqual(len(self.room.sent), 1)

    async def test_room_request_id_and_unattempted_legacy_upgrade(self):
        import uuid

        self.inbound("fresh", "new", "c")
        self.s.accept_incoming()
        mid, rid = self.s.db.execute(
            "SELECT mid,request_id FROM native_in WHERE state='queued'"
        ).fetchone()
        self.assertEqual(str(uuid.UUID(rid)), rid)
        self.s.db.execute(
            "UPDATE native_in SET request_id=? WHERE mid=?", ("w2d-" + "f" * 64, mid)
        )
        self.s.recover()
        self.assertEqual(
            self.s.db.execute(
                "SELECT request_id FROM native_in WHERE mid=?", (mid,)
            ).fetchone()[0],
            rid,
        )
        self.s.db.execute(
            "UPDATE native_in SET request_id=?,attempts=1,state='verify' WHERE mid=?",
            ("w2d-" + "f" * 64, mid),
        )
        self.s.recover()
        self.assertEqual(
            self.s.db.execute(
                "SELECT request_id FROM native_in WHERE mid=?", (mid,)
            ).fetchone()[0],
            "w2d-" + "f" * 64,
        )

    async def test_confirmed_connection_failure_retries_with_same_request_id(self):
        from wechat2dot.dot.http import NotConnected

        self.inbound("fresh", "new", "c")
        self.room.behavior = NotConnected()
        self.assertFalse(await self.inbox.tick(self.reader.read))
        self.assertEqual(self.s.status()["inbox"]["queued"], 1)
        self.room.behavior = None
        self.now += 3
        await self.inbox.tick(self.reader.read)
        self.assertEqual(self.room.sent[0][1], self.room.sent[1][1])
        self.assertEqual(self.s.status()["inbox"]["accepted"], 1)

    async def test_unknown_post_and_commands_are_not_reposted(self):
        self.inbound("question", "input", "c")
        self.room.behavior = NetworkError()
        with self.assertRaises(NetworkError):
            await self.inbox.tick(self.reader.read)
        await self.inbox.tick(self.reader.read)
        self.inbound("/status", "status", "d")
        await self.inbox.tick(self.reader.read)
        self.assertEqual(len(self.room.sent), 1)
        self.assertEqual(self.s.status()["inbox"]["uncertain"], 1)

    async def test_duplicate_official_notification_and_identical_distinct_messages(
        self,
    ):
        self.s.configure_notifications(enabled=True)
        self.s.queue_notification("same", "source", self.now + 300)
        claim = self.s.claim_notification()
        self.s.finish_notification(claim["notification_id"], "accepted")
        self.s.db.execute("UPDATE native_direct SET at=?", (self.now,))
        self.add("copied", "same")
        await self.stable()
        self.assertFalse(await self.out.tick())
        self.now += 610
        self.add("new", "same")
        await self.stable()
        self.assertTrue(await self.out.tick())

    async def test_unknown_protocol_does_not_advance_anchor(self):
        self.room.items.append({"id": "bad", "created_at": self.now})
        with self.assertRaises(DotProtocolError):
            await self.reader.read()
        self.assertEqual(self.s.get("head"), "old")

    async def test_keepalive_needs_inbound_receipt(self):
        self.now += 43201
        ping = AsyncMock(return_value=True)
        k = Keepalive(self.s, Config(auto_ping=True), ping)
        await k.tick()
        self.assertNotIn("ping_confirmed", self.s.history())
        self.now += 2
        self.inbound("/ping", "ping2", "new")
        await k.tick()
        self.assertEqual(self.s.history()["ping_confirmed"], 1)
        ping.assert_awaited_once()

    async def test_approval_requires_current_request_and_offered_option(self):
        item = self.add("ask", "")
        data = {
            "request_id": "req",
            "thread_id": "t",
            "turn_id": "u",
            "params": {
                "threadId": "t",
                "turnId": "u",
                "message": "Read fixture?",
                "requestedSchema": {"type": "object", "properties": {}},
                "_meta": {
                    "codex_approval_kind": "mcp_tool_call",
                    "persist": ["session"],
                },
            },
        }
        item["content"]["elicitation"] = data
        await self.stable()
        code = self.s.db.execute("select code from native_approvals").fetchone()[0]
        self.room.approval_details = AsyncMock(
            return_value={
                "elicitation": data,
                "can_respond": True,
                "request_fingerprint": "fp",
            }
        )
        self.room.decide = AsyncMock(
            return_value=Response(200, {"status": "applied"}, {})
        )
        self.inbound("/accept-always " + code, "badchoice", "c")
        await self.inbox.tick(self.reader.read)
        self.room.decide.assert_not_awaited()
        self.inbound("/accept-conv " + code, "choice", "c")
        await self.inbox.tick(self.reader.read)
        self.room.decide.assert_awaited_once_with(
            "room", "ask", "account", "fp", "accept", {}, "session"
        )
        self.inbound("/accept-conv " + code, "again", "c")
        await self.inbox.tick(self.reader.read)
        self.assertEqual(self.room.decide.await_count, 1)
        data["params"]["requestedSchema"]["properties"] = {"email": {"type": "string"}}
        self.assertIsNone(approval_view(data))
