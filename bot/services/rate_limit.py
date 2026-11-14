import logging

import redis.asyncio as redis

from config import settings


logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def acquire_cooldown(key: str, seconds: int) -> int | None:
    """Пытается занять cooldown-ключ.

    Возвращает None, если действие разрешено, иначе — сколько секунд ждать.
    """
    client = await get_redis()
    acquired = await client.set(key, "1", ex=seconds, nx=True)
    if acquired:
        return None
    ttl = await client.ttl(key)
    return max(int(ttl), 1)


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.debug("Redis-клиент закрыт")
