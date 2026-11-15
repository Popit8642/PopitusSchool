import re
from io import BytesIO

from docx import Document as DocxDocument
from docx.shared import Pt


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
FULL_BOLD_RE = re.compile(r"^\s*\*\*([^*\n]{1,80})\*\*\s*:?\s*$")
BULLET_RE = re.compile(r"^\s{0,3}[-*•]\s+")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
BAD_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _add_runs(paragraph, text: str) -> None:
    """Добавляет текст в параграф, превращая **жирный** в bold-фрагменты."""
    position = 0
    for match in BOLD_RE.finditer(text):
        if match.start() > position:
            paragraph.add_run(text[position : match.start()])
        run = paragraph.add_run(match.group(1))
        run.bold = True
        position = match.end()
    if position < len(text):
        paragraph.add_run(text[position:])


def _heading(line: str) -> tuple[int, str] | None:
    match = HEADING_RE.match(line)
    if match:
        return min(len(match.group(1)), 4), match.group(2).strip()
    match = FULL_BOLD_RE.match(line)
    if match:
        # Короткая строка целиком в **жирном** — это заголовок раздела.
        return 2, match.group(1).strip().rstrip(":").strip()
    return None


def build_lesson_docx(
    *,
    topic: str,
    content: str,
    document_title: str | None = None,
    sources: str | None = None,
) -> bytes:
    """Собирает .docx из сырого текста урока с базовым форматированием."""
    document = DocxDocument()
    document.core_properties.title = topic or "Урок"
    document.core_properties.subject = "Урок, сгенерированный PopitusSchool"

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    document.add_heading(topic or "Урок", level=0)

    meta: list[str] = []
    if document_title:
        meta.append(f"Учебник: {document_title}")
    if sources:
        meta.append(f"Источники: страницы {sources}")
    if meta:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(" · ".join(meta))
        run.italic = True

    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line in {"---", "***", "___"}:
            continue

        heading = _heading(line)
        if heading is not None:
            level, title = heading
            document.add_heading(title.replace("**", ""), level=level)
            continue

        if BULLET_RE.match(line):
            paragraph = document.add_paragraph(style="List Bullet")
            _add_runs(paragraph, BULLET_RE.sub("", line))
            continue

        paragraph = document.add_paragraph()
        _add_runs(paragraph, line)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_filename(topic: str | None, lesson_id: int) -> str:
    base = BAD_FILENAME_RE.sub(" ", topic or "урок").strip()
    base = " ".join(base.split())[:60] or "урок"
    return f"Урок_{base}_{lesson_id}.docx"
