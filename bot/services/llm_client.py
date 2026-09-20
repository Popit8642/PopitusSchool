from openai import AsyncOpenAI

from config import settings


_chat = AsyncOpenAI(
    api_key="ollama",
    base_url=settings.LLM_BASE_URL,
)


async def chat_completion(messages: list[dict], temperature: float = 0.5) -> str:
    response = await _chat.chat.completions.create(
        model=settings.LLM_MODEL_CHAT,
        messages=messages,
        temperature=temperature,
        max_tokens=3000,
    )
    return response.choices[0].message.content