import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import app.keyboards as kb
import database.functions as db
from config import settings
from database.models import TIER_PRO


logger = logging.getLogger(__name__)
payments_router = Router()

PRO_TITLE = "Подписка PopitusGPT1.5v-Pro"
PRO_DESCRIPTION = (
    "Доступ к более мощной нейросети для генерации качественных уроков"
)
PRO_PAYLOAD_PREFIX = "pro"
PRO_ACTIVE_TEXT = "У вас активен тариф Pro. Спасибо за поддержку!"


def _payload(telegram_id: int) -> str:
    return f"{PRO_PAYLOAD_PREFIX}:{telegram_id}"


async def _send_invoice(bot, chat_id: int, telegram_id: int) -> None:
    """Отправляет счёт на оплату подписки через Telegram Stars."""
    await bot.send_invoice(
        chat_id=chat_id,
        title=PRO_TITLE,
        description=PRO_DESCRIPTION,
        payload=_payload(telegram_id),
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(label=PRO_TITLE, amount=settings.PRO_PRICE_STARS)
        ],
    )


async def _handle_buy(bot, chat_id: int, from_user) -> None:
    user = await db.get_or_create_user(
        telegram_id=from_user.id, username=from_user.username
    )
    if user.subscription_tier == TIER_PRO:
        await bot.send_message(
            chat_id=chat_id,
            text=PRO_ACTIVE_TEXT,
            reply_markup=kb.main_menu(TIER_PRO),
        )
        return
    await _send_invoice(bot, chat_id, user.telegram_id)


@payments_router.message(F.text == "💎 Купить Pro")
async def buy_pro_message(message: Message, state: FSMContext):
    await state.clear()
    await _handle_buy(message.bot, message.chat.id, message.from_user)


@payments_router.callback_query(F.data == "buy_pro")
async def buy_pro_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _handle_buy(
        callback.bot, callback.message.chat.id, callback.from_user
    )


@payments_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    payload = query.invoice_payload or ""
    if not payload.startswith(f"{PRO_PAYLOAD_PREFIX}:"):
        logger.warning("Неизвестный payload платежа: %r", payload)
        await query.answer(
            ok=False, error_message="Неизвестный товар. Попробуйте заново."
        )
        return
    await query.answer(ok=True)


@payments_router.message(F.successful_payment)
async def successful_payment(message: Message, state: FSMContext):
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return

    payload = payment.invoice_payload or ""
    if not payload.startswith(f"{PRO_PAYLOAD_PREFIX}:"):
        logger.warning(
            "Оплата с неизвестным payload: user=%s payload=%r",
            message.from_user.id,
            payload,
        )
        return

    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )

    pro_models = [
        model
        for model in await db.get_available_models(TIER_PRO)
        if model.required_tier == TIER_PRO
    ]
    pro_name = pro_models[0].name if pro_models else "PopitusGPT1.5v-Pro"

    if user.subscription_tier == TIER_PRO:
        logger.warning(
            "Повторная оплата Pro: user=%s charge=%s amount=%s",
            user.telegram_id,
            payment.telegram_payment_charge_id,
            payment.total_amount,
        )
        await message.answer(
            "Оплата получена, но подписка Pro уже активна.\n"
            "Если это произошло по ошибке, напишите в «📞 Контакты».",
            reply_markup=kb.main_menu(TIER_PRO),
        )
    else:
        await db.set_subscription_tier(user.telegram_id, TIER_PRO)
        logger.info(
            "Оплачена подписка Pro: user=%s amount=%s charge=%s",
            user.telegram_id,
            payment.total_amount,
            payment.telegram_payment_charge_id,
        )
        await message.answer(
            "🎉 Спасибо за покупку! Вам активирована подписка Pro. "
            f"Теперь вам доступна нейросеть {pro_name}. "
            "Выбрать её можно в меню «⚙️ Выбрать нейросеть».",
            reply_markup=kb.main_menu(TIER_PRO),
        )

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "без username"
    )
    try:
        admin_ids = set(settings.admin_ids) | set(
            await db.get_admin_telegram_ids()
        )
    except Exception:
        logger.exception("Не удалось получить список админов для уведомления")
        admin_ids = set(settings.admin_ids)

    for admin_id in admin_ids:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=(
                    "💰 Покупка подписки Pro\n"
                    f"Пользователь: {username} (ID {user.telegram_id})\n"
                    f"Сумма: {payment.total_amount} Stars"
                ),
            )
        except TelegramAPIError:
            logger.warning(
                "Не удалось уведомить админа %s о покупке", admin_id
            )
