from arq import create_pool
from arq.connections import RedisSettings

from config import settings


redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings)
    return _pool


async def enqueue_ingest(
    pdf_path: str,
    filename: str,
    user_id: int,
    title: str,
    grade: str | None,
    author: str | None,
    telegram_id: int,
):
    pool = await get_pool()
    await pool.enqueue_job(
        "ingest_pdf_task",
        pdf_path,
        filename,
        user_id,
        title,
        grade,
        author,
        telegram_id,
    )