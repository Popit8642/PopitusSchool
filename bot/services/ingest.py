import asyncio
import logging
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select

from config import settings
from database.database import async_session
from database.models import DOC_COMPLETED, DOC_FAILED, DOC_PROCESSING, Chunk, Document
from errors import (
    IngestCancelledError,
    PdfNoTextError,
    PdfUnreadableError,
    UnsafeContentError,
)
from services.embeddings import get_embeddings
from services.llm_client import chat_completion
from services.pdf import extract_and_chunk


logger = logging.getLogger(__name__)

MODERATION_PROMPT = (
    "Ты — модератор. Проанализируй текст. Ответь ОДНИМ СЛОВОМ: «ОК», если это "
    "научный, учебный, образовательный или информационный текст. Ответь «БЛОК», "
    "если это порнография, спам, бессмыслица, оскорбления или текст, который "
    "не имеет отношения к учёбе."
)
_BLOCK_MARKERS = (
    "блок",
    "block",
    "запрет",
    "недопуст",
    "unsafe",
    "nsfw",
)
_MODERATION_CHUNKS = 5


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def _document_stats(document_id: uuid.UUID) -> dict[str, int]:
    async with async_session() as session:
        chunks = (
            await session.execute(
                select(func.count(Chunk.id)).where(
                    Chunk.document_id == document_id
                )
            )
        ).scalar() or 0
        pages = (
            await session.execute(
                select(func.count(func.distinct(Chunk.page_number))).where(
                    Chunk.document_id == document_id
                )
            )
        ).scalar() or 0
    return {"pages": int(pages), "chunks": int(chunks)}


async def _clear_chunks(document_id: uuid.UUID) -> None:
    async with async_session() as session:
        await session.execute(
            delete(Chunk).where(Chunk.document_id == document_id)
        )
        await session.commit()


async def _delete_document(document_id: uuid.UUID) -> None:
    """Удаляет документ вместе с чанками (для отклонённого контента)."""
    try:
        async with async_session() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            await session.execute(
                delete(Chunk).where(Chunk.document_id == document_id)
            )
            await session.delete(document)
            await session.commit()
    except Exception:
        logger.exception("Не удалось удалить отклонённый документ %s", document_id)


