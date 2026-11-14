import asyncio
import logging
import time
from typing import Any

from openai import APITimeoutError

from config import settings
from errors import LLMTimeoutError
from services.llm_client import chat_completion
from services.retriever import search_chunks


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — опытный методист и автор учебных материалов. Составляешь конспект урока для учителя, который будет вести занятие по этому тексту.

ТВОЯ АУДИТОРИЯ — УЧИТЕЛЬ, а не ученик. Пиши в стиле «на уроке разберём», «ученики узнают», «обратим внимание».
НЕ обращайся на «ты» к читателю. НЕ пиши «представь, что ты...», «ты поймёшь».
Используй формы «мы» (учитель + класс) или безличные: «важно отметить», «стоит разобрать».

СТРУКТУРА УРОКА (строго по пунктам, без отклонений):

**Тема:** [коротко]

**Цель урока:**
[1–2 предложения: что ученики должны понять или уметь после занятия]

**Ключевые понятия:**
[3–5 терминов или имён, которые нужно запомнить, с краткими пояснениями]

**Ход урока:**
1. **Вступление** (2–3 минуты) — как заинтересовать класс, с чего начать.
2. **Основная часть** — объяснение материала по фрагментам, логически связанное. Без воды, только суть.
3. **Закрепление** — один конкретный пример или мини-задание для класса.
4. **Итог** — что обязательно должно остаться в тетрадях.

**Вопросы для проверки:**
[3 вопроса разного уровня: на знание, на понимание, на применение]

**Домашнее задание (опционально):**
[одно задание, если оно логично вытекает из темы]

ТРЕБОВАНИЯ:
- Опирайся ТОЛЬКО на предоставленный текст. Не выдумывай имена, даты, события.
- Если в тексте нет ответа на какую-то часть — пропусти её, не сочиняй.
- Пиши на русском, литературным, но живым языком. Без канцелярита.
- Объём — не больше 800 слов. Лучше короче и по делу.
- Для выделения используй только **жирный** и обычные списки через дефис. Не используй таблицы, HTML и сложную разметку.
- В конце каждого смыслового блока можешь указать [стр. X], если это уместно.
"""


async def generate_lesson(
    topic: str,
    user_id: int,
    document_id: str | None = None,
    top_k: int | None = None,
    model_tag: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    logger.info(
        "Генерация урока: user=%s document=%s model=%s topic=%r",
        user_id,
        document_id,
        model_tag or settings.LLM_MODEL_CHAT,
        topic,
    )

    try:
        chunks = await search_chunks(
            query=topic,
            user_id=user_id,
            document_id=document_id,
            top_k=top_k,
        )
    except Exception:
        logger.exception("Ошибка поиска фрагментов для урока")
        return {
            "topic": topic,
            "lesson": None,
            "sources": [],
            "error": "retrieval",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    if not chunks:
        return {
            "topic": topic,
            "lesson": None,
            "sources": [],
            "error": "not_found",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    context = "\n\n---\n\n".join(
        f"[Стр. {chunk['page']}]\n{chunk['content']}" for chunk in chunks
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Тема урока: {topic}\n\nФрагменты учебника:\n\n{context}",
        },
    ]

    try:
        lesson = await asyncio.wait_for(
            chat_completion(messages, temperature=0.7, model=model_tag),
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError, LLMTimeoutError, APITimeoutError):
        logger.warning("Таймаут генерации урока: topic=%r", topic)
        return {
            "topic": topic,
            "lesson": None,
            "sources": [],
            "error": "timeout",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception:
        logger.exception("Ошибка генерации урока через LLM")
        return {
            "topic": topic,
            "lesson": None,
            "sources": [],
            "error": "llm",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    sources = sorted({chunk["page"] for chunk in chunks})
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Урок готов: topic=%r, источники=%s, %s мс",
        topic,
        sources,
        duration_ms,
    )
    return {
        "topic": topic,
        "lesson": lesson,
        "sources": sources,
        "error": None,
        "duration_ms": duration_ms,
        "chunks_count": len(chunks),
    }
