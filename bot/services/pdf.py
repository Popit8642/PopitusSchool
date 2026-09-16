import re
import pymupdf4llm
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter


def _split_long_paragraph(text: str, chunk_size: int) -> list[str]:
    """Если абзац больше chunk_size — режем по предложениям."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) + 1 <= chunk_size:
            buf = f"{buf} {s}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = s
    if buf:
        chunks.append(buf)
    return chunks


def split_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    """Режем markdown на чанки по абзацам с перекрытием."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(para, chunk_size))
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap:]
            overlapped.append(f"{tail}\n\n{chunks[i]}")
        chunks = overlapped

    return chunks


def _preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Предобработка картинки для лучшего OCR."""
    image = image.convert("L")  # grayscale
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)  # повышаем контраст
    image = image.filter(ImageFilter.MedianFilter())  # убираем шум
    image = image.filter(ImageFilter.SHARPEN)  # резкость
    return image


def _ocr_pdf(pdf_path: str) -> list[dict]:
    """OCR-обработка сканированного PDF."""
    print("[ocr] PDF похож на сканы, запускаю OCR...")
    images = convert_from_path(pdf_path, dpi=300)
    print(f"[ocr] страниц: {len(images)}")

    result: list[dict] = []
    for i, image in enumerate(images, start=1):
        processed = _preprocess_for_ocr(image)
        text = pytesseract.image_to_string(processed, lang="rus+eng")

        if not text.strip():
            continue

        for piece in split_text(text):
            result.append({"page": i, "content": piece})

        if i % 5 == 0:
            print(f"[ocr] обработано страниц: {i}/{len(images)}")

    return result


def extract_and_chunk(pdf_path: str) -> list[dict]:
    """Возвращает [{"page": int, "content": str}, ...]"""
    # Сначала пробуем обычный парсер (быстро, если текстовый слой есть)
    pages = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)

    result: list[dict] = []
    for i, page in enumerate(pages, start=1):
        meta = page.get("metadata", {})
        page_num = meta.get("page_number") or i

        text = page.get("text", "")
        if not text.strip():
            continue

        for piece in split_text(text):
            result.append({"page": page_num, "content": piece})

    # Если текста не нашли — это сканы, запускаем OCR
    if not result:
        result = _ocr_pdf(pdf_path)

    return result