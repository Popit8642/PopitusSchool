import logging
import re
from pathlib import Path

import pymupdf
import pymupdf4llm
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

from config import settings
from errors import PdfNoTextError, PdfUnreadableError


logger = logging.getLogger(__name__)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_NOISE_RE = re.compile(r"[#*_>`~|\[\]()]+")
_LETTERS_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]")


def _split_long_paragraph(text: str, chunk_size: int) -> list[str]:
    """Если абзац больше chunk_size — режем по предложениям."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        if len(buf) + len(sentence) + 1 <= chunk_size:
            buf = f"{buf} {sentence}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = sentence
    if buf:
        chunks.append(buf)
    return chunks


def split_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    """Режем markdown на чанки по абзацам с перекрытием."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, chunk_size))
            continue

        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            if current:
                chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap:]
            overlapped.append(f"{tail}\n\n{chunks[i]}")
        chunks = overlapped

    return chunks


def _meaningful_text(text: str | None) -> bool:
    """Отличает реальный текст страницы от пустых markdown-картинок."""
    if not text:
        return False
    cleaned = _HTML_COMMENT_RE.sub(" ", text)
    cleaned = _MD_IMAGE_RE.sub(" ", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _MD_NOISE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(_LETTERS_RE.findall(cleaned)) >= 20


def _preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Предобработка картинки для лучшего OCR."""
    image = image.convert("L")
    image = ImageEnhance.Contrast(image).enhance(2.0)
    image = image.filter(ImageFilter.MedianFilter())
    image = image.filter(ImageFilter.SHARPEN)
    return image


def _ocr_page(page: pymupdf.Page, page_number: int) -> str:
    """OCR одной страницы. Ошибки не роняют весь документ."""
    pix = None
    image = None
    try:
        pix = page.get_pixmap(dpi=300)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        image = _preprocess_for_ocr(image)
        text = pytesseract.image_to_string(
            image, lang=settings.OCR_LANG, timeout=60
        )
        logger.info(
            "[ocr] страница %s: распознано %s символов",
            page_number,
            len(text.strip()),
        )
        return text
    except Exception:
        logger.exception("[ocr] не удалось распознать страницу %s", page_number)
        return ""
    finally:
        del image
        del pix


def _text_pages_via_pymupdf4llm(pdf_path: str) -> dict[int, str]:
    pages = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)
    result: dict[int, str] = {}
    for index, page in enumerate(pages, start=1):
        text = page.get("text", "") if isinstance(page, dict) else str(page)
        if _meaningful_text(text):
            result[index] = text
    return result


def _text_pages_via_pymupdf(doc: pymupdf.Document) -> dict[int, str]:
    result: dict[int, str] = {}
    for page_number, page in enumerate(doc, start=1):
        try:
            text = page.get_text("markdown")
        except Exception:
            text = page.get_text("text")
        if _meaningful_text(text):
            result[page_number] = text
    return result


def extract_and_chunk(pdf_path: str) -> list[dict]:
    """Возвращает [{"page": int, "content": str}, ...].

    Сначала берём текстовый слой, затем для страниц без текста запускаем OCR.
    """
    path = Path(pdf_path)
    try:
        if not path.exists() or path.stat().st_size == 0:
            raise PdfUnreadableError("Файл не найден или пуст")
    except OSError as exc:
        raise PdfUnreadableError(f"Не удалось прочитать файл: {exc}") from exc

    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        raise PdfUnreadableError(f"PDF не открывается: {exc}") from exc

    try:
        if doc.needs_pass:
            raise PdfUnreadableError("PDF защищён паролем")
        page_count = doc.page_count
        if page_count == 0:
            raise PdfNoTextError("В PDF нет страниц")

        try:
            text_pages = _text_pages_via_pymupdf4llm(str(path))
        except Exception:
            logger.exception(
                "pymupdf4llm не смог разобрать PDF, использую обычное извлечение"
            )
            text_pages = _text_pages_via_pymupdf(doc)

        result: list[dict] = []
        ocr_pages = 0

        for page_number, page in enumerate(doc, start=1):
            text = text_pages.get(page_number, "")
            if not _meaningful_text(text):
                text = _ocr_page(page, page_number)
                if _meaningful_text(text):
                    ocr_pages += 1

            if not text or not text.strip():
                continue

            for piece in split_text(text):
                piece = piece.strip()
                if piece:
                    result.append({"page": page_number, "content": piece})

        logger.info(
            "PDF разобран: страниц=%s, с текстом=%s, OCR=%s, чанков=%s",
            page_count,
            len(text_pages),
            ocr_pages,
            len(result),
        )

        if not result:
            raise PdfNoTextError(
                "В PDF не найден текст и не удалось распознать сканы"
            )
        return result
    finally:
        doc.close()
