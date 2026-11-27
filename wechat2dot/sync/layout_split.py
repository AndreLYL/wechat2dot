"""Pack formatted Markdown blocks into WeChat text payloads."""

import re

from markdown_it import MarkdownIt

from wechat_dot_bridge.message_format import MAX_TEXT_BYTES, MAX_TEXT_CHARS, split_text


_NEWLINE = re.compile(r"\r\n|[\r\n]")
_LIST_ITEM = re.compile(r"(?P<indent> {0,3})(?:[-+*]|[0-9]{1,9}[.)])(?=[ \t\r\n]|\Z)")
_FENCE_OPEN = re.compile(
    r"\A(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})[^\r\n]*(?P<newline>\r\n|[\r\n])"
)


def _fits(text):
    return len(text) <= MAX_TEXT_CHARS and len(text.encode("utf-8")) <= MAX_TEXT_BYTES


def _pack(pieces):
    current = ""
    for piece in pieces:
        if current and not _fits(current + piece):
            yield current
            current = ""
        current += piece
    if current:
        yield current


def _at_boundaries(text, cuts, keep_together):
    """Skip a candidate cut inside a link or inline code span that fits one bubble."""
    matches = iter(keep_together.finditer(text)) if keep_together else iter(())
    match = next(matches, None)
    start = 0
    for cut in cuts:
        while match is not None and match.end() <= cut:
            match = next(matches, None)
        if match is not None and match.start() < cut and _fits(match.group()):
            continue
        yield text[start:cut]
        start = cut
    if start < len(text):
        yield text[start:]


def _lines(text, keep_together=None):
    cuts = (match.end() for match in _NEWLINE.finditer(text))
    return _at_boundaries(text, cuts, keep_together)


def _list_items(text, keep_together):
    first = _LIST_ITEM.match(text)
    if first is None:
        yield text
        return
    cuts = (
        end
        for newline in _NEWLINE.finditer(text)
        if (
            (end := newline.end()) < len(text)
            and (item := _LIST_ITEM.match(text, end)) is not None
            and item["indent"] == first["indent"]
        )
    )
    yield from _at_boundaries(text, cuts, keep_together)


def _ordinary_parts(text, kind, keep_together):
    units = _list_items(text, keep_together) if kind == "list" else (text,)
    for unit in units:
        if _fits(unit):
            yield unit
            continue
        for line in _lines(unit, keep_together):
            if _fits(line):
                yield line
            else:
                yield from split_text(line, keep_together=keep_together)


def _container_parts(text, kind, keep_together):
    if (
        kind not in {"list", "quote"}
        or _fits(text)
        or ("```" not in text and "~~~" not in text)
    ):
        yield from _ordinary_parts(text, kind, keep_together)
        return
    # Markdown tokens mark complete source lines even when a fence is nested.
    starts = [0, *(match.end() for match in _NEWLINE.finditer(text))]
    if starts[-1] < len(text):
        starts.append(len(text))
    offset = 0
    for token in MarkdownIt("commonmark").parse(text):
        if token.type != "fence" or token.map is None:
            continue
        start, end = (starts[line] for line in token.map)
        yield from _ordinary_parts(text[offset:start], kind, keep_together)
        yield from _ordinary_parts(text[start:end], "fence", keep_together)
        offset = end
    yield from _ordinary_parts(text[offset:], kind, keep_together)


def _fenced_parts(head, text, keep_together):
    opening = _FENCE_OPEN.match(text)
    if opening is None:
        yield from _ordinary_parts(head + text, "fence", keep_together)
        return
    opener = opening.group()
    marker = opening["marker"]
    newline = opening["newline"]
    closer = opening["indent"] + marker + newline
    close_line = re.compile(r" {0,3}" + re.escape(marker) + marker[0] + r"*[ \t]*")
    body = list(_lines(text[opening.end():]))
    final_close = closer
    trailing = ""
    for i, line in enumerate(body):
        if close_line.fullmatch(line.rstrip("\r\n")):
            final_close = line
            trailing = "".join(body[i + 1:])
            body = body[:i]
            break

    def wrapped(content, close):
        separator = newline if content and not content.endswith(("\n", "\r")) else ""
        return opener + content + separator + close

    def can_wrap(prefix, content):
        return _fits(prefix + wrapped(content, closer)) and _fits(
            prefix + wrapped(content, final_close)
        )

    if not body or any(not can_wrap("", line) for line in body):
        # A single code line plus its fences can exceed the protocol limits.
        # In that case every character is retained in reassemblable raw fragments.
        yield from split_text(head + text, keep_together=keep_together)
        return
    if head and not can_wrap(head, body[0]):
        yield from _ordinary_parts(head, "heading", keep_together)
        head = ""
    content = ""
    for line in body:
        if not can_wrap(head, content + line):
            yield head + wrapped(content, closer)
            head, content = "", ""
        content += line
    yield head + wrapped(content, final_close)
    if trailing:
        yield from _ordinary_parts(trailing, "paragraph", keep_together)


def _parts(blocks, keep_together):
    head = ""
    for block in blocks:
        if block.kind == "heading":
            head += block.text
            continue
        if _fits(head + block.text):
            yield head + block.text
        elif block.kind == "fence" and not _fits(block.text):
            yield from _fenced_parts(head, block.text, keep_together)
        else:
            parts = iter(_container_parts(block.text, block.kind, keep_together))
            first = next(parts, "")
            if _fits(head + first):
                yield head + first
            elif head and block.kind == "paragraph" and _fits(head):
                yield from split_text(head + first, keep_together=keep_together)
            else:
                yield from _ordinary_parts(head, "heading", keep_together)
                yield first
            yield from parts
        head = ""
    if head:
        yield from _ordinary_parts(head, "heading", keep_together)


def split_layout(blocks, *, keep_together):
    """Prefer block and list-item boundaries, without exceeding either transport limit."""
    return list(_pack(_parts(blocks, keep_together)))
