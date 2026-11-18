"""Shared human-readable status for the CLI and the bound WeChat owner."""


def render(status):
    health = "正常" if status["healthy"] else "需关注：" + "、".join(status["issues"])
    out, inbox = status["outbox"], status["inbox"]
    age = status["context_age_seconds"]
    context = (
        "尚无来信" if age is None else f"{age // 3600} 小时 {(age % 3600) // 60} 分钟"
    )
    return (
        f"微信 ⇄ DOT：{health}\n"
        f"距最近微信来信：{context}\n"
        f"待发：{out.get('queued', 0)}，等待新来信：{out.get('waiting_context', 0)}，"
        f"待核对：{out.get('uncertain', 0) + inbox.get('uncertain', 0)}，"
        f"失败：{out.get('failed', 0) + inbox.get('failed', 0)}"
    )
