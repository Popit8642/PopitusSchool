import uuid

from database.database import async_session
from database.models import Document, Chunk
from services.pdf import extract_and_chunk
from services.embeddings import get_embedding


async def ingest_pdf(
    pdf_path: str,
    filename: str,
    user_id: int,
    title: str,
    grade: str | None = None,
    author: str | None = None,
) -> uuid.UUID:
    chunks = extract_and_chunk(pdf_path)
    print(f"[ingest] чанков получено: {len(chunks)}")

    async with async_session() as session:
        doc = Document(
            user_id=user_id,
            title=title,
            grade=grade,
            author=author,
            filename=filename,
        )
        session.add(doc)
        await session.flush()

        for i, ch in enumerate(chunks, 1):
            emb = await get_embedding(ch["content"])
            session.add(
                Chunk(
                    document_id=doc.id,
                    page_number=ch["page"],
                    content=ch["content"],
                    embedding=emb,
                )
            )
            if i % 10 == 0:
                print(f"[ingest] обработано {i}/{len(chunks)}")

        await session.commit()
        print(f"[ingest] готово, doc_id={doc.id}")
        return doc.id