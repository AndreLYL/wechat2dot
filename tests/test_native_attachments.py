"""Offline coverage for native inbound file transfer and persistence."""

import base64
from contextlib import closing
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wechat2dot.config import Config
from wechat2dot.dot.client import Binding, DotClient
from wechat2dot.dot.http import FileTooLarge, FileUnavailable, Response
from wechat2dot.sync.incoming import Incoming
from wechat2dot.sync.store import NativeStore
from wechat_dot_bridge.errors import BridgeError
from wechat_dot_bridge.media import CDN_BASE, MAX_MEDIA, MediaWorker, crypt, fetch
from wechat_dot_bridge.net_transport import NetworkResponse
from wechat_dot_bridge.store import Store
from wechat_dot_bridge.transport import _normalize_message


CDN_URL = CDN_BASE + "/download?encrypted_query_param=fixture"


class NativeAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_round_trips_without_new_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            Config().save(root)
            self.assertNotIn("inbound_file_mib", (root / "config.toml").read_text())
            self.assertEqual(Config.load(root).inbound_file_bytes, MAX_MEDIA)
            for limit in (1, 64):
                config = Config(inbound_file_mib=limit)
                config.save(root)
                self.assertEqual(Config.load(root), config)
                self.assertEqual(config.inbound_file_bytes, limit * 1024**2)
        for limit in (0, 65, True, "20"):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "invalid_inbound_file_mib"):
                    Config(inbound_file_mib=limit).validate()

    async def test_configured_large_file_is_downloaded_stored_and_uploaded(self):
        limit = Config(inbound_file_mib=64).inbound_file_bytes
        data = b"a" * (MAX_MEDIA + 1)
        key = bytes(range(16))
        with tempfile.TemporaryDirectory() as tmp, closing(
            Store(Path(tmp).resolve())
        ) as store:
            store.import_binding(
                {
                    "id": "bot",
                    "owner": "owner",
                    "token": "fixture",
                    "base_url": "https://ilinkai.weixin.qq.com",
                }
            )
            store.set_enabled(True)
            store.ingest(
                "bot",
                [
                    {
                        "source_id": "fixture-file",
                        "peer": "owner",
                        "text": "[微信附件]",
                        "created_at": store.clock(),
                        "attachments": [
                            {
                                "kind": "file",
                                "name": "fixture.bin",
                                "url": CDN_URL,
                                "key": key.hex(),
                            }
                        ],
                    }
                ],
                "cursor",
            )
            mid = store.list_pending()[0]["message_id"]
            mock_response = NetworkResponse(200, crypt(data, key), ())
            with (
                patch("wechat_dot_bridge.media.resolve_addresses"),
                patch("wechat_dot_bridge.media.request", return_value=mock_response) as request,
            ):
                self.assertTrue(MediaWorker(store, max_file_bytes=limit).tick())
                self.assertEqual(request.call_args.kwargs["max_response"], limit + 16)
                self.assertEqual(request.call_args.kwargs["timeout"], 120)
            aid = store.attachment_metadata(mid)[0]["attachment_id"]
            attachment = store.read_attachment(aid)
            self.assertEqual(attachment["data"], data)

            client = DotClient(None, None, max_upload_bytes=limit)
            with patch.object(
                client, "_call", AsyncMock(return_value=Response(201, {"id": "file-room"}, {}))
            ) as call:
                result = await client.upload_file(
                    "room", attachment["name"], "application/octet-stream", attachment["data"]
                )
            self.assertEqual(result, "file-room")
            self.assertEqual(call.await_args.kwargs["upload"].data, data)

    def test_download_distinguishes_empty_from_overflow_even_with_shared_padding(self):
        key = bytes(range(16))
        ref = {"kind": "file", "url": CDN_URL, "key": key.hex()}
        for payload, error in (
            (b"", "media_invalid"),
            (crypt(b"", key), "media_invalid"),
            (crypt(b"a" * 22, key), "media_too_large"),
        ):
            with self.subTest(error=error, encrypted_length=len(payload)):
                with patch("wechat_dot_bridge.media.cdn_request", return_value=(payload, None)) as cdn:
                    with self.assertRaisesRegex(BridgeError, error):
                        fetch(ref, max_bytes=21)
                cdn.assert_called_once_with(CDN_URL, max_download=32)

    async def test_upload_distinguishes_empty_from_overflow_before_request(self):
        client = DotClient(None, None, max_upload_bytes=2)
        with patch.object(client, "_call", AsyncMock()) as call:
            for data, error in ((b"", FileUnavailable), (b"abc", FileTooLarge)):
                with self.subTest(error=error):
                    with self.assertRaises(error):
                        await client.upload_file("room", "file.bin", "application/octet-stream", data)
            call.assert_not_awaited()


class NativeAttachmentDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = NativeStore(Path(tmp.name).resolve())
        self.addCleanup(self.store.close)
        self.store.import_binding(
            dict(id="bot", owner="owner", token="fixture", base_url="https://ilinkai.weixin.qq.com")
        )
        self.store.set_enabled(True)
        binding = Binding("account", "dot", "room", ("agent",), ("owner",), "DOT")
        self.store.pin(binding)
        self.client = SimpleNamespace(
            upload_file=AsyncMock(return_value="file-room"),
            send=AsyncMock(side_effect=self.accept),
        )
        self.incoming = Incoming(self.store, self.client, binding, 86400)

    async def accept(self, room, text, request_id, account, files):
        return Response(201, {"request_id": request_id}, {})

    def add_attachment(self, *, caption="", error=None):
        count = self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0]
        items = [{"type": 4, "file_item": {
            "file_name": "example.bin",
            "media": {"full_url": CDN_URL, "aes_key": base64.b64encode(bytes(16)).decode()},
        }}]
        if caption:
            items.insert(0, {"type": 1, "text_item": {"text": caption}})
        message = _normalize_message(
            dict(
                message_id=count + 1, from_user_id="owner", to_user_id="bot",
                message_type=1, message_state=2, item_list=items,
            ),
            self.store.get_binding(), self.store.clock(), native_messages=True,
        )
        self.assertIsNotNone(message)
        if error:
            message["attachments"][0]["error"] = error
        self.store.ingest("bot", [message], str(count))
        mid = self.store.db.execute("SELECT id FROM messages ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        if not error:
            attachment = self.store.attachment_metadata(mid)[0]
            self.store.finish_attachment(attachment["attachment_id"], b"example")
        return mid

    async def test_pure_attachment_has_no_caption_and_authored_caption_is_kept(self):
        for caption in ("", "请看附件", "[微信附件]"):
            with self.subTest(caption=caption):
                self.add_attachment(caption=caption)
                self.assertTrue(await self.incoming.tick(AsyncMock()))
                room, text, _, account, files = self.client.send.await_args.args
                self.assertEqual((room, text, account, files), ("room", caption, "account", ["file-room"]))

    async def test_empty_caption_rejection_reuses_uploaded_file_and_request_id(self):
        self.add_attachment()

        async def reject_empty(room, text, request_id, account, files):
            if not text:
                return Response(400, {}, {})
            return await self.accept(room, text, request_id, account, files)

        self.client.send.side_effect = reject_empty
        self.assertTrue(await self.incoming.tick(AsyncMock()))
        first, second = [call.args for call in self.client.send.await_args_list]
        self.assertEqual((first[1], second[1]), ("", "[微信附件]"))
        self.assertEqual(first[2:], second[2:])
        self.client.upload_file.assert_awaited_once()

    async def test_distinct_wechat_notices_for_too_large_and_unreadable_files(self):
        for error, hint in (("media_too_large", "超过传输大小上限"), ("media_invalid", "无法读取或上传")):
            with self.subTest(error=error):
                mid = self.add_attachment(error=error)
                self.assertTrue(await self.incoming.tick(AsyncMock()))
                notice = self.store.db.execute(
                    "SELECT body FROM native_out WHERE source=?", ("in:" + mid,)
                ).fetchone()
                self.assertIn(hint, json.loads(notice[0]))
        self.client.send.assert_not_awaited()
        self.client.upload_file.assert_not_awaited()
