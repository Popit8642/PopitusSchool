import asyncio

from arq import func
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
    chat_id: int,        # <-- это только для воркера
    message_id: int,     # <-- это только для воркера
):
    bot: Bot = ctx["bot"]
    
    # Редактируем сообщение, что задача в работе
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ Начинаю обрабатывать «{title}»..."
        )
    except Exception:
        pass

    try:
        # ВОТ ЗДЕСЬ передаем ТОЛЬКО те аргументы, которые ждет ingest_pdf
        doc_id = await ingest_pdf(
            pdf_path=pdf_path,
            filename=filename,
            user_id=user_id,
            title=title,
            grade=grade,
            author=author,
        )
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"✅ Учебник «{title}» успешно добавлен!"
        )
        print(f"[worker] ingest OK: {doc_id}")
    except Exception as e:
        print(f"[worker] ingest FAIL: {e}")
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"❌ Не удалось обработать учебник: {e}"
        )


async def startup(ctx):
    """Вызывается один раз при старте воркера."""
    session = AiohttpSession(proxy=settings.PROXY_URL) if settings.PROXY_URL else None
    ctx["bot"] = Bot(token=settings.BOT_TOKEN, session=session) if session else Bot(token=settings.BOT_TOKEN)
    print("[worker] запущен")


async def shutdown(ctx):
    await ctx["bot"].session.close()
    print("[worker] остановлен")

async def test_error(ctx):
    raise ValueError("Тестовая ошибка")

async def test_timeout(ctx):
    import asyncio
    await asyncio.sleep(20)

class WorkerSettings:
    functions = [ingest_pdf_task, func(test_error, max_tries=3), func(test_timeout, timeout=5)]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1