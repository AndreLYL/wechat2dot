"""Native operator entry points, process exclusion, offline migration and queue-only MCP."""

import asyncio
from contextlib import closing, redirect_stdout
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from wechat2dot.cli import main
from wechat2dot.config import Config, atomic_file
from wechat2dot.migrate import migrate, source_fingerprint
from wechat2dot.notification_mcp import Notifications
from wechat2dot.service import definition
from wechat2dot.sync.store import NativeStore
from wechat2dot.platform import private_directory
from wechat_dot_bridge.cli import acquire_lock
from wechat_dot_bridge.mcp import MCPServer
from wechat_dot_bridge.store import Store


class Operators(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = private_directory(self.base / "native")
        self.patch = patch.dict(os.environ, {"WECHAT2DOT_HOME": str(self.root)})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_config_paths_and_service_definitions(self):
        config = Config(bot_chat='本人 "机器人"', credentials="file")
        config.save(self.root)
        self.assertEqual(Config.load(self.root), config)
        agent = plistlib.loads(definition(self.root, "darwin")[1])
        self.assertTrue(agent["KeepAlive"])
        self.assertEqual(
            agent["ProgramArguments"][-3:], ["--home", str(self.root), "run"]
        )
        self.assertIn("Restart=always", definition(self.root, "linux")[1].decode())
        task = ET.fromstring(definition(self.root, "win32")[1])
        ns = {"s": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(
            task.findtext(".//s:LogonType", namespaces=ns), "InteractiveToken"
        )
        self.assertIsNotNone(task.find(".//s:RestartOnFailure", ns))

    def test_offline_migration_keeps_ids_and_never_steals_worker_lock(self):
        source = private_directory(self.base / "old")
        state = private_directory(source / "state")
        locks = private_directory(source / "locks")
        atomic_file(
            source / "bridge.json",
            json.dumps({"state_dir": str(state), "lock_dir": str(locks)}),
        )
        with closing(Store(state)) as old:
            old.import_binding(
                {
                    "id": "wx",
                    "owner": "me",
                    "token": "fixture",
                    "base_url": "https://ilinkai.weixin.qq.com",
                }
            )
            old.set_enabled(True)
            old.configure_notifications(enabled=True)
            old.ingest(
                "wx",
                [
                    dict(
                        source_id="before",
                        peer="me",
                        text="old",
                        created_at=old.clock(),
                        context_token="a",
                    )
                ],
                "cursor",
            )
            note = old.queue_notification("test", "only", old.clock() + 400)
            old.claim_notification()
            old.finish_notification(note["notification_id"], "accepted")
        before = source_fingerprint(state)
        with acquire_lock(state / "worker.lock"), self.assertRaises(BlockingIOError):
            migrate(self.root, source)
        self.assertTrue(migrate(self.root, source)["migrated"])
        self.assertTrue(migrate(self.root, source)["reused"])
        self.assertEqual(source_fingerprint(state), before)
        with closing(NativeStore(self.root / "state")) as s:
            self.assertEqual(
                s.notification_status(note["notification_id"])["status"], "accepted"
            )
            self.assertEqual(s.accept_incoming(), 0)
        cfg = Config.load(self.root)
        self.assertEqual(cfg.legacy_state, str(state))
        self.assertEqual(cfg.old_locks, str(locks))
        with closing(Store(state)) as old:
            old.ingest("wx", [], "new-cursor")
        self.assertTrue(migrate(self.root, source)["migrated"])
        with closing(NativeStore(self.root / "state")) as current:
            self.assertEqual(current.get_binding()["cursor"], "new-cursor")
            current.put("identity", ["activated"])
        with closing(Store(state)) as old:
            old.ingest("wx", [], "later-cursor")
        with self.assertRaisesRegex(ValueError, "destination_already_has_state"):
            migrate(self.root, source)

    def test_renew_weixin_preserves_owner_cursor_and_queue(self):
        from wechat2dot.setup_native import renew_weixin

        cfg = Config(credentials="file")
        binding = dict(
            id="account",
            owner="owner",
            token="old",
            base_url="https://ilinkai.weixin.qq.com",
        )
        with closing(NativeStore(self.root / "state")) as target:
            target.import_binding(dict(binding, cursor="last"))
            target.set_enabled(True)
            target.pause_session()
            target.resume_session()
            self.assertFalse(target.get_binding()["enabled"])

            def scan(folder, no_browser):
                with closing(Store(folder)) as fresh:
                    fresh.import_binding(dict(binding, token="new", owner="someone"))

            with (
                patch("wechat2dot.setup_native.bind_weixin", scan),
                self.assertRaisesRegex(ValueError, "binding_changed"),
            ):
                renew_weixin(target, cfg)
            self.assertEqual(target.get_binding()["token"], "old")

            def rescan(folder, no_browser):
                with closing(Store(folder)) as fresh:
                    fresh.import_binding(dict(binding, token="new"))

            with patch("wechat2dot.setup_native.bind_weixin", rescan):
                renew_weixin(target, cfg)
            self.assertEqual(target.get_binding()["token"], "new")
            self.assertEqual(target.get_binding()["cursor"], "last")
            self.assertTrue(target.get_binding()["enabled"])

    def test_upgrade_checks_install_owner_before_stopping_service(self):
        from wechat2dot.admin import execute

        with (
            patch("wechat2dot.admin.shutil.which", return_value="/fixture/uv"),
            patch(
                "wechat2dot.admin.subprocess.check_output", return_value=str(self.base)
            ),
            patch("wechat2dot.service.manage") as service,
        ):
            with self.assertRaisesRegex(ValueError, "does_not_own"):
                execute("upgrade", [])
            service.assert_not_called()

    def test_native_status_without_setup_has_no_database_side_effect(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["status", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["issues"], ["setup_required"])
        self.assertFalse((self.root / "state/bridge.sqlite3").exists())

    def test_status_uses_current_health_not_previous_failures(self):
        with closing(NativeStore(self.root / "state")) as s:
            s.import_binding(
                {
                    "id": "wx",
                    "owner": "me",
                    "token": "fixture",
                    "base_url": "https://ilinkai.weixin.qq.com",
                }
            )
            s.set_enabled(True)
            s.ingest(
                "wx",
                [
                    dict(
                        source_id="x",
                        peer="me",
                        text="/ping",
                        created_at=s.clock(),
                        context_token="context",
                    )
                ],
                "c",
            )
            for key in ("wx_ok", "dot_ok", "heartbeat"):
                s.put(key, s.clock())
            s.event("old_test_failure")
            self.assertTrue(s.status()["healthy"])
            self.assertIn("old_test_failure", s.status()["history_24h"])

    def test_queue_only_mcp_contract(self):
        with closing(NativeStore(self.root / "state")) as s:
            s.import_binding(
                {
                    "id": "wx",
                    "owner": "me",
                    "token": "fixture",
                    "base_url": "https://ilinkai.weixin.qq.com",
                }
            )
            s.set_enabled(True)
            s.configure_notifications(enabled=True)
            api = Notifications(MCPServer(s, None))

            def request(method, params):
                return api.handle(
                    dict(jsonrpc="2.0", id=1, method=method, params=params)
                )

            tools = request("tools/list", {})["result"]["tools"]
            self.assertEqual(
                {t["name"] for t in tools},
                {
                    "send_weixin_notification",
                    "get_weixin_notification_status",
                    "set_weixin_notifications_paused",
                },
            )
            payload = dict(
                name="send_weixin_notification",
                arguments=dict(
                    notification_key="one", text="new", expires_at=s.clock() + 60
                ),
            )
            result = request("tools/call", payload)["result"]["structuredContent"]
            self.assertEqual(result["status"], "queued")
            self.assertEqual(
                request("tools/call", payload)["result"]["structuredContent"][
                    "notification_id"
                ],
                result["notification_id"],
            )
            self.assertIn(
                "error",
                request("tools/call", dict(name="read_weixin_message", arguments={})),
            )


class NativeDaemon(unittest.IsolatedAsyncioTestCase):
    async def test_owner_poll_to_room_and_back_with_persistent_cursors(self):
        from contextlib import ExitStack
        import time
        from unittest.mock import AsyncMock
        from wechat2dot.native import service_loop
        from wechat2dot.dot.client import Binding
        from wechat2dot.dot.http import Response
        from wechat2dot.sync.room import RoomReader

        binding = Binding("account", "dot", "room", ("agent",), ("owner",), "DOT")

        class Room:
            messages_by_id = []
            posts = []

            async def messages(self, *args, **kwargs):
                return list(self.messages_by_id)

            async def send(self, rid, text, request, account, files):
                self.posts.append(text)
                self.messages_by_id.extend(
                    [
                        dict(
                            id="human",
                            account_user_id="owner",
                            created_at=time.time(),
                            content={"text": text},
                            request_id=request,
                        ),
                        dict(
                            id="answered",
                            account_user_id="agent",
                            created_at=time.time(),
                            content={"text": "outbound fixture"},
                        ),
                    ]
                )
                pushed.set()
                return Response(201, {"request_id": request}, {})

        class Socket:
            connected = False

            def __init__(self, dot, event, resync):
                self.event = event

            async def run(self):
                await pushed.wait()
                self.event("room", "message")
                await asyncio.Event().wait()

        received = []

        def poll(binding_row):
            msgs = (
                []
                if binding_row["cursor"] == "seen"
                else [
                    dict(
                        from_user_id="owner",
                        to_user_id="wx",
                        message_id=123,
                        context_token="fixture-context",
                        message_type=1,
                        message_state=2,
                        item_list=[
                            {"type": 1, "text_item": {"text": "inbound fixture"}}
                        ],
                    )
                ]
            )
            return {"msgs": msgs, "get_updates_buf": "seen"}

        def send(binding_row, payload):
            received.append(payload["text"])
            return {"ret": 0}

        pushed = asyncio.Event()
        room = Room()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = private_directory(Path(tmp).resolve() / "root")
            s = stack.enter_context(closing(NativeStore(root / "state")))
            s.import_binding(
                dict(
                    id="wx",
                    owner="owner",
                    token="fixture",
                    base_url="https://ilinkai.weixin.qq.com",
                )
            )
            s.set_enabled(True)
            s.pin(binding)
            s.put("members", [binding.dot_members, binding.human_members])
            cfg = Config(
                credentials="file",
                account_id="account",
                room_id="room",
                dot_id="dot",
                auto_ping=False,
            )
            await RoomReader(s, room, binding).read()
            stack.enter_context(patch.dict(os.environ, {"WECHAT2DOT_HOME": str(root)}))
            stack.enter_context(
                patch("wechat2dot.native.discover", AsyncMock(return_value=binding))
            )
            stack.enter_context(patch("wechat2dot.native.DotClient", return_value=room))
            stack.enter_context(patch("wechat2dot.native.DotLive", Socket))
            stack.enter_context(
                patch("wechat_dot_bridge.transport.IlinkClient.poll", side_effect=poll)
            )
            stack.enter_context(
                patch("wechat_dot_bridge.transport.IlinkClient.send", side_effect=send)
            )
            worker = asyncio.create_task(service_loop(root, cfg, s, stack))
            try:
                async with asyncio.timeout(15):
                    while not received:
                        if worker.done():
                            worker.result()
                        await asyncio.sleep(0.1)
                self.assertEqual(room.posts, ["inbound fixture"])
                self.assertEqual(received, ["outbound fixture"])
                self.assertEqual(s.status()["outbox"], {"accepted": 1})
                self.assertEqual(s.status()["inbox"], {"accepted": 1})
            except TimeoutError:
                self.fail(
                    f"daemon did not complete round trip: {s.status()}, room posts {room.posts!r}, seen {list(map(tuple, s.db.execute('select id,state from native_seen')))}"
                )
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
