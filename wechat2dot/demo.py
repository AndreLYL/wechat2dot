"""Offline demonstration: real Linux engine, synthetic WeChat and dot boundaries."""

import base64
import hashlib
import hmac
import json
import tempfile


def _check(condition, detail="Demo invariant failed"):
    if not condition:
        raise RuntimeError(detail)


def preview():
    from wechat_dot_bridge.tool_contract import PROTOCOL, tool_definitions
    return {"mode": "protocol_preview", "engine_e2e": "not_run", "network_requests": 0,
            "real_dot_connected": False, "real_wechat_connected": False,
            "protocol": PROTOCOL, "event": "weixin.message",
            "tools": [tool["name"] for tool in tool_definitions()],
            "flow": ["WeChat -> durable inbox", "signed event -> existing dot",
                     "read -> interim reply -> final reply", "durable outbox -> WeChat"],
            "next": "Run wechat2dot demo on Linux or in Docker to exercise the engine."}


def run_demo():
    from wechat_dot_bridge.events import Events
    from wechat_dot_bridge.mcp import MCPServer
    from wechat_dot_bridge.store import Store
    from wechat_dot_bridge.transport import IlinkClient, WeixinWorker

    now = 1800001000.0
    clock = lambda: now
    owner, account = "demo@im.wechat", "demo@im.bot"
    signing_key = b"demo-signing-key-not-a-credential"
    callback_url = "https://callback.example.com/offline-demo"
    callbacks, sent = [], []

    class OfflineWeChat(IlinkClient):
        def _post(self, binding, path, body, timeout, allow_empty=False):
            if path == "/ilink/bot/getupdates":
                return {"ret": 0, "get_updates_buf": "demo-next", "msgs": [{
                    "message_id": 18446744073709551615, "from_user_id": owner,
                    "to_user_id": account, "create_time_ms": int(now * 1000),
                    "message_type": 1, "message_state": 2, "context_token": "synthetic-context",
                    "item_list": [{"type": 1, "text_item": {"text": "帮我整理会议要点"}}]}]}
            if path == "/ilink/bot/sendmessage":
                sent.append(body)
                return {"ret": 0}
            raise AssertionError("Unexpected network boundary")

    def callback(url, body, headers):
        _check(url == callback_url)
        material = (headers["webhook-id"] + "." + headers["webhook-timestamp"] + ".").encode() + body
        signature = "v1," + base64.b64encode(hmac.new(signing_key, material, hashlib.sha256).digest()).decode()
        _check(signature in headers["webhook-signature"].split())
        payload = json.loads(body)
        if payload.get("type") == "verification":
            return 200, json.dumps({"challenge": payload["challenge"]}).encode()
        _check(set(payload["data"]) == {"message_id"})
        callbacks.append(payload)
        return 200, b"{}"

    with tempfile.TemporaryDirectory(prefix="wechat2dot-demo-") as directory:
        store = Store(directory, clock=clock)
        try:
            store.import_binding({"id": account, "owner": owner, "token": "synthetic-token",
                                  "base_url": "https://ilinkai.weixin.qq.com", "cursor": "demo-initial"})
            store.set_enabled(True)
            client = OfflineWeChat(clock)
            worker = WeixinWorker(store, client)
            events = Events(store, allowed_hosts=["callback.example.com"], post=callback, clock=clock)
            server = MCPServer(store, events)

            def request(method, params):
                response = server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                _check("error" not in response)
                return response["result"]

            def call(name, arguments):
                result = request("tools/call", {"name": name, "arguments": arguments})
                _check(not result["isError"])
                return result["structuredContent"]

            request("events/subscribe", {"name": "weixin.message", "arguments": {},
                    "delivery": {"mode": "webhook", "url": callback_url,
                                 "secret": "whsec_" + base64.b64encode(signing_key).decode()}})
            _check(worker.poll_once())
            _check(events.deliver_once())
            identifier = callbacks[0]["data"]["message_id"]
            read = call("read_weixin_message", {"message_id": identifier})
            _check(read["text"] == "帮我整理会议要点")
            for key, text, final in [("progress", "收到，正在整理。", False),
                                      ("result", "演示结果：行动项、负责人、截止日期。", True)]:
                arguments = {"message_id": identifier, "text": text, "reply_key": key, "final": final}
                queued = call("reply_weixin_message", arguments)
                _check(queued["status"] == "queued")
                _check(worker.send_once())
                _check(call("get_weixin_send_status", {"message_id": identifier,
                           "reply_id": queued["reply_id"]})["status"] == "accepted")
                _check(call("reply_weixin_message", arguments)["status"] == "accepted")
            _check(worker.poll_once())
            _check(not events.deliver_once())
            _check(not worker.send_once())
            _check(len(callbacks) == 1 and len(sent) == 2)
            _check(all(post["msg"]["to_user_id"] == owner for post in sent))
            store.close()
            store = Store(directory, clock=clock)
            store.recover_inflight()
            _check(not WeixinWorker(store, client).send_once())
            _check(len(sent) == 2)
        finally:
            store.close()
    return {"mode": "engine_fixture", "engine_e2e": "passed", "network_requests": 0,
            "real_dot_connected": False, "real_wechat_connected": False,
            "checks": ["signed challenge", "ID-only event", "read inbound text", "interim reply",
                       "final reply", "reply deduplication", "restart without replay"],
            "simulated_wechat": ["帮我整理会议要点", "收到，正在整理。", "演示结果：行动项、负责人、截止日期。"],
            "note": "The dot tool calls and all network responses are synthetic; this is not a live validation."}
