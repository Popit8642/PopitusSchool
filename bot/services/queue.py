import logging
import uuid

from arq import create_pool
from arq.connections import RedisSettings

from config import settings


logger = logging.getLogger(__name__)

redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

QUEUE_NAME = "arq:queue"
IN_PROGRESS_PREFIX = "arq:in-progress:"

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings)
    return _pool


async def _in_progress_count() -> int:
    pool = await get_pool()
    count = 0
    async for _ in pool.scan_iter(match=f"{IN_PROGRESS_PREFIX}*"):
        count += 1
    return count


async def get_queue_size() -> int:
    """Сколько задач ждёт и сколько выполняется прямо сейчас."""
    pool = await get_pool()
    queued = int(await pool.zcard(QUEUE_NAME))
    return queued + await _in_progress_count()


async def get_queue_position() -> int:
    """Позиция новой задачи: всё, что впереди, плюс она сама."""
    return await get_queue_size() + 1


async def get_queued_document_ids() -> set[str]:
    """ID документов, для которых задача уже стоит в очереди."""
    pool = await get_pool()
    jobs = await pool.queued_jobs()
    result: set[str] = set()
    for job in jobs:
        if (
            job.function == "process_document_task"
            and job.args
            and job.args[0] is not None
        ):
            result.add(str(job.args[0]))
    return result


async def enqueue_ingest(document_id: str | uuid.UUID) -> str:
    """Ставит обработку документа в очередь. Возвращает job_id."""
    pool = await get_pool()
    job_id = f"ingest:{document_id}:{uuid.uuid4().hex[:8]}"
    job = await pool.enqueue_job(
        "process_document_task",
        str(document_id),
        _job_id=job_id,
    )
    if job is None:
        raise RuntimeError(f"Задача {job_id} не была поставлена в очередь")
    logger.info("Задача в очереди: job_id=%s document_id=%s", job_id, document_id)
    return job_id


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        logger.debug("Пул Redis закрыт")
