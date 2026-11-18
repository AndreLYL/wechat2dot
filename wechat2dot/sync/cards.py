"""Present only room-linked task metadata and explicitly offered confirmation choices."""

from dataclasses import dataclass

from wechat2dot.dot.client import ident


@dataclass
class Approval:
    message: str
    content: dict
    persist: tuple

    def describe(self, code):
        choices = [f"/accept {code} 同意", f"/decline {code} 拒绝"]
        if "session" in self.persist:
            choices.append(f"/accept-conv {code} 本对话允许")
        if "always" in self.persist:
            choices.append(f"/accept-always {code} 始终允许")
        return self.message + "\n" + "；".join(choices)


def approval_view(data):
    if not isinstance(data, dict):
        return None
    p = data.get("params")
    if (
        not isinstance(p, dict)
        or p.get("mode", "form") != "form"
        or not data.get("request_id")
    ):
        return None
    if (
        not data.get("thread_id")
        or p.get("threadId") != data["thread_id"]
        or p.get("turnId", data.get("turn_id")) != data.get("turn_id")
    ):
        return None
    message, schema, meta = (
        p.get("message"),
        p.get("requestedSchema"),
        p.get("_meta") or {},
    )
    if (
        not isinstance(message, str)
        or not message.strip()
        or not isinstance(schema, dict)
        or not isinstance(meta, dict)
    ):
        return None
    if schema.get("type") != "object":
        return None
    props, required = schema.get("properties"), schema.get("required", [])
    if meta.get("codex_approval_kind") == "mcp_tool_call":
        if (
            props != {}
            or required
            or meta.get("codex_requires_user_input", False) is not False
        ):
            return None
        offered = meta.get("persist") or []
        if isinstance(offered, str):
            offered = [offered]
        if not isinstance(offered, list) or any(
            x not in ("session", "always") for x in offered
        ):
            return None
        return Approval(message, {}, tuple(offered))
    if p.get("serverName") in ("browser", "browser-use") or set(meta) - {
        "reason",
        "openai/confirmation",
    }:
        return None
    if props == {} and not required:
        return Approval(message, {}, ())
    conf = meta.get("openai/confirmation")
    if isinstance(conf, dict) and conf.get("version") == 1:
        field = conf.get("field")
        if (
            isinstance(field, str)
            and isinstance(props, dict)
            and list(props) == [field]
            and required == [field]
        ):
            rule = props[field]
            if (
                isinstance(rule, dict)
                and rule.get("type") == "boolean"
                and not set(rule) - {"type", "title", "description", "default"}
            ):
                return Approval(message, {field: True}, ())
    return None


def linked_tasks(item):
    result = []
    for entry in item["content"].get("attachments") or []:
        if (
            isinstance(entry, dict)
            and entry.get("type") == "thread"
            and entry.get("thread_id")
        ):
            from wechat2dot.dot.client import DotError, DotProtocolError

            try:
                result.append(ident(entry["thread_id"]))
            except DotError:
                raise DotProtocolError("invalid linked task") from None
    return result


async def update_cards(s, dot, binding):
    rows = s.db.execute(
        "SELECT id,stage FROM native_cards WHERE stage IN ('new','running')"
    ).fetchall()
    for tid, old in rows:
        name, stage = await dot.task_status(tid, binding.account_id)
        terminal = (
            "completed"
            if stage == "completed"
            else "failed"
            if stage in ("systemError", "failed", "interrupted")
            else None
        )
        with s._transaction():
            if old == "new":
                s.enqueue(
                    "task:" + tid, 0, s.clock(), "text", f"任务：{name}（进行中）"
                )
            if terminal:
                label = "已完成" if terminal == "completed" else "失败"
                s.enqueue("task:" + tid, 1, s.clock(), "text", f"{label}：{name}")
            s.db.execute(
                "UPDATE native_cards SET stage=? WHERE id=?",
                (terminal or "running", tid),
            )
