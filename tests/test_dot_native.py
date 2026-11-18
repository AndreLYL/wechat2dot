import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from wechat2dot.dot.auth import Auth, AuthRequired, TokenFile, account_id_of
from wechat2dot.dot.client import (DotClient, DotError, DotProtocolError, binding_for,
                                   in_progress, oldest_first, sender_of)
from wechat2dot.dot.http import Response
from wechat2dot.dot.live import DotLive, valid_socket_url


def jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def token(account="acct-1", exp=4_000_000_000):
    return jwt({"exp": exp, "https://api.openai.com/auth": {"chatgpt_account_id": account}})


DOT = {"id": "dot_1", "messaging_room_id": "room_1", "display_name": "",
       "messaging_room_preview": {"name": "娜娜", "members": [
           {"account_user_id": "u_dot", "aeon_id": "aeon_1"},
           {"account_user_id": "u_me"}]}}


class FakeHttp:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class TokenFileTest(unittest.TestCase):
    def test_save_is_private_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as root:
            store = TokenFile(Path(root) / "state" / "login.json")
            store.save({"access_token": "a", "account_id": "x"})
            self.assertEqual(store.load()["access_token"], "a")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            store.clear()
            self.assertIsNone(store.load())


class AuthTest(unittest.IsolatedAsyncioTestCase):
    def store(self, root, **tokens):
        file = TokenFile(Path(root) / "private" / "login.json")
        file.save({"access_token": token(), "refresh_token": "r1", "account_id": "acct-1", **tokens})
        return file

    async def test_fresh_token_is_used_without_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            http = FakeHttp([])
            access, account = await Auth(self.store(root), http).access()
            self.assertEqual(account, "acct-1")
            self.assertEqual(http.calls, [])

    async def test_expiring_token_refreshes_and_rotates(self):
        with tempfile.TemporaryDirectory() as root:
            file = self.store(root, access_token=token(exp=1))
            new = token()
            http = FakeHttp([Response(200, {"access_token": new, "refresh_token": "r2"}, {})])
            access, _ = await Auth(file, http, clock=lambda: 1000).access()
            self.assertEqual(access, new)
            self.assertEqual(file.load()["refresh_token"], "r2")

    async def test_dead_grant_clears_login(self):
        with tempfile.TemporaryDirectory() as root:
            file = self.store(root, access_token=token(exp=1))
            http = FakeHttp([Response(400, {"error": "refresh_token_reused"}, {})])
            with self.assertRaises(AuthRequired):
                await Auth(file, http, clock=lambda: 1000).access()
            self.assertIsNone(file.load())

    async def test_edge_html_error_keeps_login(self):
        with tempfile.TemporaryDirectory() as root:
            file = self.store(root, access_token=token(exp=1))
            http = FakeHttp([Response(403, None, {})])
            with self.assertRaises(Exception):
                await Auth(file, http, clock=lambda: 1000).access()
            self.assertIsNotNone(file.load())

    async def test_refresh_cannot_switch_account(self):
        with tempfile.TemporaryDirectory() as root:
            file = self.store(root, access_token=token(exp=1))
            http = FakeHttp([Response(200, {"access_token": token(account="other")}, {})])
            with self.assertRaises(AuthRequired):
                await Auth(file, http, clock=lambda: 1000).access()

    def test_account_claim(self):
        self.assertEqual(account_id_of({"access_token": token("abc")}), "abc")


class StaticAuth:
    def __init__(self):
        self.refreshed, self.rejected = 0, []

    async def access(self):
        return "t1", "acct-1"

    async def refreshed_access(self, rejected):
        self.refreshed += 1
        return ("t2", "acct-1") if rejected == "t1" else None

    async def reject(self, access):
        self.rejected.append(access)


