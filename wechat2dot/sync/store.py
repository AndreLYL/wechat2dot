"""Durable native-mode state, sharing SQLite and owner routing with the iLink engine."""

import hashlib
import json
import time

from wechat_dot_bridge.store import Store


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class NativeStore(Store):
    # The native inbox on disk takes ownership; the legacy cap is for outstanding MCP tasks.
    work_queue_limit = None

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
                    (row["id"], "w2d-" + digest(row["id"])),
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
