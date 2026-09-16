from services.retriever import search_chunks
from services.llm_client import chat_completion


SYSTEM_PROMPT = """Ты — опытный методист и учитель. Твоя задача — составить интересный урок по фрагментам учебника.

Структура урока:
1. **Цель урока** — что ученик поймёт после изучения темы
2. **Объяснение** — простыми словами, с опорой на текст
3. **Аналогия из жизни** — чтобы было понятно
4. **Пример** — конкретный пример по теме
5. **Вопрос для проверки** — 2-3 вопроса

Пиши на русском, живым языком, без воды. Опирайся ТОЛЬКО на предоставленный контекст."""


async def generate_lesson(
    topic: str,
    user_id: int,
    document_id: str | None = None,
    top_k: int = 5,
) -> dict:
    chunks = await search_chunks(
        query=topic,
        user_id=user_id,
        document_id=document_id,
        top_k=top_k,
    )

    if not chunks:
        return {
            "topic": topic,
            "lesson": "Не нашёл информации по этой теме в загруженных материалах.",
            "sources": [],
        }

    context = "\n\n---\n\n".join(
        f"[Стр. {c['page']}]\n{c['content']}" for c in chunks
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Тема урока: {topic}\n\nФрагменты учебника:\n\n{context}",
        },
    ]

    lesson = await chat_completion(messages, temperature=0.7)

    return {
        "topic": topic,
        "lesson": lesson,
        "sources": sorted({c["page"] for c in chunks}),
    }