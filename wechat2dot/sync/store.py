"""Durable native-mode state, sharing SQLite and owner routing with the iLink engine."""

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

from wechat_dot_bridge.message_format import (
    compact_attachment_quote,
    compact_quote_text,
    partial_quote,
    reference_id,
)
from wechat_dot_bridge.store import Store
from wechat2dot.dot.client import DotError, ident


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request_id(mid):
    # Use a stable UUID-sized idempotency token accepted by the live room endpoint.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "wechat2dot:owner-message:" + mid))


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class QuoteContext:
    target: str | None = None
    hint: str = ""
    selected: bool = False
    refresh: bool = False


def own_text(text):
    # Some existing rows have the legacy expanded quote before the author's text.
    while text.startswith(("[引用]\n", "[引用缓存]\n")):
        closing = "\n[/" + text.partition("\n")[0][1:] + "\n"
        _, separator, remainder = text.partition(closing)
        if not separator:
            break
        text = remainder
    return text


class NativeStore(Store):
    # The native inbox on disk takes ownership; the legacy cap is for outstanding MCP tasks.
    work_queue_limit = None
    native_messages = True
    # Prevent older versions from treating a queued quote like a local command.
    native_quote_prefix = "[微信引用]\n"

    def __init__(self, directory, clock=time.time):
        super().__init__(directory, clock)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS native_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS native_seen(
                id TEXT PRIMARY KEY, stamp REAL NOT NULL, digest TEXT NOT NULL,
                first_seen REAL NOT NULL, observed REAL NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS native_out(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, part INTEGER NOT NULL, stamp REAL NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, retry REAL NOT NULL DEFAULT 0, priority INTEGER DEFAULT 1);
            CREATE INDEX IF NOT EXISTS native_dispatch ON native_out(state,priority,stamp,source,part);
            CREATE TABLE IF NOT EXISTS native_in(
                mid TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,retry REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS native_in_refs(
                mid TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                dot_mid TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS native_in_quotes(
                mid TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                quotes TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS native_sent_refs(
                account TEXT NOT NULL, peer TEXT NOT NULL, source_id TEXT NOT NULL,
                dot_mid TEXT NOT NULL, preview TEXT NOT NULL,
                PRIMARY KEY(account,peer,source_id));
            CREATE TABLE IF NOT EXISTS native_log(
                at REAL NOT NULL, code TEXT NOT NULL, ref TEXT);
            CREATE INDEX IF NOT EXISTS native_log_time ON native_log(at);
            CREATE TABLE IF NOT EXISTS native_cards(
                id TEXT PRIMARY KEY, stage TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS native_approvals(
                code TEXT PRIMARY KEY, mid TEXT NOT NULL, request TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'open');
            CREATE TABLE IF NOT EXISTS native_text(
                source TEXT PRIMARY KEY, digest TEXT NOT NULL, accepted_at REAL);
            CREATE INDEX IF NOT EXISTS native_text_ack ON native_text(digest,accepted_at);
            CREATE TABLE IF NOT EXISTS native_direct(
                id TEXT PRIMARY KEY, text TEXT NOT NULL, at REAL NOT NULL);
            CREATE TRIGGER IF NOT EXISTS native_direct_ack AFTER UPDATE OF status ON notifications
            WHEN new.status='accepted' AND old.status!='accepted' BEGIN
                INSERT OR REPLACE INTO native_direct VALUES(new.id,new.text,CAST(strftime('%s','now') AS REAL));
            END;
        """)

    def resume_session(self):
        # Native mode requires a fresh QR credential after Tencent rejects the session.
        return

    def get_binding(self):
        binding = super().get_binding()
        if binding and binding["token"] == "@keyring":
            import keyring
            from wechat2dot.vault import VaultTokens

            service = VaultTokens(self.directory.parent / "chatgpt-login.json").service
            token = keyring.get_password(service, "weixin:" + binding["id"])
            if not token:
                raise ValueError("weixin_keyring_missing")
            binding["token"] = token
        return binding

    def get(self, key, default=None):
        row = self.db.execute("SELECT v FROM native_meta WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO native_meta VALUES(?,?)", (key, packed(value))
        )

    def event(self, code, ref=None):
        self.db.execute(
            "INSERT INTO native_log VALUES(?,?,?)", (self.clock(), code, ref)
        )

    def pin(self, binding):
        owner = self._binding()
        identity = [
            owner["id"],
            owner["owner"],
            binding.account_id,
            binding.dot_id,
            binding.room_id,
        ]
        with self._transaction():
            if self.get("identity", identity) != identity:
                raise ValueError("native_binding_changed")
            self.put("identity", identity)
            if self.get("in_cursor") is None:
                self.put(
                    "in_cursor",
                    self.db.execute(
                        "SELECT coalesce(max(rowid),0) FROM messages"
                    ).fetchone()[0],
                )

    def recover(self):
        with self._transaction():
            # An already attempted request must keep its original ID for reconciliation.
            for row in self.db.execute(
                "SELECT mid FROM native_in WHERE state='queued' AND attempts=0 AND length(request_id)>64"
            ).fetchall():
                self.db.execute(
                    "UPDATE native_in SET request_id=? WHERE mid=?",
                    (request_id(row[0]), row[0]),
                )
            for row in self.db.execute(
                "SELECT id FROM native_out WHERE state='sending'"
            ):
                self.event("weixin_uncertain", row[0])
            self.db.execute(
                "UPDATE native_out SET state='uncertain' WHERE state='sending'"
            )
            self.db.execute("UPDATE native_in SET state='verify' WHERE state='sending'")
            for row in self.db.execute(
                "SELECT mid FROM native_in WHERE state='approval_reserved'"
            ):
                self.notice(
                    "approval-interrupted:" + row[0],
                    "审批提交被中断，请在 ChatGPT 核对结果。",
                )
                self.event("approval_uncertain", row[0])
            self.db.execute(
                "UPDATE native_in SET state='uncertain' WHERE state='approval_reserved'"
            )
            self.db.execute(
                "UPDATE native_approvals SET state='uncertain' WHERE state='submitting'"
            )
        self.recover_inflight()

    def enqueue(self, source, part, stamp, kind, body, priority=1):
        key = "n_" + digest(source + ":" + str(part))
        self.db.execute(
            """INSERT OR IGNORE INTO native_out(id,source,part,stamp,kind,body,priority)
                        VALUES(?,?,?,?,?,?,?)""",
            (key, source, part, stamp, kind, packed(body), priority),
        )
        return key

    def notice(self, source, text, priority=0):
        self.enqueue(source, 0, self.clock(), "text", text, priority)

    def _record_compact_quotes(self, message_id, quotes):
        binding = self._binding()
        references = []
        for quote in quotes:
            raw_ids = quote.get("ids", [quote.get("source_id")])
            if not isinstance(raw_ids, list):
                raw_ids = []
            ids = list(dict.fromkeys(
                sid for value in raw_ids[:2] if (sid := reference_id(value))
            ))
            preview = compact_quote_text(quote.get("preview"))
            selected = quote.get("selected") is True and bool(preview)
            partial = quote.get("partial")
            if not selected and isinstance(partial, dict):
                for sid in ids:
                    cached = self._quoted_inbound(binding["id"], binding["owner"], sid)
                    if cached and cached["id"] != message_id:
                        original = own_text(self.incoming_text(cached["id"], cached["text"]))
                        excerpt = partial_quote(original, partial)
                        if excerpt != original:
                            preview, selected = compact_quote_text(excerpt), True
                            break
                if not selected and (excerpt := compact_quote_text(partial.get("start"))):
                    preview, selected = excerpt, True
            reference = (ids, preview, selected)
            if reference not in references:
                references.append(reference)
        self.db.execute(
            "INSERT INTO native_in_quotes VALUES(?,?)", (message_id, packed(references))
        )

    def has_quote(self, mid):
        return self.db.execute(
            "SELECT 1 FROM native_in_quotes WHERE mid=?", (mid,)
        ).fetchone() is not None

    def incoming_text(self, mid, text):
        return text.removeprefix(self.native_quote_prefix) if self.has_quote(mid) else text

    def remember_incoming_reference(self, request, dot_mid):
        try:
            ident(dot_mid)
        except DotError:
            return
        self.db.execute(
            """INSERT OR IGNORE INTO native_in_refs(mid,dot_mid)
               SELECT i.mid,? FROM native_in i JOIN messages m ON m.id=i.mid
               JOIN binding b ON b.id=m.account AND b.owner=m.peer
               WHERE i.request_id=? AND i.state IN ('accepted','sending','verify','uncertain')""",
            (dot_mid, request),
        )

    def remember_sent_reference(self, binding, source, response, reply):
        sid = reference_id(response.get("message_id"))
        if not sid:
            return
        attachment = reply.get("attachment")
        preview = (
            compact_attachment_quote(attachment["kind"], attachment["name"])
            if attachment
            else compact_quote_text(reply.get("text"))
        )
        scope = (binding["id"], binding["owner"], sid)
        try:
            if source is None:
                # A bridge notice has a WeChat receipt but no matching DOT room message.
                if preview:
                    self.db.execute(
                        """INSERT OR IGNORE INTO sent_quote_cache
                           (account,peer,source_id,text,attachment_id) VALUES(?,?,?,?,NULL)""",
                        (*scope, preview),
                    )
            else:
                self.db.execute(
                    """INSERT OR IGNORE INTO native_sent_refs
                       SELECT ?,?,?,id,? FROM native_seen WHERE id=?""",
                    (*scope, preview, ident(source)),
                )
        except (DotError, sqlite3.IntegrityError):
            # Optional reference metadata must not roll back a confirmed send.
            return

    def prune_sent_references(self, retention):
        self.db.execute(
            """DELETE FROM native_sent_refs WHERE NOT EXISTS (
               SELECT 1 FROM native_seen WHERE id=native_sent_refs.dot_mid AND stamp>=?)""",
            (self.clock() - retention,),
        )

    def _incoming_quote_preview(self, account, peer, message):
        text = own_text(self.incoming_text(message["id"], message["text"]))
        if text and text != "[微信附件]":
            return compact_quote_text(text)
        file = self.db.execute(
            """SELECT kind,name FROM attachments WHERE account=? AND peer=?
               AND message_id=? ORDER BY rowid LIMIT 1""",
            (account, peer, message["id"]),
        ).fetchone()
        if file:
            return compact_attachment_quote(*file)
        return "附件" if text else ""

    def _quote_target(self, mid, account, peer, ids, preview):
        cached_fallback = None
        for sid in ids:
            cached = self._quoted_inbound(account, peer, sid)
            if cached and cached["id"] != mid:
                target = self.db.execute(
                    """SELECT i.state,r.dot_mid FROM native_in i
                       LEFT JOIN native_in_refs r ON r.mid=i.mid WHERE i.mid=?""",
                    (cached["id"],),
                ).fetchone()
                if target and target["dot_mid"]:
                    description = preview or self._incoming_quote_preview(account, peer, cached)
                    return target["dot_mid"], "用户原消息", description, False
                if cached_fallback is None or (target and target["state"] == "accepted"):
                    cached_fallback = cached, bool(target and target["state"] == "accepted")
            sent = self.db.execute(
                """SELECT dot_mid,preview FROM native_sent_refs
                   WHERE account=? AND peer=? AND source_id=?""",
                (account, peer, sid),
            ).fetchone()
            if sent:
                return sent["dot_mid"], "DOT 原回复", preview or sent["preview"], False
        if cached_fallback:
            cached, refresh = cached_fallback
            description = preview or self._incoming_quote_preview(account, peer, cached)
            return None, "用户原消息", description, refresh
        for sid in ids:
            sent = self.db.execute(
                """SELECT q.text,a.kind,a.name FROM sent_quote_cache q
                   LEFT JOIN attachments a ON a.id=q.attachment_id
                      AND a.account=q.account AND a.peer=q.peer
                   WHERE q.account=? AND q.peer=? AND q.source_id=?""",
                (account, peer, sid),
            ).fetchone()
            if sent:
                description = (
                    compact_attachment_quote(sent["kind"], sent["name"])
                    if sent["kind"] else compact_quote_text(sent["text"])
                )
                return None, "此前回复", preview or description, False
        return None, "", preview, False

    def quote_context(self, mid):
        row = self.db.execute(
            "SELECT quotes FROM native_in_quotes WHERE mid=?", (mid,)
        ).fetchone()
        if not row:
            return QuoteContext()
        binding = self._binding()
        references = json.loads(row["quotes"])
        hints = []
        for ids, preview, *selected_flags in references[:3]:
            target, author, preview, refresh = self._quote_target(
                mid, binding["id"], binding["owner"], ids, preview
            )
            label = f"[微信引用 · {author}]" if author else "[微信引用]"
            hint = preview or ("原消息摘录不可用" if target else "原消息未能定位")
            hints.append(f"{label} {hint}")
        if len(references) > 3:
            hints.append(f"[另有 {len(references) - 3} 条微信引用]")
        if len(references) == 1:
            return QuoteContext(target, hints[0], bool(selected_flags and selected_flags[0]), refresh)
        return QuoteContext(hint="\n".join(hints))

    def accept_incoming(self):
        with self._transaction():
            owner = self._binding()
            cursor = self.get("in_cursor")
            if cursor is None:
                raise ValueError("native_room_not_bound")
            rows = self.db.execute(
                """SELECT rowid,id,context_token FROM messages WHERE rowid>?
                AND account=? AND peer=? ORDER BY rowid LIMIT 100""",
                (cursor, owner["id"], owner["owner"]),
            ).fetchall()
            for row in rows:
                self.db.execute(
                    "INSERT OR IGNORE INTO native_in VALUES(?,?,'queued',0,0)",
                    (row["id"], request_id(row["id"])),
                )
                self.db.execute(
                    "UPDATE message_work SET state='completed' WHERE message_id=?",
                    (row["id"],),
                )
                self.put("in_cursor", row["rowid"])
            return len(rows)

    def history(self):
        return dict(
            self.db.execute(
                "SELECT code,count(*) FROM native_log WHERE at>=? GROUP BY code",
                (self.clock() - 86400,),
            )
        )

    def status(self, window=86400):
        with self.lock:
            now = self.clock()
            wx, rest = self.get("wx_ok", 0), self.get("dot_ok", 0)
            online = now - self.get("heartbeat", 0) < 90
            socket = online and self.get("socket", False)
            context = self.db.execute(
                "SELECT created_at FROM notification_routes LIMIT 1"
            ).fetchone()
            age = max(0, int(now - context[0])) if context else None
            issues = []
            if not online:
                issues.append("offline")
            if now - wx > 120:
                issues.append("weixin_poll_stale")
            if not socket and now - rest > 300:
                issues.append("dot_read_stale")
            if age is None or age >= window or self.get("closed_context"):
                issues.append("waiting_context")
            for key in ("dot_error", "wx_error"):
                if self.get(key):
                    issues.append(self.get(key))
            if self.get("paused"):
                issues.append("paused")
            return dict(
                healthy=not issues,
                issues=issues,
                online=online,
                socket=socket,
                last_weixin_poll=wx,
                last_dot_read=rest,
                context_age_seconds=age,
                outbox=dict(
                    self.db.execute(
                        "SELECT state,count(*) FROM native_out GROUP BY state"
                    )
                ),
                inbox=dict(
                    self.db.execute(
                        "SELECT state,count(*) FROM native_in GROUP BY state"
                    )
                ),
                pending_review=[
                    dict(
                        reference=digest(r["ref"])[:12],
                        direction=r["direction"],
                        part=r["part"],
                    )
                    for r in self.db.execute("""
                        SELECT id AS ref,'weixin' AS direction,part+1 AS part FROM native_out WHERE state='uncertain'
                        UNION ALL SELECT mid,'dot',NULL FROM native_in WHERE state='uncertain'
                        ORDER BY direction,ref""")
                ],
                history_24h=self.history(),
                last_send=self.get("last_send_evidence"),
                last_dot_post=self.get("last_dot_post"),
                recent_issues=[
                    dict(
                        at=r["at"],
                        code=r["code"],
                        reference=digest(r["ref"])[:12] if r["ref"] else None,
                    )
                    for r in self.db.execute(
                        "SELECT at,code,ref FROM native_log WHERE at>=? ORDER BY rowid DESC LIMIT 20",
                        (now - 86400,),
                    )
                ],
            )
