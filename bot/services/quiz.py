import asyncio
import json
import logging
import re
import time
from typing import Any

from openai import APITimeoutError

from config import settings
from errors import LLMTimeoutError
from services.llm_client import chat_completion
from services.retriever import search_chunks


logger = logging.getLogger(__name__)

QUIZ_SYSTEM_PROMPT = """Ты — составитель учебных тестов. Составь тест строго по предоставленному тексту учебника.

ФОРМАТ ОТВЕТА — ТОЛЬКО валидный JSON, без пояснений и без Markdown:
{
  "questions": [
    {
      "question": "текст вопроса",
      "options": ["вариант 1", "вариант 2", "вариант 3", "вариант 4"],
      "correct": 1
    }
  ]
}

ПРАВИЛА:
- В каждом вопросе ровно 4 варианта ответа.
- "correct" — номер правильного варианта от 1 до 4.
- Опирайся только на факты из текста, ничего не выдумывай.
- Вопросы не должны повторяться.
- Не добавляй текст вне JSON.
"""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d+")
_OPTION_PREFIX_RE = re.compile(
    r"^\s*(?:[А-ГA-Dа-гa-d]|\d{1,2})\s*[\).:\-]\s*", re.IGNORECASE
)
_LETTERS = {"a": 0, "b": 1, "c": 2, "d": 3, "а": 0, "б": 1, "в": 2, "г": 3}


def _extract_json(raw: str) -> Any:
    text = (raw or "").strip()
    text = _CODE_FENCE_RE.sub("", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("В ответе модели нет JSON")


def _clean_options(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        option = _OPTION_PREFIX_RE.sub("", str(item).strip()).strip()
        if option and option.casefold() not in {o.casefold() for o in result}:
            result.append(option)
    return result[:4]


def _correct_index(item: dict, options: list[str]) -> int | None:
    value = item.get("correct")
    if value is None:
        value = item.get("correct_index")
    if value is None:
        value = item.get("answer")
    if value is None:
        value = item.get("correct_answer")
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = int(value)
        if 1 <= number <= len(options):
            return number - 1
        if number == 0:
            return 0
        return None

    text = str(value).strip()
    if not text:
        return None

    lowered = text.casefold()

    # Сначала ищем точное совпадение с текстом варианта: модель часто
    # возвращает сам ответ, а не его номер.
    for index, option in enumerate(options):
        if option.casefold() == lowered:
            return index

    if text.isdigit():
        number = int(text)
        if 1 <= number <= len(options):
            return number - 1
        if number == 0:
            return 0
        return None

    if len(lowered) == 1 and lowered in _LETTERS:
        index = _LETTERS[lowered]
        return index if index < len(options) else None

    for index, option in enumerate(options):
        option_lower = option.casefold()
        if len(lowered) >= 3 and (
            lowered in option_lower or option_lower in lowered
        ):
            return index

    match = _DIGIT_RE.search(text)
    if match:
        number = int(match.group())
        if 1 <= number <= len(options):
            return number - 1
    return None


def _normalize_questions(raw: Any, limit: int) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        items = raw.get("questions") or raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(
            item.get("question") or item.get("q") or item.get("text") or ""
        ).strip()
        options = _clean_options(
            item.get("options")
            or item.get("answers")
            or item.get("variants")
            or item.get("choices")
        )
        if not question or len(options) < 2:
            continue
        index = _correct_index(item, options)
        if index is None:
            logger.warning("Тест: пропущен вопрос без корректного ответа: %r", question)
            continue
        result.append(
            {
                "question": question,
                "options": options,
                "correct_index": index,
            }
        )
        if len(result) >= limit:
            break
    return result


async def generate_quiz(
    topic: str,
    user_id: int,
    document_id: str | None,
    count: int,
    document_title: str | None = None,
    model_tag: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    logger.info(
        "Генерация теста: user=%s document=%s model=%s count=%s topic=%r",
        user_id,
        document_id,
        model_tag or settings.LLM_MODEL_CHAT,
        count,
        topic,
    )

    try:
        chunks = await search_chunks(
            query=topic,
            user_id=user_id,
            document_id=document_id,
            top_k=max(settings.RAG_TOP_K, 8),
        )
    except Exception:
        logger.exception("Ошибка поиска фрагментов для теста")
        return {
            "topic": topic,
            "questions": [],
            "sources": [],
            "error": "retrieval",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    if not chunks:
        return {
            "topic": topic,
            "questions": [],
            "sources": [],
            "error": "not_found",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    context = "\n\n---\n\n".join(
        f"[Стр. {chunk['page']}]\n{chunk['content']}" for chunk in chunks
    )
    messages = [
        {"role": "system", "content": QUIZ_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Тема: {topic}\n"
                f"Учебник: {document_title or 'не указан'}\n"
                f"Сколько вопросов: {count}\n\n"
                f"Текст учебника:\n\n{context}"
            ),
        },
    ]

    try:
        raw = await asyncio.wait_for(
            chat_completion(messages, temperature=0.3, model=model_tag),
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError, LLMTimeoutError, APITimeoutError):
        logger.warning("Таймаут генерации теста: topic=%r", topic)
        return {
            "topic": topic,
            "questions": [],
            "sources": [],
            "error": "timeout",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception:
        logger.exception("Ошибка генерации теста через LLM")
        return {
            "topic": topic,
            "questions": [],
            "sources": [],
            "error": "llm",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        data = _extract_json(raw)
        questions = _normalize_questions(data, count)
    except (ValueError, json.JSONDecodeError):
        logger.warning("Тест: не удалось разобрать JSON: %r", raw[:500])
        questions = []

    if not questions:
        return {
            "topic": topic,
            "questions": [],
            "sources": [],
            "error": "format",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    sources = sorted({chunk["page"] for chunk in chunks})
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Тест готов: topic=%r, вопросов=%s, %s мс",
        topic,
        len(questions),
        duration_ms,
    )
    return {
        "topic": topic,
        "questions": questions,
        "sources": sources,
        "error": None,
        "duration_ms": duration_ms,
        "chunks_count": len(chunks),
    }


def format_quiz(
    topic: str,
    questions: list[dict[str, Any]],
    document_title: str | None = None,
) -> str:
    lines = [f"📝 Тест по теме «{topic}»"]
    if document_title:
        lines.append(f"Учебник: «{document_title}»")
    lines.append("")

    for number, item in enumerate(questions, start=1):
        lines.append(f"{number}. {item['question']}")
        for index, option in enumerate(item["options"], start=1):
            lines.append(f"   {index}) {option}")
        lines.append("")

    lines.append("Выберите ответы, затем нажмите «Показать ответы».")
    return "\n".join(lines).strip()


def format_answers(questions: list[dict[str, Any]]) -> str:
    lines = ["✅ Правильные ответы", ""]
    for number, item in enumerate(questions, start=1):
        options = item.get("options") or []
        index = item.get("correct_index", 0)
        if 0 <= index < len(options):
            correct = f"{index + 1}) {options[index]}"
        else:
            correct = "—"
        lines.append(f"{number}. {correct}")
    return "\n".join(lines)


def split_message(text: str, limit: int = 3800) -> list[str]:
    """Делит длинный текст по абзацам, не разрывая вопросы без нужды."""
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(block) <= limit:
            current = block
            continue
        for start in range(0, len(block), limit):
            parts.append(block[start : start + limit])
    if current:
        parts.append(current)
    return parts or [text]
