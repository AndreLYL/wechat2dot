"""Weixin text and quoted-message handling; protocol reference Tencent MIT source."""
import hashlib


MAX_TEXT_CHARS = 3800
MAX_TEXT_BYTES = 8192


def _text_units(text, keep_together, max_chars, max_bytes):
    offset = 0
    for match in keep_together.finditer(text):
        yield from text[offset:match.start()]
        unit = match.group()
        if len(unit) <= max_chars and len(unit.encode("utf-8")) <= max_bytes:
            yield unit
        else:
            yield from unit
        offset = match.end()
    yield from text[offset:]


def split_text(text, max_chars=MAX_TEXT_CHARS, max_bytes=MAX_TEXT_BYTES, *, keep_together=None):
    chunks, current, chars, size = [], [], 0, 0
    units = (
        text if keep_together is None
        else _text_units(text, keep_together, max_chars, max_bytes)
    )
    for unit in units:
        count = len(unit.encode("utf-8"))
        if current and (chars + len(unit) > max_chars or size + count > max_bytes):
            chunks.append(''.join(current))
            current, chars, size = [], 0, 0
        current.append(unit)
        chars += len(unit)
        size += count
    if current:
        chunks.append(''.join(current))
    return chunks or ['']


def partial_quote(text, partial):
    """Both observed end-index conventions, verified by protocol MD5 when given."""
    if not isinstance(partial, dict):
        return text
    def nth(needle, index, start=0):
        if not isinstance(needle, str) or not needle or type(index) is not int or not 0 <= index <= len(text):
            return -1
        for _ in range(index + 1):
            pos = text.find(needle, start)
            if pos < 0:
                return -1
            start = pos + len(needle)
        return pos
    start = nth(partial.get('start'), partial.get('startindex'))
    if start < 0:
        return text
    for origin in (0, start + len(partial['start'])):
        end = nth(partial.get('end'), partial.get('endindex'), origin)
        if end < start:
            continue
        candidate = text[start:end + len(partial['end'])]
        digest = partial.get('quotemd5')
        if not digest or (isinstance(digest, str) and hashlib.md5(candidate.encode(), usedforsecurity=False).hexdigest() == digest.lower()):
            return candidate
    return text


def quote_content(ref):
    if not isinstance(ref, dict):
        return '', None
    item = ref.get('message_item')
    text = ref.get('title') if isinstance(ref.get('title'), str) else ''
    media = None
    if isinstance(item, dict):
        body = item.get('text_item') if item.get('type') == 1 else item.get('voice_item') if item.get('type') == 3 else None
        if isinstance(body, dict) and isinstance(body.get('text'), str):
            text = '\n'.join(filter(None, [text, partial_quote(body['text'], ref.get('partial_text'))]))
        elif item.get('type') in (2, 3, 4, 5):
            media = item
    if not text and not media:
        text = '[引用内容暂不可用]'
    return text[:16384], media


def compact_quote_text(text, limit=64):
    if not isinstance(text, str):
        return ""
    line = " ".join(text.split())
    if len(line) > limit:
        line = line[:limit - 1] + "…"
    return line.encode("utf-8", errors="replace").decode("utf-8")


def compact_attachment_quote(kind, name=None):
    label = {"image": "图片", "file": "文件", "video": "视频", "voice": "语音"}.get(kind, "附件")
    if isinstance(name, str) and name.strip():
        return label + "《" + compact_quote_text(name, 64 - len(label) - 2) + "》"
    return label


def markdown_to_text(text):
    """Use the upstream Weixin-compatible parser when Node is installed."""
    import shutil
    from pathlib import Path
    from .audio import _run
    from .errors import BridgeError
    node = shutil.which("node")
    if node:
        try:
            return _run([node, str(Path(__file__).parent / "codecs/markdown.mjs")], text.encode(), 262144).decode()
        except BridgeError:
            pass
    return _plain_markdown(text)


def _plain_markdown(text):
    """Plain Weixin rendering with code contents and link destinations preserved."""
    import re
    output, fenced = [], False
    for line in text.splitlines(keepends=True):
        if re.match(r'^\s*(```|~~~)', line):
            fenced = not fenced
            continue
        if not fenced:
            line = re.sub(r'^ {0,3}#{1,6}\s+', '', line)
            line = re.sub(r'!?\[([^\]\n]*)\]\(([^)\n]+)\)', lambda m: m[1] + ' (' + m[2] + ')', line)
            line = re.sub(r'(`+)([^`\n]+)\1', r'\2', line)
            line = re.sub(r'(\*\*|__|~~)(.+?)\1', r'\2', line)
            line = re.sub(r'(?<!\w)\*([^*\n]+)\*(?!\w)', r'\1', line)
            if re.match(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$', line):
                continue
        output.append(line)
    return ''.join(output)


def reference_id(value):
    """Opaque Weixin reference ID, not a URL or credential."""
    import re
    if isinstance(value,int) and not isinstance(value,bool):
        value=str(value)
    if not isinstance(value,str): return None
    value=value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,256}",value) or value=="0": return None
    return str(int(value)) if value.isdigit() else value
