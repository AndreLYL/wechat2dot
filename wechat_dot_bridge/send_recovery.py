"""Persist evidence of an explicit send rejection in the same transaction as its outcome."""
import hashlib
import json
from email.utils import parsedate_to_datetime


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def retry_delay(header, now):
    """Retry-After is either an ASCII delta in seconds or an HTTP date."""
    if header:
        value = header.strip()
        if value.isascii() and value.isdecimal():
            # A huge wait must not overflow SQLite or cause an early replay.
            digits = value.lstrip("0") or "0"
            return min(int(digits[:12]), 253402300799)
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is not None:
                return max(0, date.timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            pass
    return 0


def record_rejection(store, row, *, notification, evidence):
    # All keys are constructed by the transport. No raw headers, tokens or server text.
    table, key = ("notifications", "id") if notification else ("replies", "client_id")
    detail = dict(evidence)
    delay = detail.pop("retry_delay", 0)
    if detail["http_status"] == 429:
        retry_at = store.clock() + max(delay, 30 * 2 ** (row["attempts"] - 1))
        store.db.execute("UPDATE binding SET send_retry_at=max(send_retry_at,?)", (retry_at,))
        detail["retry_at"] = retry_at
    store.db.execute(f"UPDATE {table} SET last_rejection=? WHERE {key}=?",
                     (json.dumps(detail), row[key]))


def recovery_fields(row):
    fields = {"last_rejection": json.loads(row["last_rejection"])} if row["last_rejection"] else {}
    if row["error"] == "rate_limited" and not fields:
        return {"recovery_action": "legacy_cause_unknown"}
    status = row["status"]
    if status == "context_required":
        fields["recovery_action"] = "wait_for_new_owner_context"
    elif status == "queued" and row["error"] == "http_rate_limited":
        fields["recovery_action"] = "automatic_backoff"
    elif status == "uncertain":
        fields["recovery_action"] = "verify_on_phone"
    elif status == "failed":
        fields["recovery_action"] = "inspect_failed_send"
    return fields
