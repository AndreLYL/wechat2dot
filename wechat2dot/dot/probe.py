"""M0 checks of the native dot protocol on the owner's own account.

Output describes structure only (field names, counts, sizes); message text is shown
only with --show-text.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
import webbrowser
from collections import Counter

from .auth import Auth, AuthRequired, AuthUnavailable, TokenFile
from .client import (
    DotClient,
    DotError,
    attachments_of,
    binding_for,
    in_progress,
    message_text,
    sender_of,
    timestamp_of,
)
from .http import Http, NetworkError
from .live import DotLive

SUBSCRIBE_WAIT = 15


def _clients():
    from wechat2dot.paths import chatgpt_token_file
    from wechat2dot.config import Config
    from wechat2dot.paths import data_dir
    from wechat2dot.vault import token_store

    root = data_dir()
    cfg = Config.load(root, required=False)
    http = Http(dns=cfg.dns)
    tokens = (
        token_store(cfg)
        if (root / "config.toml").exists()
        else TokenFile(chatgpt_token_file())
    )
    auth = Auth(tokens, http)
    return http, auth, DotClient(http, auth)


async def login(open_browser=True) -> dict:
    http, auth, _ = _clients()
    try:
        challenge = await auth.device_start()
        print(
            f"在浏览器打开 {challenge.verification_url}，输入代码：{challenge.user_code}",
            flush=True,
        )
        print("（代码 15 分钟内有效；登录后回到这里等待。）", flush=True)
        if open_browser:
            webbrowser.open(challenge.verification_url)
        account = await auth.device_complete(challenge)
        return {"logged_in": True, "account_id_suffix": account[-6:]}
    except (AuthUnavailable, TimeoutError) as exc:
        return {"logged_in": False, "error": type(exc).__name__, "detail": str(exc)}
    finally:
        await http.close()


def _shape(value, depth=0):
    """Field names and types without values."""
    if isinstance(value, dict) and depth < 4:
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def _summary(item, binding, show_text):
    sender = sender_of(item)
    who = (
        "dot"
        if sender in binding.dot_members
        else "human"
        if sender in binding.human_members
        else "unknown"
    )
    text = message_text(item)
    row = {
        "id_suffix": str(item.get("id"))[-8:],
        "from": who,
        "created_at": timestamp_of(item),
        "updated_after_s": None,
        "text_chars": len(text),
        "attachments": len(attachments_of(item)),
        "in_progress": in_progress(item),
        "request_id": bool(item.get("request_id")),
        "trigger": (item.get("message_metadata") or {}).get("turn_trigger")
        if isinstance(item.get("message_metadata"), dict)
        else None,
        "content_keys": sorted(item.get("content", {}).keys())
        if isinstance(item.get("content"), dict)
        else None,
    }
    created, updated = timestamp_of(item), timestamp_of(item, "updated_at")
    if created and updated:
        row["updated_after_s"] = round(updated - created, 3)
    if show_text:
        row["text"] = text[:80]
    return row


async def probe(dot_id=None, limit=20, post=None, listen=0, show_text=False) -> dict:
    http, auth, client = _clients()
    report = {"checked_at": time.time()}
    try:
        state = auth.state()
        if not state["logged_in"]:
            raise AuthRequired()
        dots = await client.list_dots()
        report["dots"] = [
            {
                "id": d.get("id"),
                "name": d.get("display_name")
                or (d.get("messaging_room_preview") or {}).get("name"),
                "has_room": bool(d.get("messaging_room_id")),
            }
            for d in dots
        ]
        chosen = (
            [d for d in dots if d.get("id") == dot_id]
            if dot_id
            else dots[:1]
            if len(dots) == 1
            else []
        )
        if not chosen:
            report["next"] = (
                "多个或没有 Dot：用 --dot-id 指定" if dots else "账号下没有 Dot"
            )
            return report
        binding = binding_for(chosen[0], state["account_id"])
        report["binding"] = {
            "dot_id": binding.dot_id,
            "name": binding.name,
            "dot_members": len(binding.dot_members),
            "human_members": len(binding.human_members),
        }
        items = await client.messages(binding.room_id, limit=limit)
        report["messages"] = [_summary(i, binding, show_text) for i in items]
        report["message_shapes"] = sorted(
            {json.dumps(_shape(i), sort_keys=True) for i in items}
        )
        report["senders"] = dict(Counter(r["from"] for r in report["messages"]))

        events = []
        live = None
        if listen:
            live = DotLive(
                client,
                lambda room, kind: events.append(
                    {
                        "t": time.time(),
                        "kind": kind,
                        "this_room": room == binding.room_id,
                    }
                ),
                lambda: events.append({"t": time.time(), "kind": "resync"}),
            )
            task = asyncio.create_task(live.run())
            for _ in range(int(SUBSCRIBE_WAIT * 10)):
                if live.connected:
                    break
                await asyncio.sleep(0.1)
            report["live_connected"] = live.connected
        if post:
            request_id = "w2d-probe-" + uuid.uuid4().hex
            sent_at = time.time()
            resp = await client.send(binding.room_id, post, request_id)
            report["post"] = {
                "status": resp.status,
                "echo_request_id": isinstance(resp.body, dict)
                and resp.body.get("request_id") == request_id,
            }
            after = await client.messages(binding.room_id, limit=5)
            report["post"]["visible_in_room"] = any(
                i.get("request_id") == request_id for i in after
            )
            report["post"]["sent_at"] = sent_at
        if listen:
            await asyncio.sleep(listen)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            report["live_events"] = events
            if post:
                newer = await client.messages(binding.room_id, limit=10)
                replies = [
                    i
                    for i in newer
                    if sender_of(i) in binding.dot_members
                    and (timestamp_of(i) or 0) > report["post"]["sent_at"]
                ]
                report["post"]["dot_replies"] = [
                    _summary(i, binding, show_text) for i in replies
                ]
        return report
    except AuthRequired:
        report["error"] = "auth_required"
        report["next"] = "先运行 wechat2dot dot-login"
        return report
    except (DotError, AuthUnavailable, NetworkError) as exc:
        report["error"] = getattr(exc, "code", None) or type(exc).__name__
        report["detail"] = str(exc)
        return report
    finally:
        await http.close()
