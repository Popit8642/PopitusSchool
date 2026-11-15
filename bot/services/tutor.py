import asyncio
import logging
import time
from typing import Any

from openai import APITimeoutError

from config import settings
from errors import LLMTimeoutError
from services.llm_client import chat_completion


logger = logging.getLogger(__name__)

TUTOR_SYSTEM_PROMPT = """Ты — школьный репетитор. Отвечай на вопрос ученика, опираясь ТОЛЬКО на текст урока, приведённый ниже.

ПРАВИЛА:
- Если ответа в уроке нет, прямо скажи: «Это за рамками урока».
- Не выдумывай факты, имена, даты и определения.
- Отвечай на русском языке, кратко и понятно, как объяснял бы учитель.
- Можно ссылаться на формулировки урока, но не пересказывай весь урок.
"""

MAX_LESSON_CHARS = 14000
HISTORY_LIMIT = 3


def _lesson_block(context: dict[str, Any]) -> str:
    content = (context.get("content") or "").strip()
    if len(content) > MAX_LESSON_CHARS:
        content = content[:MAX_LESSON_CHARS] + "\n\n[текст урока сокращён]"
    topic = context.get("topic") or "без темы"
    title = context.get("document_title") or "учебник не указан"
    return (
        f"Тема урока: {topic}\n"
        f"Учебник: {title}\n\n"
        f"Текст урока:\n\n{content}"
    )


async def answer_question(
    context: dict[str, Any],
    question: str,
    model_tag: str | None = None,
) -> dict[str, Any]:
    """Отвечает на вопрос ученика по сохранённому контексту урока."""
    started = time.monotonic()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": TUTOR_SYSTEM_PROMPT},
        {"role": "user", "content": _lesson_block(context)},
    ]

    history = context.get("history") or []
    for pair in history[-HISTORY_LIMIT:]:
        asked = (pair.get("q") or "").strip()
        answered = (pair.get("a") or "").strip()
        if asked:
            messages.append({"role": "user", "content": asked})
        if answered:
            messages.append({"role": "assistant", "content": answered})

    messages.append({"role": "user", "content": question})

    try:
        answer = await asyncio.wait_for(
            chat_completion(messages, temperature=0.3, model=model_tag),
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError, LLMTimeoutError, APITimeoutError):
        logger.warning("Таймаут ответа репетитора: %r", question[:120])
        return {"answer": None, "error": "timeout"}
    except Exception:
        logger.exception("Ошибка ответа репетитора через LLM")
        return {"answer": None, "error": "llm"}

    logger.info(
        "Ответ репетитора готов за %s мс", int((time.monotonic() - started) * 1000)
    )
    return {"answer": answer, "error": None}
