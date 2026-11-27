"""Adapt recognized Markdown blocks to the native WeChat text view."""

from collections import Counter
from dataclasses import dataclass
import re
from unicodedata import category, east_asian_width

from markdown_it import MarkdownIt, rules_inline
from markdown_it.common.utils import normalizeReference

from wechat_dot_bridge.message_format import MAX_TEXT_BYTES, MAX_TEXT_CHARS


@dataclass(frozen=True)
class LayoutBlock:
    kind: str
    text: str


_SOURCE_LINES = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")
_LINE_END = re.compile(r"(?:\r\n|\r|\n)$")
_QUOTE_PREFIX = r"(?:[ \t]*>[ \t]?)+"
_LIST_PREFIX = rf"(?:{_QUOTE_PREFIX})?[ \t]*(?:[-+*]|[0-9]{{1,9}}[.)])[ \t]+"
_TASK = re.compile(rf"^({_LIST_PREFIX})\[([xX ])\]([ \t]+)(?=\S)")
_ALERT = re.compile(
    rf"^({_QUOTE_PREFIX})[ \t]*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*$"
)
_ALERT_NAMES = {
    "NOTE": "提示",
    "TIP": "建议",
    "IMPORTANT": "重要",
    "WARNING": "警告",
    "CAUTION": "注意",
}
_BRIEF = re.compile(r"^([ ]{0,3}(?:[-+*] )?)(结果|结论|下一步)([：:][ \t]*)(\S.*)$")
_LABEL_TEXT = re.compile(r"[\w\u3400-\u9fff /.-]+")
_SAFE_SCHEME = re.compile(r"(?i)^(?:https?://|codex://|mailto:)")
_URL = re.compile(r"(?i)(?:https?://|mailto:|www\.)[^\s<>]+")
_ESCAPED_BRACKETS = re.compile(r"\\\[([^\[\]\\\r\n]+)\\\](?![\[(])")
_CAPTION_SPECIAL = re.compile(r"([!\\`*_{}\[\]<>~&])")
_TABLE_PIPE = re.compile(r"(?<!\\)\|")
_NOTE = re.compile(r"\[\^([\w.:-]+)\](?![\[(:])")
_NOTE_DEFINITION = re.compile(r"^ {0,3}\[\^([\w.:-]+)\]:[ \t]+(\S[^\r\n]*)$")
_VISIBLE_INLINE = {"text", "text_special", "code_inline", "image"}
_BLOCK_KINDS = {
    "blockquote_open": "quote",
    "bullet_list_open": "list",
    "ordered_list_open": "list",
    "heading_open": "heading",
    "table_open": "table",
    "fence": "fence",
    "code_block": "fence",
    "hr": "rule",
    "html_block": "html",
    "definition": "definition",
}
# The two- and three-column phone fixtures fit within this estimate. It includes
# room for inter-column spacing, and does not depend on Python's byte length.
_TABLE_WIDTH = 34
_TABLE_COLUMN_GAP = 3


def _end_of_line(line):
    match = _LINE_END.search(line)
    return match.group() if match else ""


def _inline_link(label, target, title):
    if not _SAFE_SCHEME.match(target) or re.search(r"[<>\s\\]", target):
        return None
    if title:
        quoted = " ".join(title.split()).replace("\\", "\\\\").replace('"', '\\"')
        return f'[{label}](<{target}> "{quoted}")'
    return f"[{label}](<{target}>)"


def _caption(title):
    if not title:
        return ""
    text = _CAPTION_SPECIAL.sub(r"\\\1", " ".join(title.split()))
    return f"（{text}）"


def _capture_links(rule, is_image=False):
    """Use the existing CommonMark recognizers to obtain exact source spans."""

    def wrapped(state, silent):
        start, previous_count = state.pos, len(state.tokens)
        matched = rule(state, silent)
        scan = state.env.get("_wechat_inline")
        if not matched or silent or scan is None or scan.source is not state.src:
            return matched
        if is_image and state.linkLevel:
            return matched
        expected = "image" if is_image else "link_open"
        token = next(
            (item for item in state.tokens[previous_count:] if item.type == expected),
            None,
        )
        if token is None:
            return matched
        label = token.meta.get("label")
        title = token.attrGet("title") or ""
        target = token.attrGet("src" if is_image else "href") or ""
        if label or (is_image and _SAFE_SCHEME.match(target)):
            label_start = start + (1 if is_image else 0)
            label_end = state.md.helpers.parseLinkLabel(
                state, label_start, not is_image
            )
            if label_end < 0:
                return matched
            label_text = state.src[label_start + 1 : label_end]
            if is_image and any(
                child.type == "link_open" for child in token.children or ()
            ):
                # Image alt text can contain links, but a link label cannot.
                plain = state.md.renderer.renderInlineAsText(
                    token.children, state.md.options, state.env
                )
                label_text = _CAPTION_SPECIAL.sub(lambda match: "\\" + match[0], plain)
            if label:
                target = scan.reference_targets.get(label, target)
                replacement = _inline_link(label_text, target, "" if is_image else title)
            else:
                destination = state.src[label_end + 1 : state.pos]
                replacement = f"[{label_text or '图片'}]{destination}"
        else:
            replacement = None
        if replacement:
            if is_image:
                replacement += _caption(title)
            scan.add(start, state.pos, replacement, label)
        return matched

    return wrapped