def _remove_source(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Не удалось удалить файл %s", path)


def _moderation_sample(chunks: list[dict]) -> str:
    """Первые 3–5 чанков, но не больше MODERATION_MAX_CHARS символов."""
    parts: list[str] = []
    total = 0
    for chunk in chunks[:_MODERATION_CHUNKS]:
        content = (chunk.get("content") or "").strip()
        if not content:
            continue
        remaining = settings.MODERATION_MAX_CHARS - total
        if remaining <= 0:
            break
        parts.append(content[:remaining])
        total += len(parts[-1])
    return "\n\n".join(parts).strip()


def _is_blocked_verdict(answer: str) -> bool:
    """Блокируем только при явном вердикте модератора (иначе fail-open)."""
    normalized = (answer or "").casefold()
    return any(marker in normalized for marker in _BLOCK_MARKERS)


async def _moderate(chunks: list[dict], document_id: uuid.UUID) -> None:
    """Проверяет начало документа локальной LLM. Fail-open при сбоях."""
    sample = _moderation_sample(chunks)
    if not sample:
        return

    messages = [
        {"role": "system", "content": MODERATION_PROMPT},
        {"role": "user", "content": f"Текст для проверки:\n\n{sample}"},
    ]
    try:
        answer = await asyncio.wait_for(
            chat_completion(
                messages,
                temperature=0.0,
                model=settings.MODERATION_MODEL,
            ),
            timeout=settings.MODERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "[ingest] модерация %s недоступна (%s: %s) — пропускаю документ",
            document_id,
            type(exc).__name__,
            exc,
        )
        return

    verdict = (answer or "").strip()
    logger.info(
        "[ingest] модерация %s: %r", document_id, verdict[:120]
    )
    if _is_blocked_verdict(verdict):
        raise UnsafeContentError(
            f"Модерация отклонила содержимое: {verdict[:120]}"
        )


async def _mark_failed(document_id: uuid.UUID, error: str) -> None:
    try:
        async with async_session() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            document.status = DOC_FAILED
            document.is_completed = False
            document.error_message = error[:1000]
            await session.commit()
    except Exception:
        logger.exception("Не удалось сохранить статус ошибки для %s", document_id)


async def ingest_document(document_id: str | uuid.UUID) -> dict[str, int]:
    """Разбирает PDF, считает эмбеддинги и сохраняет чанки."""
    started = time.monotonic()
    uid = _as_uuid(document_id)
    if uid is None:
        raise IngestCancelledError("Некорректный id документа")

    async with async_session() as session:
        document = await session.get(Document, uid)
        if document is None:
            raise IngestCancelledError("Документ не найден")
        if document.status == DOC_COMPLETED:
            stats = await _document_stats(uid)
            stats["duration_ms"] = 0
            return stats

        source_file = document.source_file
        title = document.title
        document.status = DOC_PROCESSING
        document.is_completed = False
        document.error_message = None
        await session.commit()

    logger.info("[ingest] старт: %s (%s), файл=%s", title, uid, source_file)

    if not source_file:
        raise PdfUnreadableError("У документа не сохранён исходный файл")

    # Берём только имя файла, чтобы исключить path traversal
    path = settings.upload_dir / Path(source_file).name
    if not path.exists() or path.stat().st_size == 0:
        error = "Исходный PDF-файл не найден"
        await _mark_failed(uid, error)
        raise PdfUnreadableError(error)

    try:
        chunks = extract_and_chunk(str(path))
    except PdfNoTextError as exc:
        logger.info("[ingest] в PDF %s не найден текст — отклоняю", uid)
        await _delete_document(uid)
        _remove_source(path)
        raise UnsafeContentError("В документе нет распознаваемого текста") from exc
    except Exception as exc:
        logger.exception("[ingest] не удалось разобрать PDF %s", uid)
        await _mark_failed(uid, str(exc))
        raise

    try:
        await _moderate(chunks, uid)
    except UnsafeContentError:
        logger.warning("[ingest] документ %s отклонён модерацией", uid)
        await _clear_chunks(uid)
        await _delete_document(uid)
        _remove_source(path)
        raise

    pages_count = len({chunk["page"] for chunk in chunks})
    total = len(chunks)
    logger.info(
        "[ingest] %s: страниц=%s, чанков=%s, начинаю эмбеддинги",
        uid,
        pages_count,
        total,
    )

    # Повторная обработка не должна оставлять старые чанки
    await _clear_chunks(uid)

    processed = 0
    try:
        for start in range(0, total, settings.EMBEDDING_BATCH_SIZE):
            batch = chunks[start : start + settings.EMBEDDING_BATCH_SIZE]
            vectors = await get_embeddings([item["content"] for item in batch])

            async with async_session() as session:
                document = await session.get(Document, uid)
                if document is None:
                    raise IngestCancelledError("Документ удалён во время обработки")

                for item, vector in zip(batch, vectors):
                    session.add(
                        Chunk(
                            document_id=uid,
                            page_number=item["page"],
                            content=item["content"],
                            embedding=vector,
                        )
                    )
                await session.commit()

            processed += len(batch)
            logger.info("[ingest] %s: %s/%s чанков", uid, processed, total)
    except IngestCancelledError:
        raise
    except Exception as exc:
        logger.exception("[ingest] ошибка обработки %s", uid)
        await _clear_chunks(uid)
        await _mark_failed(uid, str(exc))
        raise

    async with async_session() as session:
        document = await session.get(Document, uid)
        if document is None:
            raise IngestCancelledError("Документ удалён во время обработки")
        document.status = DOC_COMPLETED
        document.is_completed = True
        document.error_message = None
        await session.commit()

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[ingest] готово: %s (%s), страниц=%s, чанков=%s, %s мс",
        title,
        uid,
        pages_count,
        total,
        duration_ms,
    )
    return {
        "pages": pages_count,
        "chunks": total,
        "duration_ms": duration_ms,
    }
