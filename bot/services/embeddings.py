from openai import AsyncOpenAI

from config import settings


_client = AsyncOpenAI(
    api_key="ollama",
    base_url=f"{settings.EMBEDDING_BASE_URL}/v1",
)


async def get_embedding(text: str) -> list[float]:
    response = await _client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding