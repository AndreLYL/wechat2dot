"""Offline native quote routing using the iLink and DOT room message shapes."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wechat2dot.dot.client import Binding
from wechat2dot.dot.http import Response
from wechat2dot.sync.incoming import Incoming
from wechat2dot.sync.room import RoomReader
from wechat2dot.sync.store import NativeStore
from wechat2dot.weixin import Outgoing
from wechat_dot_bridge.transport import WeixinWorker


class NativeQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.now = 1000
        self.store = NativeStore(Path(folder.name).resolve() / "state", clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.store.import_binding(
            dict(id="wx", owner="owner", token="fixture", base_url="https://ilinkai.weixin.qq.com")
        )
        self.store.set_enabled(True)
        self.binding = Binding("account", "dot", "room", ("agent",), ("owner",), "DOT")
        self.store.pin(self.binding)
        self.posts, self.messages, self.responses = [], [], []
        self.include_receipt_id = True
        self.dot = SimpleNamespace(send=self.send_dot, messages=AsyncMock(side_effect=self.read_dot))
        self.incoming = Incoming(self.store, self.dot, self.binding, 86400)
        self.reader = RoomReader(self.store, self.dot, self.binding)
        await self.reader.read()
        self.read = AsyncMock(wraps=self.reader.read)

    def read_dot(self, room, limit, before=None, account_id=None):
        self.assertEqual((room, account_id), ("room", "account"))
        return self.messages if before is None else []

    async def send_dot(self, room, text, request_id, account_id, files, reply_to=None):
        self.assertEqual((room, account_id), ("room", "account"))
        self.posts.append((text, request_id, files, reply_to))
        if self.responses:
            return self.responses.pop(0)
        dot_id = f"posted-{len(self.posts)}"
        self.messages.append(
            dict(id=dot_id, request_id=request_id, account_user_id="owner",
                 created_at=self.now, content={"text": text})
        )
        body = {"request_id": request_id}
        if self.include_receipt_id:
            body["id"] = dot_id
        return Response(201, body, {})

    def receive(self, message_id, text, reference=None):
        item = {"type": 1, "text_item": {"text": text}}
        if reference is not None:
            item["ref_msg"] = reference
        wire = dict(message_id=message_id, from_user_id="owner", to_user_id="wx",
                    message_type=1, message_state=2, item_list=[item], context_token="context")
        poll = Mock(return_value={"msgs": [wire], "get_updates_buf": f"cursor-{message_id}"})
        worker = WeixinWorker(self.store, SimpleNamespace(poll=poll, clock=lambda: self.now))
        self.assertTrue(worker.poll_once())
        self.assertEqual(worker.poll_added, 1)
        return self.store.db.execute(
            "SELECT id,text FROM messages WHERE source_id=?", (f"message:{message_id}",)
        ).fetchone()

    async def test_selected_quote_posts_with_native_target_and_never_runs_command(self):
        self.receive(101, "First line. Please keep this sentence. Last line.")
        await self.incoming.tick(self.read)
        selected = "Please keep this sentence."
        received = self.receive(102, "/pause", dict(
            svr_id=101, partial_text={"start": selected, "end": selected, "startindex": 0,
                                      "endindex": 0},
        ))
        self.assertEqual(received["text"], "[微信引用]\n/pause")
        self.assertFalse(await self.incoming.command(received["id"], "/pause"))
        await self.incoming.tick(self.read)
        text, _, files, target = self.posts[-1]
        self.assertEqual(target, "posted-1")
        self.assertEqual(files, [])
        self.assertEqual(text, f"/pause\n\n[微信引用 · 用户原消息] {selected}")
        self.assertFalse(self.store.get("paused"))
        self.read.assert_not_awaited()

    async def test_immediate_quote_uses_incremental_lookup_when_post_has_no_id(self):
        self.include_receipt_id = False
        self.receive(201, "An original message")
        await self.incoming.tick(self.read)
        self.receive(202, "About that message", {"svr_id": 201})
        await self.incoming.tick(self.read)
        self.read.assert_awaited_once_with(full=False)
        self.assertEqual(self.posts[-1][0], "About that message")
        self.assertEqual(self.posts[-1][3], "posted-1")

    async def test_alternate_id_is_tried_before_cached_fallback(self):
        orphan = self.receive(301, "An original message with no DOT counterpart")
        self.store.accept_incoming()
        self.incoming.finish(orphan["id"], "failed")
        self.receive(302, "A different known source")
        await self.incoming.tick(self.read)
        self.receive(303, "A second-ID quote", {
            "svr_id": 301, "message_item": {"type": 1, "msg_id": 302},
        })
        await self.incoming.tick(self.read)
        self.assertEqual(self.posts[-1][0], "A second-ID quote")
        self.assertEqual(self.posts[-1][3], "posted-1")
        self.read.assert_not_awaited()

    async def test_unmatched_attachment_quote_uses_name_without_reattaching(self):
        received = self.receive(401, "这个文件有新版吗？", {
            "svr_id": 400,
            "message_item": {"type": 4, "file_item": {"file_name": "报告.pdf", "media": {}}},
        })
        self.assertEqual(self.store.attachment_metadata(received["id"]), [])
        await self.incoming.tick(self.read)
        text, _, files, target = self.posts[-1]
        self.assertIsNone(target)
        self.assertEqual(files, [])
        self.assertEqual(text, "这个文件有新版吗？\n\n[微信引用] 文件《报告.pdf》")

    async def test_accepted_wechat_receipt_maps_back_to_same_dot_reply(self):
        self.receive(501, "Context for the outgoing fixture")
        await self.incoming.tick(self.read)
        dot_id, text = "dot-reply", "The original reply"
        self.store.db.execute(
            "INSERT INTO native_seen VALUES(?,?,?,?,?,?)",
            (dot_id, self.now, "fixture", self.now, self.now, "frozen"),
        )
        self.store.enqueue(dot_id, 0, self.now, "text", text)
        send_weixin = Mock(return_value={"ret": 0, "message_id": 600})
        outgoing = Outgoing(self.store, self.dot, self.binding, client=SimpleNamespace(send=send_weixin))
        self.assertTrue(await outgoing.tick())
        quote = self.receive(502, "Explain further", {"svr_id": 600})
        await self.incoming.tick(self.read)
        send_weixin.assert_called_once()
        self.assertEqual(self.posts[-1][3], dot_id)
        self.assertEqual(self.posts[-1][0], "Explain further")

        self.now += 8 * 86400
        self.store.prune_sent_references(7 * 86400)
        self.assertIsNone(self.store.quote_context(quote["id"]).target)

    async def test_reference_validation_rejection_retries_with_same_request_id(self):
        self.receive(701, "The original message")
        await self.incoming.tick(self.read)
        self.receive(702, "A reply to it", {"svr_id": 701})
        self.responses.append(Response(422, {}, {}))
        await self.incoming.tick(self.read)
        first, second = self.posts[-2:]
        self.assertEqual(first[3], "posted-1")
        self.assertIsNone(second[3])
        self.assertEqual(first[1], second[1])
        self.assertEqual(second[0], "A reply to it\n\n[微信引用 · 用户原消息] The original message")

    async def test_conflicting_validation_receipt_stays_in_verification(self):
        self.receive(801, "The original message")
        await self.incoming.tick(self.read)
        received = self.receive(802, "A reply to it", {"svr_id": 801})
        self.responses.append(Response(422, {"id": "unexpected-message"}, {}))
        await self.incoming.tick(self.read)
        state = self.store.db.execute(
            "SELECT state FROM native_in WHERE mid=?", (received["id"],)
        ).fetchone()[0]
        self.assertEqual(state, "verify")
        self.assertEqual(len(self.posts), 2)

    async def test_dot_quoted_reply_sends_only_its_own_body_and_file(self):
        self.receive(901, "An original message that should not be copied")
        await self.incoming.tick(self.read)
        self.messages.append({
            "id": "agent-reply",
            "account_user_id": "agent",
            "created_at": self.now,
            "reply_to": {"message_id": "posted-1"},
            "content": {
                "text": "Here is the new version.",
                "attachments": [{"type": "file", "file_id": "file-1", "name": "updated.txt"}],
            },
        })
        await self.reader.read()
        self.now += 2
        await self.reader.read()
        self.dot.download_file = AsyncMock(return_value=(b"updated", "updated.txt", "text/plain"))
        send_weixin = Mock(return_value={"ret": 0})
        outgoing = Outgoing(self.store, self.dot, self.binding, client=SimpleNamespace(send=send_weixin))
        self.assertTrue(await outgoing.tick())
        self.assertTrue(await outgoing.tick())
        self.assertFalse(await outgoing.tick())
        text_part, file_part = (call.args[1] for call in send_weixin.call_args_list)
        self.assertEqual(text_part["text"], "Here is the new version.")
        self.assertEqual(file_part["attachment"]["data"], b"updated")
