import logging
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select

from config import settings
from database.database import async_session
from database.models import DOC_COMPLETED, DOC_FAILED, DOC_PROCESSING, Chunk, Document
from errors import IngestCancelledError, PdfUnreadableError
from services.embeddings import get_embeddings
from services.pdf import extract_and_chunk


logger = logging.getLogger(__name__)


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
    except Exception as exc:
        logger.exception("[ingest] не удалось разобрать PDF %s", uid)
        await _mark_failed(uid, str(exc))
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
