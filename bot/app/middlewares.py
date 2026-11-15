import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, PreCheckoutQuery

import database.functions as db


logger = logging.getLogger(__name__)

BLOCKED_ALERT = "Вы заблокированы и не можете пользоваться ботом."


class BlockedUserMiddleware(BaseMiddleware):
    """Молча пропускает апдейты заблокированных пользователей."""

    async def __call__(self, handler, event, data):
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        try:
            blocked = await db.is_user_blocked(from_user.id)
        except Exception:
            # Ошибка БД не должна ломать обработку всех сообщений.
            logger.exception("Не удалось проверить блокировку %s", from_user.id)
            return await handler(event, data)

        if not blocked:
            return await handler(event, data)

        logger.info("Апдейт от заблокированного пользователя %s проигнорирован", from_user.id)
        if isinstance(event, PreCheckoutQuery):
            try:
                await event.answer(ok=False, error_message=BLOCKED_ALERT)
            except Exception:
                logger.debug("Не удалось отклонить платёж заблокированного пользователя")
        elif isinstance(event, CallbackQuery):
            try:
                await event.answer(BLOCKED_ALERT, show_alert=True)
            except Exception:
                logger.debug("Не удалось ответить на callback заблокированного пользователя")
        return None
