import logging
import uuid
from typing import Any

from sqlalchemy import select

from config import settings
from database.database import async_session
from database.models import Chunk, Document
from services.embeddings import get_embedding


logger = logging.getLogger(__name__)


def _as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def search_chunks(
    query: str,
    user_id: int,
    document_id: str | uuid.UUID | None = None,
    top_k: int | None = None,
    max_distance: float | None = None,
) -> list[dict[str, Any]]:
    """Ищет релевантные фрагменты только по завершённым учебникам."""
    query_emb = await get_embedding(query)
    top_k = top_k or settings.RAG_TOP_K
    max_distance = (
        settings.RAG_MAX_DISTANCE if max_distance is None else max_distance
    )

    distance = Chunk.embedding.cosine_distance(query_emb)
    stmt = (
        select(
            Chunk.id,
            Chunk.page_number,
            Chunk.content,
            distance.label("distance"),
        )
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Document.user_id == user_id,
            Document.is_completed.is_(True),
            distance <= max_distance,
        )
        .order_by(distance)
        .limit(top_k)
    )

    if document_id is not None:
        uid = _as_uuid(document_id)
        if uid is None:
            return []
        stmt = stmt.where(Chunk.document_id == uid)

    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    result = [
        {
            "id": row.id,
            "page": row.page_number,
            "content": row.content,
            "distance": float(row.distance),
        }
        for row in rows
    ]
    logger.info(
        "RAG: найдено %s фрагментов (top_k=%s, max_distance=%s)",
        len(result),
        top_k,
        max_distance,
    )
    return result
