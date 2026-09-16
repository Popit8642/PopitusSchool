import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from app.handlers import router
from config import settings


def create_bot() -> Bot:
    if settings.PROXY_URL:
        session = AiohttpSession(proxy=settings.PROXY_URL)
        return Bot(token=settings.BOT_TOKEN, session=session)
    return Bot(token=settings.BOT_TOKEN)


async def main():
    bot = create_bot()
    dp = Dispatcher()
    dp.include_router(router)
    print("[ON] | Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[OFF] | Бот остановлен")
    except Exception as e:
        print(f"[ERR] | {e}")