class ClientTest(unittest.IsolatedAsyncioTestCase):
    def test_binding_identifies_dot_by_aeon_member(self):
        binding = binding_for(DOT, "acct-1")
        self.assertEqual(binding.dot_members, ("u_dot",))
        self.assertEqual(binding.human_members, ("u_me",))
        self.assertEqual(binding.name, "娜娜")

    def test_binding_never_creates_rooms(self):
        with self.assertRaises(DotError) as caught:
            binding_for({**DOT, "messaging_room_id": None}, "acct-1")
        self.assertEqual(caught.exception.code, "no_room")

    async def test_401_retries_once_with_refreshed_token(self):
        auth = StaticAuth()
        http = FakeHttp([Response(401, None, {}), Response(200, {"items": [DOT]}, {})])
        dots = await DotClient(http, auth).list_dots()
        self.assertEqual([d["id"] for d in dots], ["dot_1"])
        self.assertEqual(auth.refreshed, 1)
        self.assertEqual(http.calls[1][2]["headers"]["Authorization"], "Bearer t2")

    async def test_second_401_requires_login(self):
        auth = StaticAuth()
        http = FakeHttp([Response(401, None, {}), Response(401, None, {})])
        with self.assertRaises(AuthRequired):
            await DotClient(http, auth).list_dots()
        self.assertEqual(auth.rejected, ["t2"])

    async def test_changed_shape_is_a_protocol_error(self):
        http = FakeHttp([Response(200, {"data": []}, {})])
        with self.assertRaises(DotProtocolError):
            await DotClient(http, StaticAuth()).messages("room_1")

    async def test_send_carries_request_id_and_idempotency_token(self):
        http = FakeHttp([Response(201, {"request_id": "rq"}, {})])
        await DotClient(http, StaticAuth()).send("room_1", "你好", "rq")
        body = http.calls[0][2]["json_body"]
        self.assertEqual((body["request_id"], body["idempotency_token"]), ("rq", "rq"))

    def test_message_helpers(self):
        items = [{"id": "b", "created_at": "2026-11-18T10:00:01Z"},
                 {"id": "a", "created_at": "2026-11-18T10:00:00Z", "account_user_id": "u_dot",
                  "status": "in_progress"}]
        ordered = oldest_first(items)
        self.assertEqual([i["id"] for i in ordered], ["a", "b"])
        self.assertEqual(sender_of(ordered[0]), "u_dot")
        self.assertTrue(in_progress(ordered[0]))


class ChunkedStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def _gen(self):
        for chunk in self.chunks:
            yield chunk

    def iter_chunked(self, size):
        return self._gen()


class ReadBoundedTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_every_chunk_not_just_the_buffer(self):
        from wechat2dot.dot.http import read_bounded
        data = await read_bounded(ChunkedStream([b'{"items"', b': []}']), 100, ValueError())
        self.assertEqual(json.loads(data), {"items": []})

    async def test_limit_is_enforced_across_chunks(self):
        from wechat2dot.dot.http import read_bounded
        with self.assertRaises(ValueError):
            await read_bounded(ChunkedStream([b"x" * 60, b"x" * 60]), 100, ValueError())


class LiveTest(unittest.TestCase):
    def test_socket_origin_is_pinned(self):
        self.assertTrue(valid_socket_url("wss://ws.chatgpt.com/p3/ws/user/u_1?x=1"))
        self.assertFalse(valid_socket_url("wss://evil.example/p3/ws/user/u_1"))
        self.assertFalse(valid_socket_url("ws://ws.chatgpt.com/p3/ws/user/u_1"))

    def test_subscribe_reply_resyncs_and_events_trigger(self):
        events, resyncs = [], []
        live = DotLive(None, lambda room, kind: events.append((room, kind)), lambda: resyncs.append(1))
        subscribed = live.handle(json.dumps([
            {"id": 2, "reply": {"type": "subscribe", "topic_id": "calpico-chatgpt-messaging",
                                "last_offset": "o9"}},
            {"type": "message", "topic_id": "calpico-chatgpt-messaging",
             "payload": {"type": "calpico-message-add", "payload": {"room_id": "room_1"}}},
            {"type": "message", "topic_id": "calpico-chatgpt-messaging",
             "payload": {"type": "calpico-typing", "payload": {"room_id": "room_1"}}}]))
        self.assertTrue(subscribed)
        self.assertEqual(resyncs, [1])
        self.assertEqual(events, [("room_1", "calpico-message-add")])
        self.assertIn('"offset": "o9"', live.commands())

    def test_garbage_frames_are_ignored(self):
        live = DotLive(None, lambda *a: self.fail("no event"), lambda: self.fail("no resync"))
        self.assertFalse(live.handle("not json"))
        self.assertFalse(live.handle(json.dumps({"type": "message"})))


if __name__ == "__main__":
    unittest.main()
