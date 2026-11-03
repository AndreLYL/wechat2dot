"""Offline notification lifecycle and real transport-builder fixtures. No accounts."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from wechat_dot_bridge import migrations
from wechat_dot_bridge.errors import BridgeError
from wechat_dot_bridge.mcp import MCPServer
from wechat_dot_bridge.store import Store
from wechat_dot_bridge.transport import IlinkClient, WeixinWorker


class FixtureClient(IlinkClient):
    def __init__(self):
        super().__init__()
        self.posts = []
        self.response = {}
        self.failure = None

    def _post(self, binding, path, body, timeout, allow_empty=False):
        assert path == "/ilink/bot/sendmessage"
        self.posts.append(body)
        if self.failure:
            raise BridgeError(self.failure)
        return self.response


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1800000000.0
        self.path = Path(self.temp.name).resolve() / "state"
        self.store = Store(self.path, clock=lambda: self.now)
        self.store.import_binding({"id": "fixture-account", "owner": "fixture-owner", "token": "fixture-token",
                                   "base_url": "https://ilinkai.weixin.qq.com"})
        self.store.set_enabled(True)
        self.client = FixtureClient()
        self.worker = WeixinWorker(self.store, self.client)
        self.server = MCPServer(self.store, None)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def incoming(self, key="incoming", context="fixture-context", peer="fixture-owner"):
        self.store.ingest("fixture-account", [{"source_id": key, "peer": peer, "text": "fixture input",
            "created_at": self.now, "context_token": context}], "fixture-cursor")
        return self.store.list_pending()[-1]["message_id"]

    def enable(self, **kwargs):
        self.store.configure_notifications(enabled=True, **kwargs)

    def queue(self, key="test", **kwargs):
        return self.store.queue_notification(kwargs.get("text", "fixture notice"), key, kwargs.get("expires_at", self.now + 3600))

    def tool(self, name, **args):
        return self.server._call({"name": name, "arguments": args})

    def test_default_off_and_mcp_cannot_grant_opt_in_or_choose_recipient(self):
        self.assertFalse(self.store.notification_status()["enabled"])
        self.assertEqual(self.tool("send_weixin_notification", text="x", notification_key="x", expires_at=self.now+60)["structuredContent"], {"error": "notifications_disabled"})
        self.assertEqual(self.tool("set_weixin_notifications_paused", paused=False)["structuredContent"], {"error": "notifications_disabled"})
        for field in ("peer", "recipient", "message_id", "processing_id", "enabled"):
            with self.subTest(field=field), self.assertRaises(BridgeError):
                self.tool("send_weixin_notification", text="x", notification_key="x", expires_at=self.now+60, **{field: "other"})

    def test_notification_does_not_create_or_complete_chat_work_and_uses_real_wire(self):
        mid = self.incoming()
        before = self.store.work_status(mid)
        self.enable()
        result = self.queue()
        self.assertTrue(self.worker.send_once())
        self.assertEqual(self.store.notification_status(result["notification_id"])["status"], "accepted")
        self.assertEqual(self.store.work_status(mid), before)
        self.assertEqual(len(self.store.list_pending()), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM replies").fetchone()[0], 0)
        wire = self.client.posts[0]["msg"]
        self.assertEqual(wire["to_user_id"], "fixture-owner")
        self.assertEqual(wire["context_token"], "fixture-context")
        self.assertEqual(wire["client_id"], result["notification_id"])
        self.assertEqual(wire["item_list"], [{"type": 1, "text_item": {"text": "fixture notice"}}])
        safe = json.dumps(self.store.notification_status())
        for private in ("fixture notice", "fixture-token", "fixture-context", "fixture-owner"):
            self.assertNotIn(private, safe)

    def test_no_route_waits_for_owner_context_without_fake_inbound(self):
        self.enable()
        item = self.queue()
        self.assertFalse(self.worker.send_once())
        self.assertEqual(self.store.notification_status(item["notification_id"])["status"], "context_required")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
        with self.assertRaisesRegex(BridgeError, "owner_identity_mismatch"):
            self.incoming(peer="outsider")
        self.assertFalse(self.store.notification_status()["context_available"])
        self.incoming()
        self.assertTrue(self.worker.send_once())

    def test_context_rejection_retries_only_after_new_context(self):
        self.incoming()
        self.enable()
        item = self.queue()
        self.client.response = {"ret": 123, "errmsg": "context_token expired"}
        self.assertTrue(self.worker.send_once())
        self.assertEqual(self.store.notification_status(item["notification_id"])["status"], "context_required")
        self.assertFalse(self.worker.send_once())
        self.now += 1
        self.incoming("fresh", "new-context")
        self.client.response = {}
        self.assertTrue(self.worker.send_once())
        self.assertEqual(self.client.posts[-1]["msg"]["context_token"], "new-context")

    def test_duplicate_key_survives_restart_and_expiry_without_resending(self):
        self.incoming()
        self.enable()
        expires = self.now + 60
        first = self.queue(expires_at=expires)
        self.worker.send_once()
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.now += 120
        repeated = self.queue(expires_at=expires)
        self.assertEqual(first["notification_id"], repeated["notification_id"])
        self.assertEqual(repeated["status"], "accepted")
        self.assertIsNone(self.store.claim_notification())
        self.assertEqual(self.store.db.execute("SELECT text FROM notifications").fetchone()[0], "")
        with self.assertRaisesRegex(BridgeError, "notification_key_conflict"):
            self.queue(expires_at=expires+1)

    def test_paused_and_quiet_notifications_expire_without_blocking_fresh(self):
        self.incoming()
        self.enable()
        old = self.queue("old", expires_at=self.now+60)
        self.store.set_notifications_paused(True)
        with self.assertRaisesRegex(BridgeError, "notifications_paused"):
            self.queue("new")
        self.assertIsNone(self.store.claim_notification())
        self.now += 61
        self.assertEqual(self.store.notification_status(old["notification_id"])["status"], "expired")
        self.store.set_notifications_paused(False)
        fresh = self.queue("fresh")
        self.worker.send_once()
        self.assertEqual(self.store.notification_status(fresh["notification_id"])["status"], "accepted")
        self.assertEqual(len(self.client.posts), 1)

    def test_quiet_hours_overnight_and_daytime_boundaries_fixed_offset(self):
        self.incoming()
        for start, end, blocked, open_at in [("22:00", "08:00", 23*60, 8*60), ("09:00", "17:00", 9*60, 17*60)]:
            with self.subTest(start=start):
                self.now = 1800057600.0  # set exact local wall minutes explicitly below
                self.now = (self.now // 86400) * 86400 + (blocked-480)*60
                self.enable(quiet_start=start, quiet_end=end, utc_offset_minutes=480)
                self.queue(start.replace(":", ""), expires_at=self.now+86400)
                self.assertIsNone(self.store.claim_notification())
                delta = (open_at-blocked) % 1440
                self.now += delta*60
                self.assertIsNotNone(self.store.claim_notification())

    def test_restarting_inflight_is_uncertain_never_auto_replayed(self):
        self.incoming()
        self.enable()
        item = self.queue()
        self.store.claim_notification()
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.store.recover_inflight()
        self.assertEqual(self.store.notification_status(item["notification_id"])["status"], "uncertain")
        self.now += 7200
        self.incoming("new")
        self.assertIsNone(self.store.claim_notification())
        self.assertEqual(self.store.notification_status(item["notification_id"])["status"], "uncertain")

    def test_failure_classification_does_not_blindly_retry(self):
        self.incoming()
        self.enable()
        for key, response, failure, expected in [("reject", {"ret": 123}, None, "failed"),
                ("timeout", {}, "network_timeout", "uncertain"), ("invalid", {"unexpected": 1}, None, "uncertain")]:
            self.client.response, self.client.failure = response, failure
            item = self.queue(key)
            self.assertTrue(self.worker.send_once())
            self.assertEqual(self.store.notification_status(item["notification_id"])["status"], expected)
            self.assertFalse(self.worker.send_once())

    def test_notification_auth_failure_stops_sending_and_is_visible(self):
        self.incoming()
        self.enable()
        item = self.queue()
        self.client.response = {"ret": -14}
        self.worker.send_once()
        self.assertEqual(self.store.notification_status(item["notification_id"])["status"], "auth_required")
        self.assertFalse(self.worker.send_once())

    def test_existing_active_and_completed_work_generation_remain_untouched(self):
        mid = self.incoming()
        self.store.read_message(mid)
        self.enable()
        for state in ("active", "completed"):
            if state == "completed":
                self.store.report_progress(mid, False, completed=True)
            before = self.store.work_status(mid)
            self.queue(state)
            self.worker.send_once()
            self.assertEqual(self.store.work_status(mid), before)

    def test_reply_and_notification_share_sender_without_starvation(self):
        mid = self.incoming()
        self.enable()
        for i in range(3):
            self.store.queue_reply(mid, "reply", reply_key=str(i))
            self.queue(str(i))
        for _ in range(6):
            self.assertTrue(self.worker.send_once())
        texts = [p["msg"]["item_list"][0]["text_item"]["text"] for p in self.client.posts]
        self.assertEqual(texts, ["reply", "fixture notice"] * 3)

    def test_status_scope_and_notification_binding_are_owner_only(self):
        self.incoming()
        self.enable()
        item = self.queue()
        with self.assertRaisesRegex(BridgeError, "binding_identity_changed"):
            self.store.import_binding({"id":"other", "owner":"other", "token":"fixture-other", "base_url":"https://ilinkai.weixin.qq.com"})
        with self.store._transaction():  # model stale foreign data, not a supported identity change
            self.store.db.execute("UPDATE notifications SET peer='other'")
        with self.assertRaisesRegex(BridgeError, "unknown_notification"):
            self.store.notification_status(item["notification_id"])
        self.assertIsNone(self.store.claim_notification())

    def test_route_survives_history_removal_and_is_not_reset_by_empty_poll(self):
        self.incoming()
        self.enable()
        # Delete fixture chat graph to prove no notification FK to historical tasks.
        for table in ("message_timeline", "message_work", "message_aliases", "messages"):
            self.store.db.execute("DELETE FROM " + table)
        self.store.ingest("fixture-account", [], "next")
        self.queue()
        self.assertTrue(self.worker.send_once())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_invalid_payloads_and_queue_bounds(self):
        self.enable()
        for expiry in (self.now, float("nan"), float("inf"), True, self.now+7*86400+1):
            with self.subTest(expiry=expiry), self.assertRaises(BridgeError):
                self.queue(expires_at=expiry)
        for text in (" ", "x"*2001, "\ud800"):
            with self.assertRaises(BridgeError):
                self.queue(text=text)
        for i in range(32):
            self.queue(str(i))
        with self.assertRaisesRegex(BridgeError, "notification_queue_full"):
            self.queue("overflow")

    def test_simultaneous_tool_retries_enqueue_and_claim_once(self):
        self.incoming()
        self.enable()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.queue(), range(12)))
            claims = list(pool.map(lambda _: self.store.claim_notification(), range(12)))
        self.assertEqual(len({r["notification_id"] for r in results}), 1)
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_disable_pause_and_quiet_hours_do_not_block_chat_replies(self):
        mid = self.incoming()
        self.enable()
        notice = self.queue()
        for state in ("paused", "disabled", "quiet"):
            if state == "paused":
                self.store.set_notifications_paused(True)
            elif state == "disabled":
                self.store.configure_notifications(enabled=False)
            else:
                self.now = (self.now // 86400) * 86400 + 12*3600
                self.enable(quiet_start="11:00", quiet_end="13:00", utc_offset_minutes=0)
                self.store.set_notifications_paused(False)
            self.store.queue_reply(mid, "chat reply", reply_key=state)
            self.assertTrue(self.worker.send_once())
            self.assertFalse(self.worker.send_once())
        self.assertEqual(len(self.client.posts), 3)
        self.assertEqual(self.store.notification_status(notice["notification_id"])["attempts"], 0)

    def test_cached_route_cannot_regress_after_partial_cleanup_or_delayed_inbound(self):
        self.incoming("old", "old-context")
        self.now += 10
        newest = self.incoming("new", "new-context")
        for table in ("message_timeline", "message_work", "message_aliases"):
            self.store.db.execute("DELETE FROM " + table + " WHERE message_id=?", (newest,))
        self.store.db.execute("DELETE FROM messages WHERE id=?", (newest,))
        self.store.ingest("fixture-account", [], "next")
        self.now -= 5
        self.incoming("delayed", "delayed-context")
        self.enable()
        self.queue()
        self.worker.send_once()
        self.assertEqual(self.client.posts[0]["msg"]["context_token"], "new-context")

    def test_context_retries_are_bounded_and_rate_limit_is_not_context_failure(self):
        self.incoming()
        self.enable()
        item = self.queue()
        self.client.response = {"ret": 123, "errmsg": "context token expired"}
        for i in range(3):
            self.now += 1
            self.incoming(str(i), "context-" + str(i))
            self.assertTrue(self.worker.send_once())
        self.now += 1
        self.incoming("limit", "limit-context")
        self.assertFalse(self.worker.send_once())
        status = self.store.notification_status(item["notification_id"])
        self.assertEqual((status["status"], status["attempts"]), ("failed", 3))
        rate = self.queue("rate")
        self.client.response = {"ret": -2}
        self.worker.send_once()
        self.assertEqual(self.store.notification_status(rate["notification_id"])["error"], "rate_limited")
        from wechat_dot_bridge.operations import diagnostics
        self.assertTrue(any(i["code"] == "notification_failed" for i in diagnostics(self.store)["issues"]))

    def test_expiry_during_inflight_does_not_erase_send_result(self):
        self.incoming()
        self.enable()
        for outcome in ("accepted", "uncertain"):
            notice = self.queue(outcome, expires_at=self.now+1)
            self.store.claim_notification()
            self.now += 2
            self.store.finish_notification(notice["notification_id"], outcome)
            self.assertIsNone(self.store.claim_notification())
            self.assertEqual(self.store.notification_status(notice["notification_id"])["status"], outcome)

    def test_cli_opt_in_policy_validation_and_single_consumer_lock(self):
        from wechat_dot_bridge.cli import acquire_lock
        from wechat2dot.platform import safe_open
        config = self.path / "fixture.json"
        with os.fdopen(safe_open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
            json.dump({"state_dir": str(self.path), "lock_dir": str(self.path / "locks")}, stream)
        command = [sys.executable, "-m", "wechat2dot", "--config", str(config), "notifications"]
        def run(*args):
            return subprocess.run([*command, *args], capture_output=True, text=True, timeout=15)
        result = run("--enable", "--quiet-start", "22:00", "--quiet-end", "08:00")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["enabled"])
        invalid = run("--enable", "--quiet-start", "25:00", "--quiet-end", "08:00")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(self.store.notification_status()["quiet_start"], "22:00")
        with acquire_lock(self.path / "worker.lock"):
            locked = run("--disable")
            self.assertEqual(locked.returncode, 2)
            self.assertIn("already_running", locked.stderr)
        self.assertEqual(run("--disable").returncode, 0)
        self.assertFalse(self.store.notification_status()["enabled"])

    def test_backup_restore_keeps_dedup_and_disables_delivery(self):
        from wechat_dot_bridge.operations import backup, restore
        self.incoming()
        self.enable()
        notice = self.queue()
        self.worker.send_once()
        snapshot = Path(self.temp.name).resolve() / "backup" / "snapshot.sqlite3"
        backup(self.store, snapshot)
        restored = Path(self.temp.name).resolve() / "restored"
        restore(snapshot, restored)
        with closing(Store(restored, clock=lambda: self.now)) as target:
            self.assertFalse(target.get_binding()["enabled"])
            self.assertIsNone(target.claim_notification())
            target.set_enabled(True)
            repeat = target.queue_notification("fixture notice", "test", self.now+3600)
            self.assertEqual(repeat["notification_id"], notice["notification_id"])
            self.assertEqual(repeat["status"], "accepted")
            self.assertIsNone(target.claim_notification())

    def test_mcp_wakes_only_sender_and_query_does_not_disclose_body(self):
        self.enable()
        wakes = []
        self.server.wake_workers = lambda *names: wakes.append(names)
        result = self.tool("send_weixin_notification", text="private fixture", notification_key="tool", expires_at=self.now+600)
        self.assertFalse(result["isError"])
        self.assertEqual(wakes, [("send",)])
        nid = result["structuredContent"]["notification_id"]
        status = self.tool("get_weixin_notification_status", notification_id=nid)
        self.assertNotIn("private fixture", json.dumps(status))

    def test_migration_seeds_only_owner_route_and_rolls_back_atomically(self):
        database = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(database.close)
        with patch.object(migrations, "MIGRATIONS", migrations.MIGRATIONS[:-1]), patch.object(migrations, "SCHEMA_VERSION", migrations.SCHEMA_VERSION-1):
            migrations.migrate(database, clock=lambda: self.now)
        database.execute("INSERT INTO binding(id,owner,token,base_url) VALUES('a','owner','fixture','https://ilinkai.weixin.qq.com')")
        database.execute("INSERT INTO messages(id,account,peer,source_id,text,created_at,context_token) VALUES('m','a','owner','s','fixture',1,'owner-context')")
        database.execute("INSERT INTO messages(id,account,peer,source_id,text,created_at,context_token) VALUES('x','a','outsider','x','fixture',2,'foreign-context')")
        original = migrations.MIGRATIONS[-1]
        def fail(db, clock):
            original(db, clock)
            raise RuntimeError("fixture")
        with patch.object(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:-1], fail)), self.assertRaises(RuntimeError):
            migrations.migrate(database)
        self.assertIsNone(database.execute("SELECT name FROM sqlite_master WHERE name='notifications'").fetchone())
        migrations.migrate(database)
        self.assertEqual(database.execute("SELECT context_token FROM notification_routes").fetchone()[0], "owner-context")
        self.assertEqual(database.execute("SELECT count(*) FROM notification_policy").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
