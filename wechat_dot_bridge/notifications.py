"""Owner-only text notifications, independent of inbound processing generations.

No network I/O here. A claimed notification uses the existing serial sender.
Only a fresh inbound context can wake a context-rejected notification; unknown
outcomes and explicit failures are never automatically replayed.
"""

import hashlib
import json
import re

from .errors import BridgeError


MAX_NOTIFICATION_CHARS = 2000
MAX_NOTIFICATION_TTL = 7 * 86400
MAX_NOTIFICATION_HISTORY = 4096
WAITING = ("queued", "context_required")


def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise BridgeError("invalid_quiet_hours")
    hour, minutes = map(int, value.split(":"))
    return hour * 60 + minutes


class NotificationStore:
    def _notification_policy(self, binding):
        row = self.db.execute("SELECT * FROM notification_policy WHERE account=? AND peer=?",
                              (binding["id"], binding["owner"])).fetchone()
        return dict(row) if row else {"enabled": 0, "paused": 0, "quiet_start": None,
                                     "quiet_end": None, "utc_offset_minutes": 480}

    def _notification_context(self, binding):
        return self.db.execute("SELECT * FROM notification_routes WHERE account=? AND peer=?",
                               (binding["id"], binding["owner"])).fetchone()

    def _save_notification_context(self, binding, context):
        if context is not None:
            self.db.execute("""INSERT INTO notification_routes(account,peer,context_id,context_token,created_at)
                VALUES(?,?,?,?,?) ON CONFLICT(account,peer) DO UPDATE SET
                (context_id,context_token,created_at)=(excluded.context_id,excluded.context_token,excluded.created_at)
                WHERE excluded.created_at>=notification_routes.created_at""",
                (binding["id"], binding["owner"], context["id"], context["context_token"], context["created_at"]))

    def configure_notifications(self, *, enabled, quiet_start=None, quiet_end=None, utc_offset_minutes=480):
        """Local operator opt-in. Not exposed as an MCP permission-granting tool."""
        if type(enabled) is not bool or type(utc_offset_minutes) is not int or not -840 <= utc_offset_minutes <= 840:
            raise BridgeError("invalid_notification_policy")
        if (quiet_start is None) != (quiet_end is None):
            raise BridgeError("invalid_quiet_hours")
        if quiet_start is not None:
            minute(quiet_start)
            minute(quiet_end)
            if quiet_start == quiet_end:
                raise BridgeError("invalid_quiet_hours")
        with self._transaction():
            b = self._binding()
            self.db.execute("""INSERT INTO notification_policy
                (account,peer,enabled,quiet_start,quiet_end,utc_offset_minutes) VALUES(?,?,?,?,?,?)
                ON CONFLICT(account,peer) DO UPDATE SET enabled=excluded.enabled,
                quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,
                utc_offset_minutes=excluded.utc_offset_minutes""",
                (b["id"], b["owner"], int(enabled), quiet_start, quiet_end, utc_offset_minutes))
        return self.notification_status()

    def set_notifications_paused(self, paused):
        if type(paused) is not bool:
            raise BridgeError("invalid_notification_policy")
        with self._transaction():
            b = self._binding()
            if not self._notification_policy(b)["enabled"]:
                raise BridgeError("notifications_disabled")
            self.db.execute("UPDATE notification_policy SET paused=? WHERE account=? AND peer=?",
                            (int(paused), b["id"], b["owner"]))
        return self.notification_status()

    def _notification_value(self, row):
        status = row["status"]
        if status in WAITING and row["expires_at"] <= self.clock():
            status = "expired"
        return {"notification_id": row["id"], "status": status, "delivered": None,
                "created_at": row["created_at"], "expires_at": row["expires_at"],
                "attempts": row["attempts"], "error": "notification_expired" if status == "expired" else row["error"]}

    def notification_status(self, notification_id=None):
        with self.lock:
            b = self._binding()
            if notification_id is not None:
                row = self.db.execute("SELECT * FROM notifications WHERE id=? AND account=? AND peer=?",
                                      (notification_id, b["id"], b["owner"])).fetchone()
                if row is None:
                    raise BridgeError("unknown_notification")
                return self._notification_value(row)
            p = self._notification_policy(b)
            return {"enabled": bool(p["enabled"]), "paused": bool(p["paused"]),
                    "quiet_start": p["quiet_start"], "quiet_end": p["quiet_end"],
                    "utc_offset_minutes": p["utc_offset_minutes"],
                    "context_available": self._notification_context(b) is not None,
                    "recent": [self._notification_value(r) for r in self.db.execute(
                        "SELECT * FROM notifications WHERE account=? AND peer=? ORDER BY rowid DESC LIMIT 20",
                        (b["id"], b["owner"]))]}

    def queue_notification(self, text, notification_key, expires_at):
        from .store import _text_bytes, _timestamp
        size = _text_bytes(text)
        if size is None or not text.strip() or len(text) > MAX_NOTIFICATION_CHARS or size > 8000:
            raise BridgeError("invalid_notification_text")
        if not isinstance(notification_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", notification_key):
            raise BridgeError("invalid_notification_key")
        if not _timestamp(expires_at):
            raise BridgeError("invalid_notification_expiry")
        digest = hashlib.sha256(json.dumps([text, float(expires_at)], ensure_ascii=False).encode()).hexdigest()
        with self._transaction():
            b = self._binding()
            scope = (b["id"], b["owner"])
            identifier = "dot_notify_" + hashlib.sha256(json.dumps([*scope, notification_key]).encode()).hexdigest()
            existing = self.db.execute("SELECT * FROM notifications WHERE id=?", (identifier,)).fetchone()
            if existing:
                if existing["digest"] != digest:
                    raise BridgeError("notification_key_conflict")
                return self._notification_value(existing)
            now = self.clock()
            if not now < expires_at <= now + MAX_NOTIFICATION_TTL:
                raise BridgeError("invalid_notification_expiry")
            p = self._notification_policy(b)
            if not p["enabled"] or p["paused"]:
                raise BridgeError("notifications_disabled" if not p["enabled"] else "notifications_paused")
            if not b["enabled"] or b["status"] != "ready":
                raise BridgeError("account_disabled")
            self._expire_notifications(now)
            if self.db.execute("SELECT count(*) FROM notifications WHERE account=? AND peer=? AND status IN ('queued','sending','context_required')", scope).fetchone()[0] >= 32:
                raise BridgeError("notification_queue_full")
            if self.db.execute("SELECT count(*) FROM notifications WHERE account=? AND peer=?", scope).fetchone()[0] >= MAX_NOTIFICATION_HISTORY:
                raise BridgeError("notification_history_full")
            self.db.execute("""INSERT INTO notifications
                (id,account,peer,digest,text,created_at,expires_at,status) VALUES(?,?,?,?,?,?,?,'queued')""",
                (identifier, *scope, digest, text, now, expires_at))
        return self.notification_status(identifier)

    def _expire_notifications(self, now):
        self.db.execute("UPDATE notifications SET status='expired',error='notification_expired',text='' WHERE expires_at<=? AND status IN ('queued','context_required')", (now,))
        # Keep idempotency digests/status after expiry, but discard terminal bodies.
        self.db.execute("UPDATE notifications SET text='' WHERE expires_at<=? AND status NOT IN ('queued','context_required','sending','uncertain')", (now,))

    def claim_notification(self):
        with self._transaction():
            now = self.clock()
            self._expire_notifications(now)
            b = self._binding()
            p = self._notification_policy(b)
            if not b["enabled"] or b["status"] != "ready" or not p["enabled"] or p["paused"]:
                return None
            if p["quiet_start"] is not None:
                start, end = minute(p["quiet_start"]), minute(p["quiet_end"])
                current = int(now // 60 + p["utc_offset_minutes"]) % 1440
                quiet = start <= current < end if start < end else current >= start or current < end
                if quiet:
                    return None
            context = self._notification_context(b)
            # A context-rejected head must not block unrelated queued notifications.
            row = self.db.execute("""SELECT * FROM notifications WHERE account=? AND peer=? AND
                (status='queued' OR (status='context_required' AND COALESCE(context_id,'')!=?))
                ORDER BY created_at,rowid LIMIT 1""",
                (b["id"], b["owner"], context["context_id"] if context else "")).fetchone()
            if row is None:
                return None
            if context is None:
                self.db.execute("UPDATE notifications SET status='context_required',error='fresh_context_required' WHERE id=?", (row["id"],))
                return None
            if row["attempts"] >= 3:
                self.db.execute("UPDATE notifications SET status='failed',error='retry_limit' WHERE id=?", (row["id"],))
                return None
            self.db.execute("UPDATE notifications SET status='sending',error=NULL,context_id=?,attempts=attempts+1 WHERE id=?",
                            (context["context_id"], row["id"]))
            return {"notification_id": row["id"], "client_id": row["id"], "peer": b["owner"],
                    "text": row["text"], "context_token": context["context_token"]}

    def finish_notification(self, notification_id, status, error=None):
        from .store import SEND_OUTCOMES, _safe_error
        if status not in SEND_OUTCOMES:
            raise BridgeError("invalid_send_outcome")
        with self._transaction():
            b = self._binding()
            row = self.db.execute("SELECT * FROM notifications WHERE id=? AND account=? AND peer=?",
                                  (notification_id, b["id"], b["owner"])).fetchone()
            if row is None or row["status"] != "sending":
                raise BridgeError("notification_not_sending")
            self.db.execute("UPDATE notifications SET status=?,error=? WHERE id=?",
                            (status, _safe_error(error) if error else None, notification_id))
            if status == "auth_required":
                self.db.execute("UPDATE binding SET status='auth_required',last_error='auth_required' WHERE id=?", (b["id"],))
