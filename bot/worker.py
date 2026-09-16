import asyncio

from arq.connections import RedisSettings
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from config import settings
from services.ingest import ingest_pdf
from services.queue import redis_settings


async def ingest_pdf_task(
    ctx,
    pdf_path: str,
    filename: str,
    user_id: int,
    title: str,
    grade: str | None,
    author: str | None,
    telegram_id: int,
):
    """Задача, которую arq забирает из очереди."""
    bot: Bot = ctx["bot"]

    try:
        doc_id = await ingest_pdf(
            pdf_path=pdf_path,
            filename=filename,
            user_id=user_id,
            title=title,
            grade=grade,
            author=author,
        )
        await bot.send_message(
            telegram_id,
            f"✅ Учебник «{title}» успешно добавлен!\n"
            f"Страниц обработано, можно генерировать уроки.",
        )
        print(f"[worker] ingest OK: {doc_id}")
    except Exception as e:
        print(f"[worker] ingest FAIL: {e}")
        await bot.send_message(
            telegram_id,
            f"❌ Не удалось обработать учебник: {e}",
        )


async def startup(ctx):
    """Вызывается один раз при старте воркера."""
    session = AiohttpSession(proxy=settings.PROXY_URL) if settings.PROXY_URL else None
    ctx["bot"] = Bot(token=settings.BOT_TOKEN, session=session) if session else Bot(token=settings.BOT_TOKEN)
    print("[worker] запущен")


async def shutdown(ctx):
    await ctx["bot"].session.close()
    print("[worker] остановлен")


class WorkerSettings:
    functions = [ingest_pdf_task]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown