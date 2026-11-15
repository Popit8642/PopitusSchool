import json
import logging
import uuid
from typing import Any

from config import settings
from services.rate_limit import get_redis


logger = logging.getLogger(__name__)

QUIZ_PREFIX = "popitus:quiz:"
TUTOR_PREFIX = "popitus:tutor:"


def new_token() -> str:
    """Короткий случайный идентификатор для callback_data."""
    return uuid.uuid4().hex


async def _save(key: str, payload: dict[str, Any], ttl: int) -> None:
    client = await get_redis()
    await client.set(
        key, json.dumps(payload, ensure_ascii=False), ex=ttl
    )


async def _load(key: str) -> dict[str, Any] | None:
    client = await get_redis()
    raw = await client.get(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Некорректный JSON в Redis по ключу %s", key)
        return None
    return data if isinstance(data, dict) else None


async def _delete(key: str) -> None:
    client = await get_redis()
    await client.delete(key)


# ─── Тесты ───────────────────────────────────────────────────────────────────
async def save_quiz(token: str, payload: dict[str, Any]) -> None:
    await _save(
        f"{QUIZ_PREFIX}{token}", payload, settings.QUIZ_CONTEXT_TTL_SECONDS
    )


async def load_quiz(token: str) -> dict[str, Any] | None:
    return await _load(f"{QUIZ_PREFIX}{token}")


async def delete_quiz(token: str) -> None:
    await _delete(f"{QUIZ_PREFIX}{token}")


# ─── Репетитор ───────────────────────────────────────────────────────────────
def _tutor_key(telegram_id: int) -> str:
    return f"{TUTOR_PREFIX}{telegram_id}"


async def save_tutor(telegram_id: int, payload: dict[str, Any]) -> None:
    await _save(
        _tutor_key(telegram_id),
        payload,
        settings.TUTOR_CONTEXT_TTL_SECONDS,
    )


async def load_tutor(telegram_id: int) -> dict[str, Any] | None:
    return await _load(_tutor_key(telegram_id))


async def delete_tutor(telegram_id: int) -> None:
    await _delete(_tutor_key(telegram_id))