def _capture_escape(state, silent):
    start = state.pos
    matched = rules_inline.escape(state, silent)
    scan = state.env.get("_wechat_inline")
    if not matched or silent or scan is None or scan.source is not state.src:
        return matched
    if state.src[start + 1] == "\n" and state.pos < state.posMax:
        # A final line ending is excluded from CommonMark's inline span.
        scan.add(start, start + 1, "")
    elif state.src.startswith("\\\r\n", start) and state.src[start + 3 :].strip(
        " \t\r\n"
    ):
        scan.add(start, start + 1, "")
    elif (brackets := _ESCAPED_BRACKETS.match(state.src, start)) and (
        normalizeReference(brackets[1]) not in state.env.get("references", {})
        and (not start or state.src[start - 1] != "!")
    ):
        scan.add(start, brackets.end(), f"[{brackets[1]}]")
    return matched


def _capture_entity(state, silent):
    start = state.pos
    matched = rules_inline.entity(state, silent)
    scan = state.env.get("_wechat_inline")
    if not matched or silent or scan is None or scan.source is not state.src:
        return matched
    value = state.tokens[-1].content
    following = state.src[state.pos : state.pos + 1]
    allow_punctuation = value in {"&", "<"} and (not following or following.isspace())
    if allow_punctuation or all(
        category(char)[0] in "LNS" and char != "\ufffd" for char in value
    ):
        scan.add(start, state.pos, value)
    return matched


def _capture_note(state, silent):
    scan = state.env.get("_wechat_inline")
    if scan is None or scan.source is not state.src or state.linkLevel:
        return False
    match = _NOTE.match(state.src, state.pos, state.posMax)
    if (
        not match
        or match[1] not in scan.footnotes
        or (state.pos and state.src[state.pos - 1] == "!")
    ):
        return False
    if not silent:
        scan.add(state.pos, match.end(), "", ("note", match[1]))
        state.append_pending(match[0])
    state.pos = match.end()
    return True


_MARKDOWN = MarkdownIt(
    "commonmark", {"inline_definitions": True, "store_labels": True}
).enable(["table", "strikethrough"])
_MARKDOWN.inline.ruler.at("link", _capture_links(rules_inline.link))
_MARKDOWN.inline.ruler.at("image", _capture_links(rules_inline.image, is_image=True))
_MARKDOWN.inline.ruler.at("escape", _capture_escape)
_MARKDOWN.inline.ruler.at("entity", _capture_entity)
_MARKDOWN.inline.ruler.before("link", "wechat_footnote", _capture_note)


class _InlineScan:
    def __init__(self, source, reference_targets, footnotes, table):
        self.source, self.reference_targets, self.table = (
            source,
            reference_targets,
            table,
        )
        self.footnotes = footnotes
        self.edits = []

    def add(self, start, end, replacement, label=None):
        if self.table and (
            "\n" in replacement or bool(_TABLE_PIPE.search(replacement))
        ):
            return
        self.edits.append((start, end, replacement, label))

    def render(self, used_refs, note_numbers):
        parts = []
        position = 0
        urls = iter(_URL.finditer(self.source))
        url = next(urls, None)
        for start, end, replacement, label in sorted(
            self.edits, key=lambda edit: (edit[0], -edit[1])
        ):
            if start < position:
                continue
            while url is not None and url.end() <= start:
                url = next(urls, None)
            if url is not None and url.start() <= start < url.end():
                continue
            if isinstance(label, tuple):
                number = note_numbers.setdefault(label[1], len(note_numbers) + 1)
                replacement = f"（注 {number}）"
            parts.extend((self.source[position:start], replacement))
            position = end
            if label:
                used_refs[label] += 1
        parts.append(self.source[position:])
        return "".join(parts)


