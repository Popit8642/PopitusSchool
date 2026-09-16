from openai import AsyncOpenAI

from config import settings


_chat = AsyncOpenAI(
    api_key="ollama",
    base_url=f"{settings.LLM_BASE_URL}/v1",
)


async def chat_completion(messages: list[dict], temperature: float = 0.7) -> str:
    response = await _chat.chat.completions.create(
        model=settings.LLM_MODEL_CHAT,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content