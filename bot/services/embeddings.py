import logging

from openai import AsyncOpenAI

from config import settings
from errors import EmbeddingError


logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    api_key="ollama",
    base_url=settings.EMBEDDING_OPENAI_BASE_URL,
    timeout=120,
    max_retries=1,
)


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    response = await _client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    data = sorted(response.data, key=lambda item: getattr(item, "index", 0))
    return [item.embedding for item in data]


def _validate(vectors: list[list[float]], expected: int) -> list[list[float]]:
    if len(vectors) != expected:
        raise EmbeddingError(
            f"Модель вернула {len(vectors)} векторов вместо {expected}"
        )
    for vector in vectors:
        if len(vector) != settings.EMBEDDING_DIM:
            raise EmbeddingError(
                "Размерность эмбеддинга "
                f"{len(vector)} не совпадает с EMBEDDING_DIM={settings.EMBEDDING_DIM}"
            )
    return vectors


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Считает эмбеддинги батчем, при сбое — по одному тексту."""
    if not texts:
        return []

    try:
        vectors = await _embed_batch(texts)
        return _validate(vectors, len(texts))
    except EmbeddingError:
        raise
    except Exception as exc:
        logger.warning(
            "Батчевый запрос эмбеддингов не удался (%s), пробую по одному", exc
        )

    vectors: list[list[float]] = []
    for text in texts:
        try:
            vectors.extend(await _embed_batch([text]))
        except Exception as exc:
            raise EmbeddingError(f"Не удалось получить эмбеддинг: {exc}") from exc
    return _validate(vectors, len(texts))


async def get_embedding(text: str) -> list[float]:
    vectors = await get_embeddings([text])
    return vectors[0]