def _inline_tokens(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from _inline_tokens(token.children)


def _reference_targets(lines, env):
    targets = {}
    for label, definition in env.get("references", {}).items():
        start, end = definition["map"]
        text = "".join(lines[start:end])
        match = re.match(r"^[ \t]*\[(?:[^\[\]\\]|\\.)+\]:[ \t\r\n]*", text)
        if match:
            dest = _MARKDOWN.helpers.parseLinkDestination(text, match.end(), len(text))
            if dest.ok and _MARKDOWN.normalizeLink(dest.str) == definition["href"]:
                targets[label] = dest.str
    return targets


def _display_width(token):
    if token.children is None:
        return 0
    return sum(
        2 if east_asian_width(char) in {"F", "W"} else 1
        for item in token.children
        if item.type in _VISIBLE_INLINE
        for char in (
            item.content + (item.attrGet("title") or "")
            if item.type == "image"
            else item.content
        )
        if category(char)[0] != "M"
    )


def _table_records(children, lines, render_cell):
    rows = []
    row = None
    for token in children:
        if token.type == "tr_open":
            row = []
            rows.append(row)
        elif token.type == "inline" and row is not None:
            row.append(token)
    if len(rows) < 2:
        return None
    columns = len(rows[0])
    # A row wider than its declared header has cells discarded by GFM. Preserve
    # the source rather than silently dropping user data during flattening.
    for line in lines[2:]:
        raw = line.strip()
        pipes = len(_TABLE_PIPE.findall(raw))
        fields = (
            pipes
            + 1
            - raw.startswith("|")
            - (raw.endswith("|") and not raw.endswith("\\|"))
        )
        if fields > columns:
            return None
    width = sum(max(_display_width(cell) for cell in column) for column in zip(*rows))
    width += _TABLE_COLUMN_GAP * (columns - 1)
    raw_text = "".join(lines)
    if (
        columns <= 3
        and width <= _TABLE_WIDTH
        and len(raw_text) <= MAX_TEXT_CHARS
        and len(raw_text.encode()) <= MAX_TEXT_BYTES
    ):
        return None
    keys = [render_cell(cell.content) for cell in rows[0]]
    records = []
    for row in rows[1:]:
        fields = []
        for i, (key, cell) in enumerate(zip(keys, row)):
            label = f"**{key}** " if _LABEL_TEXT.fullmatch(key) else f"{key}："
            prefix = "- " if i == 0 else "  - "
            fields.append(f"{prefix}{label}{render_cell(cell.content)}".rstrip())
        records.append("\n".join(fields))
    return records


def _code_fence(token, lines):
    newline = _end_of_line(lines[0]) or "\n"
    fence = min(
        (
            marker
            * max(
                3,
                1
                + max(
                    (len(run) for run in re.findall(f"{marker}+", token.content)),
                    default=0,
                ),
            )
            for marker in "`~"
        ),
        key=len,
    )
    # The parser removes precisely the indentation consumed by the code block.
    content = token.content.replace("\n", newline)
    return fence + newline + content + fence + _end_of_line(lines[-1])


def _footnotes(roots, lines):
    candidates, duplicates = {}, set()
    for _, token in roots:
        start, end = token.map
        if token.type not in {"paragraph_open", "definition"} or end != start + 1:
            continue
        match = _NOTE_DEFINITION.fullmatch(lines[start].rstrip("\r\n"))
        if not match:
            continue
        if match[1] in candidates:
            duplicates.add(match[1])
        candidates[match[1]] = start, match[2]
    return {
        label: details
        for label, details in candidates.items()
        if label not in duplicates
    }


def layout_blocks(text, *, render_links):
    """Return source-ordered blocks with their original Markdown spacing."""
    lines = _SOURCE_LINES.findall(text)
    env = {}
    tokens = _MARKDOWN.parse(text, env)
    reference_targets = _reference_targets(lines, env)
    source_references = Counter(
        token.meta["label"]
        for token in _inline_tokens(tokens)
        if token.type in {"link_open", "image"} and token.meta.get("label")
    )
    duplicate_references = {
        normalizeReference(ref["label"]) for ref in env.get("duplicate_refs", ())
    }
    used_refs = Counter()
    roots = [
        (i, token)
        for i, token in enumerate(tokens)
        if token.level == 0 and token.map and token.type != "inline"
    ]
    footnotes = _footnotes(roots, lines)
    notes_by_line = {line: (label, body) for label, (line, body) in footnotes.items()}
    note_numbers = {}

    def render(source, table=False):
        if any(char in source for char in "[&\\"):
            scan = _InlineScan(source, reference_targets, footnotes, table)
            _MARKDOWN.inline.parse(
                source, _MARKDOWN, {**env, "_wechat_inline": scan}, []
            )
            source = scan.render(used_refs, note_numbers)
        return render_links(source)

    blocks = []
    if not roots or roots[0][1].map[0]:
        prefix = "".join(lines[: roots[0][1].map[0]]) if roots else text
        if prefix:
            blocks.append((LayoutBlock("paragraph", render(prefix)), None))
    for cursor, (i, token) in enumerate(roots):
        start, end = token.map
        next_index, next_line = (len(tokens), len(lines))
        if cursor + 1 < len(roots):
            next_index, next_token = roots[cursor + 1]
            next_line = next_token.map[0]
        children = tokens[i + 1 : next_index]
        source = lines[start:end]
        trailing = "".join(lines[end:next_line])
        kind = _BLOCK_KINDS.get(token.type, "paragraph")
        definition = None
        if start in notes_by_line:
            label, body = notes_by_line[start]
            definition = ("note", label, body, _end_of_line(source[-1]) + trailing)
            rendered = "".join(source)
        elif token.type == "definition":
            definition = token.meta["id"]
            rendered = "".join(source)
        elif token.type == "code_block":
            rendered = _code_fence(token, source)
        elif kind in {"fence", "html"}:
            rendered = "".join(source)
        elif kind == "table":
            records = _table_records(children, source, render)
            if records:
                for n, record in enumerate(records):
                    suffix = (
                        "\n\n"
                        if n + 1 < len(records)
                        else _end_of_line(source[-1]) + trailing
                    )
                    blocks.append((LayoutBlock("list", record + suffix), None))
                continue
            rendered = render("".join(source), table=True)
        elif kind == "heading" and token.markup in {"=", "-"}:
            if len(source) == 2:
                heading = source[0].strip()
                rendered = render(f"{'#' * int(token.tag[1:])} {heading}")
                rendered += _end_of_line(source[-1])
            else:
                # ATX headings are one line. Joining could discard hard breaks.
                rendered = "".join(source)
        else:
            rendered = _render_prose(source, children, start, kind, render)
        blocks.append((LayoutBlock(kind, rendered + trailing), definition))
    result = []
    for block, definition in blocks:
        if isinstance(definition, tuple):
            _, label, body, suffix = definition
            if label in note_numbers:
                block = LayoutBlock(
                    "paragraph", f"**注 {note_numbers[label]}** {body}{suffix}"
                )
        elif (
            definition
            and definition not in duplicate_references
            and (
                used_refs[definition]
                and used_refs[definition] == source_references[definition]
            )
        ):
            continue
        result.append(block)
    return result


def _render_prose(lines, children, offset, kind, render):
    protected = {
        i
        for token in children
        if token.map
        and token.type in {"fence", "code_block", "html_block", "definition"}
        for i in range(token.map[0] - offset, token.map[1] - offset)
    }
    for token in children:
        if not token.map:
            continue
        n = token.map[0] - offset
        if n in protected:
            continue
        if token.type == "list_item_open":
            lines[n] = _TASK.sub(
                lambda m: f"{m[1]}（{'待办' if m[2] == ' ' else '已完成'}）{m[3]}",
                lines[n],
            )
        elif token.type == "blockquote_open":
            line = lines[n].rstrip("\r\n")
            alert = _ALERT.fullmatch(line)
            if alert:
                lines[n] = (
                    f"{alert[1]}**{_ALERT_NAMES[alert[2]]}**{_end_of_line(lines[n])}"
                )
    if kind == "quote" and lines:
        line = lines[0].rstrip("\r\n")
        alert = _ALERT.fullmatch(line)
        if alert:
            lines[0] = f"{alert[1]}**{_ALERT_NAMES[alert[2]]}**{_end_of_line(lines[0])}"
    if kind in {"list", "paragraph"} and not any("`" in line for line in lines):
        brief = [
            (_BRIEF.fullmatch(line.rstrip("\r\n")) if n not in protected else None)
            for n, line in enumerate(lines)
        ]
        labels = {field[2] for field in brief if field and len(field[4]) <= 100}
        if "下一步" in labels and ("结果" in labels or "结论" in labels):
            for n, field in enumerate(brief):
                if field and len(field[4]) <= 100:
                    lines[n] = (
                        f"{field[1]}**{field[2]}**{field[3]}{field[4]}{_end_of_line(lines[n])}"
                    )
    parts = []
    start = 0
    for n in sorted(protected):
        if start < n:
            parts.append(render("".join(lines[start:n])))
        parts.append(lines[n])
        start = n + 1
    if start < len(lines):
        parts.append(render("".join(lines[start:])))
    return "".join(parts)
