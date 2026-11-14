import html
import re
from collections.abc import Iterable


HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
BULLET_RE = re.compile(r"^\s{0,3}[-*]\s+")
BOLD_RE = re.compile(r"\*\*([^\n]+?)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
CODE_FENCE_RE = re.compile(r"^```.*$", re.MULTILINE)
HTML_TAG_RE = re.compile(r"<[^>]+>")


def convert_markdown_to_html(text: str) -> str:
    """Аккуратно превращает типичный Markdown LLM в безопасный HTML."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CODE_FENCE_RE.sub("", text)
    text = html.escape(text, quote=False)
    text = LINK_RE.sub(r"\1", text)

    lines: list[str] = []
    for line in text.split("\n"):
        if HEADING_RE.match(line):
            title = HEADING_RE.sub("", line).strip()
            lines.append(f"<b>{title}</b>")
        elif BULLET_RE.match(line):
            lines.append(BULLET_RE.sub("• ", line))
        else:
            lines.append(line)

    text = "\n".join(lines)
    text = BOLD_RE.sub(r"<b>\1</b>", text)
    text = text.replace("**", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_plain(text: str) -> str:
    return html.unescape(HTML_TAG_RE.sub("", text))


def _split_long_line(line: str, limit: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", line)
    result: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) + 1 <= limit:
            buffer = f"{buffer} {sentence}".strip()
            continue
        if buffer:
            result.append(buffer)
        if len(sentence) <= limit:
            buffer = sentence
        else:
            result.extend(_hard_split(sentence, limit))
            buffer = ""
    if buffer:
        result.append(buffer)
    return result


def _hard_split(text: str, limit: int) -> list[str]:
    words = text.split()
    result: list[str] = []
    buffer = ""
    for word in words:
        while len(word) > limit:
            if buffer:
                result.append(buffer)
                buffer = ""
            result.append(word[:limit])
            word = word[limit:]
        candidate = f"{buffer} {word}".strip() if buffer else word
        if len(candidate) <= limit:
            buffer = candidate
        else:
            if buffer:
                result.append(buffer)
            buffer = word
    if buffer:
        result.append(buffer)
    return result


def _split_raw(text: str, limit: int) -> list[str]:
    """Делит исходный текст на логические блоки, не длиннее limit."""
    text = text.strip()
    if not text:
        return []

    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
            continue
        for line in paragraph.split("\n"):
            line = line.strip()
            if not line:
                continue
            if len(line) <= limit:
                units.append(line)
            else:
                units.extend(_split_long_line(line, limit))
    return units


def build_html_parts(
    text: str,
    sources: Iterable[int | str] | None = None,
    limit: int = 3800,
) -> list[str]:
    """Готовит безопасные HTML-части сообщения и источники в конце."""
    raw_limit = max(500, limit - 200)
    blocks: list[str] = []

    for unit in _split_raw(text, raw_limit):
        formatted = convert_markdown_to_html(unit)
        if len(formatted) <= limit:
            blocks.append(formatted)
            continue

        # Крайне редкий случай: одна строка с разметкой всё равно огромна
        plain = html_to_plain(formatted)
        for piece in _hard_split(plain, raw_limit):
            blocks.append(html.escape(piece, quote=False))

    if sources:
        source_text = ", ".join(str(item) for item in sources)
        blocks.append(
            f"📖 <b>Источники:</b> страницы {html.escape(source_text, quote=False)}"
        )

    parts: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = block
    if current:
        parts.append(current)
    return parts
