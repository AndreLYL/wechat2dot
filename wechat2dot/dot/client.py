"""ChatGPT dot room client. Shapes follow wecom-dot-bridge docs/dot-api.md (observed 2026-10-15)."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from .auth import AuthRequired
from .http import MAX_FILE_BYTES, FileTooLarge, FileUnavailable, Response, Upload

BASE = "https://chatgpt.com/backend-api"
PAGE_LIMIT = 32
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,199}\Z")
CLIENT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Codex Desktop",
    "originator": "Codex Desktop",
    "OAI-Product-Sku": "CODEX",
}


class DotError(Exception):
    def __init__(self, message: str = "", code: str | None = None):
        super().__init__(message)
        self.code = code


class DotProtocolError(DotError):
    """A response no longer matches the observed protocol; stop instead of guessing."""

    def __init__(self, message: str):
        super().__init__(message, code="dot_protocol_changed")


class AccountChanged(DotError):
    """The logged-in account is not the one bound; nothing was sent."""


def ident(value) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DotError("invalid identifier")
    return value


@dataclass(frozen=True)
class Binding:
    account_id: str
    dot_id: str
    room_id: str
    dot_members: tuple[str, ...]
    human_members: tuple[str, ...]
    name: str


def binding_for(dot: dict, account_id: str) -> Binding:
    """Bind an existing dot and room; rooms are never created.

    `dot` must come from the /tbo list: only it carries messaging_room_preview.members.
    Every message has role "user", so the dot is identified by the member with an aeon_id.
    """
    dot_id = ident(dot.get("id"))
    room = dot.get("messaging_room_id")
    if not isinstance(room, str) or not room:
        raise DotError(
            "dot has no messaging room yet; open it once in ChatGPT", code="no_room"
        )
    preview = (
        dot.get("messaging_room_preview")
        if isinstance(dot.get("messaging_room_preview"), dict)
        else {}
    )
    found = [m for m in preview.get("members") or [] if isinstance(m, dict)]
    dots = sorted(
        {
            m["account_user_id"]
            for m in found
            if m.get("aeon_id") and isinstance(m.get("account_user_id"), str)
        }
    )
    humans = sorted(
        {
            m["account_user_id"]
            for m in found
            if not m.get("aeon_id") and isinstance(m.get("account_user_id"), str)
        }
    )
    if not dots:
        raise DotError("room has no dot member", code="no_member")
    name = next(
        (
            n.strip()
            for n in (dot.get("display_name"), preview.get("name"))
            if isinstance(n, str) and n.strip()
        ),
        "dot",
    )
    return Binding(account_id, dot_id, ident(room), tuple(dots), tuple(humans), name)


def message_text(item: dict) -> str:
    content = item.get("content")
    text = content.get("text") if isinstance(content, dict) else None
    return text if isinstance(text, str) else ""


def attachments_of(item: dict) -> list[dict]:
    """Downloadable files of a message: [{file_id, name, mime}]."""
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    out = []
    for att in content.get("attachments") or []:
        if not isinstance(att, dict) or (att.get("type") or "file") != "file":
            continue
        file = att.get("file") if isinstance(att.get("file"), dict) else {}
        file_id = att.get("file_id") or file.get("id")
        if isinstance(file_id, str) and file_id:
            name = file.get("name") or att.get("name") or "attachment"
            mime = file.get("mime_type")
            out.append(
                {
                    "file_id": file_id,
                    "name": name if isinstance(name, str) else "attachment",
                    "mime": mime if isinstance(mime, str) else None,
                }
            )
    return out


IN_PROGRESS_STATUSES = frozenset({"in_progress", "streaming", "pending", "generating"})


def sender_of(item: dict) -> str | None:
    value = item.get("account_user_id")
    return value if isinstance(value, str) else None


def in_progress(item: dict) -> bool:
    return str(item.get("status") or "").lower() in IN_PROGRESS_STATUSES


def timestamp_of(item: dict, key: str = "created_at") -> float | None:
    """Epoch seconds from seconds, milliseconds or ISO-8601."""
    stamp = item.get(key)
    if isinstance(stamp, bool):
        return None
    if isinstance(stamp, (int, float)):
        return float(stamp) / 1000 if stamp > 1e11 else float(stamp)
    if isinstance(stamp, str):
        try:
            parsed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    return None


def oldest_first(items: list[dict]) -> list[dict]:
    def key(pair):
        index, item = pair
        stamp = timestamp_of(item)
        return (0, stamp, index) if stamp is not None else (1, 0.0, index)

    return [item for _, item in sorted(enumerate(items), key=key)]


def file_host_ok(host: str) -> bool:
    return host.lower().endswith(".oaiusercontent.com")


class DotClient:
    def __init__(self, http, auth, *, max_upload_bytes=MAX_FILE_BYTES):
        self.http, self.auth = http, auth
        self.max_upload_bytes = max_upload_bytes

    async def _call(
        self,
        method,
        path,
        *,
        account_id=None,
        body=None,
        params=None,
        upload=None,
        timeout=30,
        origin=BASE,
        retry_401=True,
    ) -> Response:
        """One request, plus one retry after a definitive 401 with a refreshed token."""

        def pinned(account):
            if account_id is not None and account != account_id:
                raise AccountChanged("logged-in account changed")

        access, account = await self.auth.access()
        pinned(account)
        refreshed = False
        while True:
            headers = {
                "Authorization": "Bearer " + access,
                "ChatGPT-Account-Id": account,
                **CLIENT_HEADERS,
            }
            extra = {"upload": upload} if upload is not None else {}
            resp = await self.http.request(
                method,
                origin + path,
                headers=headers,
                json_body=body,
                params=params,
                timeout=timeout,
                **extra,
            )
            if resp.status != 401 or not retry_401:
                return resp
            fresh = None if refreshed else await self.auth.refreshed_access(access)
            if fresh is None:
                await self.auth.reject(access)
                raise AuthRequired()
            (access, account), refreshed = fresh, True
            pinned(account)

    @staticmethod
    def _items(resp: Response, what: str) -> list[dict]:
        if resp.status != 200:
            raise DotError(f"{what} HTTP {resp.status}", code="http_error")
        items = resp.body.get("items") if isinstance(resp.body, dict) else None
        if not isinstance(items, list):
            raise DotProtocolError(f"{what}: no items list")
        if not all(isinstance(i, dict) for i in items):
            raise DotProtocolError(f"{what}: invalid item")
        return items

    async def list_dots(self, account_id=None) -> list[dict]:
        resp = await self._call("GET", "/tbo", account_id=account_id)
        return [d for d in self._items(resp, "GET /tbo") if d.get("id")]

    async def live_url(self) -> str:
        """The per-user live socket URL. It carries auth material: never log it."""
        resp = await self._call("GET", "/celsius/ws/user")
        url = resp.body.get("websocket_url") if isinstance(resp.body, dict) else None
        if resp.status != 200:
            raise DotError(f"GET live socket HTTP {resp.status}", code="http_error")
        if not isinstance(url, str):
            raise DotProtocolError("live socket: no websocket_url")
        return url

    async def messages(
        self, room_id, limit=PAGE_LIMIT, before=None, account_id=None
    ) -> list[dict]:
        """Newest page, oldest first. `before` returns strictly older messages."""
        params = {"limit": str(limit)}
        if before is not None:
            params["before"] = ident(before)
        resp = await self._call(
            "GET",
            f"/messaging/rooms/{ident(room_id)}/messages",
            account_id=account_id,
            params=params,
        )
        return oldest_first(self._items(resp, "GET messages"))

    async def send(
        self, room_id, text, request_id, account_id=None, file_ids=None, reply_to=None
    ) -> Response:
        """One POST; the caller classifies the outcome. request_id makes it reconcilable."""
        attachments = [{"type": "file", "file_id": ident(f)} for f in file_ids or []]
        body = {
            "content": {"text": text, "attachments": attachments},
            "request_id": request_id,
            "idempotency_token": request_id,
        }
        if reply_to:
            body["reply_to"] = {"message_id": ident(reply_to)}
        return await self._call(
            "POST",
            f"/messaging/rooms/{ident(room_id)}/messages",
            account_id=account_id,
            body=body,
            timeout=45,
        )

    async def upload_file(self, room_id, name, mime, data, account_id=None) -> str:
        if not data:
            raise FileUnavailable()
        if len(data) > self.max_upload_bytes:
            raise FileTooLarge()
        resp = await self._call(
            "POST",
            f"/messaging/rooms/{ident(room_id)}/files",
            account_id=account_id,
            upload=Upload("file", name, mime, data),
            timeout=120,
        )
        file_id = resp.body.get("id") if isinstance(resp.body, dict) else None
        if resp.status == 413:
            raise FileTooLarge()
        if resp.status in (400, 403, 404, 410, 415, 422):
            raise FileUnavailable()
        if resp.status not in (200, 201):
            raise DotError(f"file upload HTTP {resp.status}", code="http_error")
        if not isinstance(file_id, str) or not file_id:
            raise DotProtocolError("file upload: no id")
        return ident(file_id)

    async def download_file(
        self, room_id, file_id, account_id=None
    ) -> tuple[bytes, str | None, str | None]:
        """Fresh metadata (signed URLs expire), then the bytes from OpenAI file storage."""
        resp = await self._call(
            "GET",
            f"/messaging/rooms/{ident(room_id)}/files/{ident(file_id)}",
            account_id=account_id,
        )
        meta = resp.body if isinstance(resp.body, dict) else {}
        url = meta.get("download_url")
        if (
            resp.status in (400, 403, 404, 410)
            or str(meta.get("status") or "").lower() == "error"
        ):
            raise FileUnavailable()
        if resp.status != 200:
            raise DotError(f"file metadata HTTP {resp.status}", code="http_error")
        if meta.get("id", file_id) != file_id or not isinstance(url, str):
            raise DotProtocolError("file metadata: unexpected shape")
        data = await self.http.fetch(url, host_ok=file_host_ok)
        name, mime = meta.get("name"), meta.get("mime_type")
        return (
            data,
            name if isinstance(name, str) else None,
            mime if isinstance(mime, str) else None,
        )

    async def task_status(self, tid, account_id):
        # Only called for thread attachments observed in the pinned room.
        origin = "https://codex-cloud-backend.chatgpt.com"
        tid = ident(tid)
        response = await self._call(
            "GET", "/v1/threads/" + tid, origin=origin, account_id=account_id
        )
        if response.status != 200:
            raise DotError("task metadata unavailable", code="task_unavailable")
        meta = response.body.get("thread") if isinstance(response.body, dict) else None
        if not isinstance(meta, dict) or meta.get("id") != tid:
            raise DotProtocolError("task metadata changed")
        latest = await self._call(
            "GET",
            "/v2/threads/" + tid + "/turns",
            origin=origin,
            account_id=account_id,
            params={"limit": "1", "sortDirection": "desc", "itemsView": "notLoaded"},
        )
        if latest.status != 200:
            raise DotError("task status unavailable", code="task_unavailable")
        turns = latest.body.get("data") if isinstance(latest.body, dict) else None
        if not isinstance(turns, list) or (turns and not isinstance(turns[0], dict)):
            raise DotProtocolError("task status changed")
        stage = turns[0].get("status") if turns else None
        if not stage:
            status = meta.get("status")
            stage = status.get("type") if isinstance(status, dict) else None
        if not isinstance(stage, str):
            raise DotProtocolError("task stage missing")
        return (meta.get("name") or "DOT 任务", stage)

    async def approval_details(self, room, mid, account_id):
        path = f"/messaging/rooms/{ident(room)}/messages/{ident(mid)}/elicitation"
        resp = await self._call("GET", path, account_id=account_id)
        if resp.status != 200 or not isinstance(resp.body, dict):
            raise DotError("approval unavailable", code="approval_unavailable")
        return resp.body

    async def decide(
        self, room, mid, account_id, fingerprint, action, content, persist
    ):
        path = (
            f"/messaging/rooms/{ident(room)}/messages/{ident(mid)}/elicitation/response"
        )
        return await self._call(
            "POST",
            path,
            account_id=account_id,
            retry_401=False,
            body={
                "request_fingerprint": fingerprint,
                "result": {
                    "action": action,
                    "content": content,
                    "_meta": {"persist": persist} if persist else None,
                },
            },
        )
