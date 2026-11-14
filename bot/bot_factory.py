from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from config import settings


def create_bot() -> Bot:
    """Создаёт бота с прокси, если он задан."""
    if settings.PROXY_URL:
        session = AiohttpSession(proxy=settings.PROXY_URL)
        return Bot(token=settings.BOT_TOKEN, session=session)
    return Bot(token=settings.BOT_TOKEN)
