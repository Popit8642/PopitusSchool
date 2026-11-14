import asyncio
import logging

from openai import AsyncOpenAI

from config import settings
from errors import LLMEmptyResponseError


logger = logging.getLogger(__name__)

_chat = AsyncOpenAI(
    api_key="ollama",
    base_url=settings.LLM_OPENAI_BASE_URL,
    timeout=settings.LLM_TIMEOUT_SECONDS,
    max_retries=settings.LLM_MAX_RETRIES,
)

# Ollama эффективно обрабатывает один запрос за раз: параллельные вызовы
# выбивают видеопамять и замедляют всех. Ограничиваем конкурентность.
_semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)


async def chat_completion(
    messages: list[dict],
    temperature: float = 0.5,
    model: str | None = None,
    timeout: float | None = None,
) -> str:
    model_name = model or settings.LLM_MODEL_CHAT
    logger.debug(
        "LLM request: model=%s messages=%d", model_name, len(messages)
    )
    async with _semaphore:
        response = await _chat.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=timeout or settings.LLM_TIMEOUT_SECONDS,
        )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise LLMEmptyResponseError("Модель вернула пустой ответ")
    return content.strip()
