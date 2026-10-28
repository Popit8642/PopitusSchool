import uuid
from sqlalchemy import select  # <--- Убедись, что эта строка есть в импортах вверху файла!
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
    async with async_session() as session:
        stmt = select(Document).where(
            Document.user_id == user_id,
            Document.filename == filename
        )
        existing_doc = (await session.execute(stmt)).scalar_one_or_none()

        # Если документ есть и он ПОЛНОСТЬЮ обработан — пропускаем
        if existing_doc and existing_doc.is_completed:
            print(f"[ingest] Учебник '{filename}' уже полностью обработан. Пропускаем.")
            return existing_doc.id

        # Если документ есть, но НЕ доделан (упал воркер) — удаляем его старые огрызки
        if existing_doc and not existing_doc.is_completed:
            print(f"[ingest] Найден недоделанный учебник '{filename}'. Удаляем старые чанки и перезапускаем.")
            await session.delete(existing_doc)  # Каскадно удалит все его чанки
            await session.commit()

    # 2. Парсим PDF и создаем чанки (твой старый код)
    chunks = extract_and_chunk(pdf_path)
    print(f"[ingest] чанков получено: {len(chunks)}")

    async with async_session() as session:
        doc = Document(
            user_id=user_id,
            title=title,
            grade=grade,
            author=author,
            filename=filename,
            is_completed=False,  # <-- При создании ставим False
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

        # ФИНАЛ: когда всё сохранили, ставим True
        doc.is_completed = True
        await session.commit()

        print(f"[ingest] готово, doc_id={doc.id}")
        return doc.id