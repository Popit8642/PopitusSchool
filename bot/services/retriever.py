from sqlalchemy import select

from database.database import async_session
from database.models import Chunk, Document
from services.embeddings import get_embedding


async def search_chunks(
    query: str,
    user_id: int,
    document_id: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    query_emb = await get_embedding(query)

    async with async_session() as session:
        stmt = (
            select(Chunk)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.user_id == user_id)
            .order_by(Chunk.embedding.cosine_distance(query_emb))
            .limit(top_k)
        )
        if document_id:
            stmt = stmt.where(Chunk.document_id == document_id)

        result = await session.execute(stmt)
        chunks = result.scalars().all()

    return [
        {"id": c.id, "page": c.page_number, "content": c.content}
        for c in chunks
    ]