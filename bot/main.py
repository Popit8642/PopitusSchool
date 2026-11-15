import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent

from app.admin import admin_router
from app.handlers import router
from app.middlewares import BlockedUserMiddleware
from app.payments import payments_router
from bot_factory import create_bot
from config import settings
from database.database import engine
from database.functions import ensure_admins
from logging_setup import setup_logging
from services.queue import close_pool
from services.rate_limit import close_redis


logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        await ensure_admins(settings.admin_ids)
    except Exception:
        logger.exception("Не удалось применить ADMIN_IDS")
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Запустить бота"),
                BotCommand(command="help", description="Помощь"),
                BotCommand(command="admin_panel", description="Админ-панель"),
            ]
        )
    except Exception:
        logger.exception("Не удалось установить меню команд")
    logger.info("Бот запущен")


async def on_shutdown(bot: Bot) -> None:
    try:
        await bot.session.close()
    except Exception:
        logger.exception("Ошибка при закрытии сессии бота")
    await close_pool()
    await close_redis()
    await engine.dispose()
    logger.info("Бот остановлен")


async def errors_handler(event: ErrorEvent) -> bool:
    exception = event.exception
    logger.error(
        "Необработанная ошибка",
        exc_info=(type(exception), exception, exception.__traceback__),
    )

    update = event.update
    message = update.message
    if message is None and update.callback_query is not None:
        message = update.callback_query.message

    if message is not None:
        try:
            await message.answer("❌ Произошла ошибка, попробуйте позже.")
        except Exception:
            logger.debug("Не удалось сообщить пользователю об ошибке")
    return True


async def main() -> None:
    setup_logging()
    bot = create_bot()
    dp = Dispatcher()

    blocked_middleware = BlockedUserMiddleware()
    dp.message.outer_middleware(blocked_middleware)
    dp.callback_query.outer_middleware(blocked_middleware)
    dp.pre_checkout_query.outer_middleware(blocked_middleware)

    dp.include_router(admin_router)
    dp.include_router(payments_router)
    dp.include_router(router)
    dp.errors.register(errors_handler)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка по сигналу")
    except Exception:
        logger.exception("Критическая ошибка")
