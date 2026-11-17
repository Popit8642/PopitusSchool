import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import app.keyboards as kb
import database.functions as db
from config import settings
from database.models import AIModel, TIER_FREE, TIER_PRO
from services.queue import get_queue_size
from services.rate_limit import acquire_cooldown


logger = logging.getLogger(__name__)
admin_router = Router()


class BroadcastState(StatesGroup):
    waiting_for_broadcast_text = State()
    confirming_broadcast = State()


class SubscriptionState(StatesGroup):
    waiting_identifier = State()


class BlockState(StatesGroup):
    waiting_identifier = State()


def admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="🧠 Нейросети", callback_data="models")],
            [InlineKeyboardButton(text="👑 Подписки", callback_data="subscriptions")],
            [InlineKeyboardButton(text="🚫 Блокировки", callback_data="blocks")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_panel")],
        ]
    )


def cancel_broadcast_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
        ]
    )


def confirm_broadcast_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
        ]
    )


def _tier_badge(tier: str) -> str:
    return "Pro" if tier == TIER_PRO else "Free"


def models_text(models: list[AIModel]) -> str:
    if not models:
        return "🧠 <b>Нейросети</b>\n\nВ таблице пока нет моделей."

    lines = ["🧠 <b>Нейросети</b>", ""]
    for model in models:
        state = "✅ включена" if model.is_active else "⏸ выключена"
        default = " · ⭐ по умолчанию" if model.is_default else ""
        lines.append(
            f"• <b>{escape(model.name)}</b> — {state}{default}\n"
            f"  <code>{escape(model.ollama_tag)}</code> · {_tier_badge(model.required_tier)}"
        )
    lines += [
        "",
        "⭐ — назначить модель по умолчанию.",
        "Модель по умолчанию используется, если пользователь не выбрал свою.",
    ]
    return "\n".join(lines)


def models_panel(models: list[AIModel]) -> InlineKeyboardMarkup:
    rows = []
    for model in models:
        star = "⭐" if model.is_default else "☆"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{star} {model.name} · {_tier_badge(model.required_tier)}",
                    callback_data=f"model_default:{model.id}",
                )
            ]
        )
        toggle_text = "⏸ Выключить" if model.is_active else "▶️ Включить"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{toggle_text} {model.name}",
                    callback_data=f"model_toggle:{model.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscriptions_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👑 Выдать Pro", callback_data="sub_grant")],
            [InlineKeyboardButton(text="🚫 Забрать Pro", callback_data="sub_revoke")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_panel")],
        ]
    )


def blocks_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="block_user")],
            [InlineKeyboardButton(text="✅ Разблокировать", callback_data="unblock_user")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_panel")],
        ]
    )


def cancel_state_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="back_and_clear_state")]
        ]
    )


async def _is_plain_text(message: Message) -> bool:
    text = message.text or ""
    return bool(text) and not text.startswith("/") and text not in kb.MENU_TEXTS


async def _safe_edit(
    message: Message, text: str, reply_markup=None, parse_mode: str | None = None
) -> None:
    try:
        await message.edit_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode
        )
    except TelegramBadRequest:
        logger.debug("Сообщение админки не изменилось")


@admin_router.message(Command("admin_panel"))
async def owner_panel(message: Message, state: FSMContext):
    await state.clear()
    if not await db.is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к этой команде.")
        return
    await message.answer(
        f"Добро пожаловать, @{message.from_user.username or message.from_user.id}.\n"
        "Это панель управления ботом.",
        reply_markup=admin_panel(),
    )


@admin_router.callback_query(F.data == "back_panel")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await _safe_edit(
        callback.message,
        f"Добро пожаловать, @{callback.from_user.username or callback.from_user.id}.\n"
        "Это панель управления ботом.",
        admin_panel(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "back_and_clear_state")
async def back_and_clear(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await _safe_edit(callback.message, "Действие отменено.", back_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "stats")
async def process_stats(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    stats = await db.get_admin_stats()
    try:
        queue_size = await get_queue_size()
    except Exception:
        logger.exception("Не удалось получить размер очереди")
        queue_size = None

    queue_text = str(queue_size) if queue_size is not None else "н/д"
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"👑 Pro-подписок: <b>{stats['pro_users']}</b>\n"
        f"🚫 Заблокировано: <b>{stats['blocked_users']}</b>\n"
        f"📬 Активных для рассылки: <b>{stats['active_users']}</b>\n\n"
        f"📚 Учебников: <b>{stats['documents']}</b>\n"
        f"✅ Готовых учебников: <b>{stats['completed_documents']}</b>\n"
        f"🧩 Фрагментов: <b>{stats['chunks']}</b>\n"
        f"🧠 Сгенерированных уроков: <b>{stats['lessons']}</b>\n"
        f"⏳ Задач в очереди: <b>{queue_text}</b>"
    )
    await _safe_edit(callback.message, text, back_menu(), parse_mode="HTML")
    await callback.answer()


@admin_router.callback_query(F.data == "broadcast")
async def process_broadcast(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await state.clear()
    await state.set_state(BroadcastState.waiting_for_broadcast_text)
    await _safe_edit(
        callback.message,
        "Введите текст для рассылки.\n"
        "Сообщение уйдёт всем активным пользователям.\n\n"
        "Максимум 4000 символов.",
        cancel_broadcast_menu(),
    )
    await callback.answer()


@admin_router.message(BroadcastState.waiting_for_broadcast_text, _is_plain_text)
async def handle_broadcast_text(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return

    broadcast_text = (message.text or "").strip()
    if not broadcast_text:
        await message.answer(
            "Текст не может быть пустым. Введите сообщение.",
            reply_markup=cancel_broadcast_menu(),
        )
        return
    if len(broadcast_text) > 4000:
        await message.answer(
            "Сообщение слишком длинное (максимум 4000 символов). Сократите текст.",
            reply_markup=cancel_broadcast_menu(),
        )
        return

    await state.update_data(broadcast_text=broadcast_text)
    await state.set_state(BroadcastState.confirming_broadcast)
    await message.answer(
        "📢 Предпросмотр рассылки\n\n"
        f"{broadcast_text}\n\n"
        "Отправить всем активным пользователям?",
        reply_markup=confirm_broadcast_menu(),
    )


@admin_router.message(BroadcastState.confirming_broadcast, _is_plain_text)
async def broadcast_confirming_text(message: Message):
    await message.answer(
        "Нажмите «✅ Отправить» или «❌ Отмена».",
        reply_markup=confirm_broadcast_menu(),
    )


@admin_router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await _safe_edit(callback.message, "Рассылка отменена.", back_menu())
    await callback.answer()


@admin_router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer("Текст рассылки потерян. Начните заново.", show_alert=True)
        await state.clear()
        return

    try:
        remaining = await acquire_cooldown(
            f"broadcast:cooldown:{callback.from_user.id}",
            settings.BROADCAST_COOLDOWN_SECONDS,
        )
    except Exception:
        logger.exception("Не удалось проверить cooldown рассылки")
        await callback.answer(
            "Сервис рассылки временно недоступен. Попробуйте позже.",
            show_alert=True,
        )
        return

    if remaining is not None:
        await callback.answer(
            f"Подождите {remaining} сек. перед следующей рассылкой.",
            show_alert=True,
        )
        return

    users = await db.get_all_active_users()
    if not users:
        await callback.answer("Нет активных пользователей.", show_alert=True)
        await state.clear()
        return

    await _safe_edit(
        callback.message,
        f"📤 Рассылка началась. Получателей: {len(users)}.",
    )
    await callback.answer()

    sent = 0
    failed = 0
    blocked = 0
    total = len(users)

    for index, user in enumerate(users, start=1):
        try:
            await callback.bot.send_message(
                chat_id=user.telegram_id, text=broadcast_text
            )
            sent += 1
        except TelegramRetryAfter as exc:
            logger.warning(
                "Telegram попросил подождать %s сек. при рассылке", exc.retry_after
            )
            await asyncio.sleep(exc.retry_after)
            try:
                await callback.bot.send_message(
                    chat_id=user.telegram_id, text=broadcast_text
                )
                sent += 1
            except Exception:
                failed += 1
                logger.exception("Не удалось отправить сообщение %s", user.telegram_id)
        except TelegramForbiddenError:
            blocked += 1
            await db.deactivate_broadcast(user.telegram_id)
            logger.info("Пользователь %s заблокировал бота", user.telegram_id)
        except TelegramAPIError as exc:
            failed += 1
            logger.warning("Ошибка отправки %s: %s", user.telegram_id, exc)
        except Exception:
            failed += 1
            logger.exception("Неожиданная ошибка отправки %s", user.telegram_id)

        if index % 25 == 0:
            await _safe_edit(
                callback.message,
                f"📤 Рассылка: {index}/{total}. "
                f"Отправлено: {sent}, ошибок: {failed + blocked}.",
            )
        await asyncio.sleep(settings.BROADCAST_DELAY_SECONDS)

    try:
        await db.log_broadcast(
            message=broadcast_text,
            sent_count=sent,
            failed_count=failed + blocked,
            admin_telegram_id=callback.from_user.id,
        )
    except Exception:
        logger.exception("Не удалось сохранить аудит рассылки")

    logger.info(
        "Рассылка завершена: отправлено=%s, ошибок=%s, заблокировали=%s",
        sent,
        failed,
        blocked,
    )
    await state.clear()
    await _safe_edit(
        callback.message,
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Отправлено: <b>{sent}</b>\n"
        f"Ошибок: <b>{failed}</b>\n"
        f"Заблокировали бота: <b>{blocked}</b>",
        back_menu(),
        parse_mode="HTML",
    )


# ─── Нейросети ───────────────────────────────────────────────────────────────
async def _refresh_models(callback: CallbackQuery) -> None:
    models = await db.get_all_models()
    await _safe_edit(
        callback.message,
        models_text(models),
        models_panel(models),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "models")
async def process_models(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await _refresh_models(callback)
    await callback.answer()


@admin_router.callback_query(F.data.startswith("model_toggle:"))
async def process_model_toggle(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    raw_id = callback.data.split(":", 1)[1]
    if not raw_id.isdigit():
        await callback.answer("Некорректная модель.", show_alert=True)
        return

    status, model = await db.toggle_model_active(int(raw_id))
    if status != "ok":
        alerts = {
            "is_default": "Сначала назначьте другую модель по умолчанию (⭐).",
            "last_free": "Нельзя выключить единственную активную Free-модель.",
            "not_found": "Модель не найдена.",
        }
        await callback.answer(
            alerts.get(status, "Не удалось изменить модель."), show_alert=True
        )
        return

    await callback.answer(
        "Модель включена" if model.is_active else "Модель выключена"
    )
    await _refresh_models(callback)


@admin_router.callback_query(F.data.startswith("model_default:"))
async def process_model_default(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    raw_id = callback.data.split(":", 1)[1]
    if not raw_id.isdigit():
        await callback.answer("Некорректная модель.", show_alert=True)
        return

    status, model = await db.set_default_model(int(raw_id))
    if status == "not_active":
        await callback.answer(
            "Сначала включите модель, затем назначайте её по умолчанию.",
            show_alert=True,
        )
        return
    if status != "ok" or model is None:
        await callback.answer("Модель не найдена.", show_alert=True)
        return

    await callback.answer(f"По умолчанию: {model.name}")
    await _refresh_models(callback)


# ─── Подписки ────────────────────────────────────────────────────────────────
@admin_router.callback_query(F.data == "subscriptions")
async def process_subscriptions(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await _safe_edit(
        callback.message,
        "👑 <b>Подписки</b>\n\n"
        "Здесь можно выдать или забрать подписку Pro.\n"
        "Pro открывает доступ к более мощной нейросети.",
        subscriptions_panel(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data.in_({"sub_grant", "sub_revoke"}))
async def process_subscription_action(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    action = "grant" if callback.data == "sub_grant" else "revoke"
    await state.set_state(SubscriptionState.waiting_identifier)
    await state.update_data(sub_action=action)

    if action == "grant":
        text = (
            "👑 <b>Выдача Pro</b>\n\n"
            "Введите Telegram ID или @username пользователя.\n"
            "Пользователь должен хотя бы раз написать боту."
        )
    else:
        text = (
            "🚫 <b>Снятие Pro</b>\n\n"
            "Введите Telegram ID или @username пользователя."
        )
    await _safe_edit(callback.message, text, cancel_state_menu(), parse_mode="HTML")
    await callback.answer()


@admin_router.message(SubscriptionState.waiting_identifier, _is_plain_text)
async def process_subscription_identifier(
    message: Message, state: FSMContext
):
    if not await db.is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return

    data = await state.get_data()
    action = data.get("sub_action")
    if action not in ("grant", "revoke"):
        await state.clear()
        await message.answer(
            "Не удалось определить действие. Откройте раздел «👑 Подписки» заново.",
            reply_markup=back_menu(),
        )
        return

    identifier = (message.text or "").strip()
    if not identifier or len(identifier) > 64:
        await message.answer(
            "Введите Telegram ID (число) или @username.",
            reply_markup=cancel_state_menu(),
        )
        return

    target = await db.find_user_by_identifier(identifier)
    if target is None:
        await message.answer(
            "Пользователь не найден. Проверьте ID или @username — "
            "он должен хотя бы раз написать боту.",
            reply_markup=cancel_state_menu(),
        )
        return

    new_tier = TIER_PRO if action == "grant" else TIER_FREE
    updated = await db.set_subscription_tier(target.telegram_id, new_tier)
    if updated is None:
        await message.answer(
            "Не удалось обновить подписку. Попробуйте ещё раз.",
            reply_markup=cancel_state_menu(),
        )
        return

    await state.clear()

    notified = True
    if action == "grant":
        pro_models = [
            model
            for model in await db.get_available_models(TIER_PRO)
            if model.required_tier == TIER_PRO
        ]
        pro_name = pro_models[0].name if pro_models else "PopitusGPT1.5v-Pro"
        notify_text = (
            f"🎉 Вам активирована подписка {pro_name}!\n"
            "Теперь вам доступна более мощная нейросеть.\n\n"
            "Выберите её в меню «⚙️ Выбрать нейросеть»."
        )
    else:
        notify_text = (
            "Подписка Pro отключена. Осталась базовая нейросеть.\n\n"
            "При необходимости выберите модель в меню «⚙️ Выбрать нейросеть»."
        )

    try:
        await message.bot.send_message(
            chat_id=target.telegram_id,
            text=notify_text,
            reply_markup=kb.main_menu(new_tier),
        )
    except TelegramForbiddenError:
        notified = False
        logger.info(
            "Пользователь %s заблокировал бота, уведомление не отправлено",
            target.telegram_id,
        )
    except TelegramAPIError:
        notified = False
        logger.exception(
            "Не удалось отправить уведомление о подписке %s", target.telegram_id
        )

    handle = f"@{target.username}" if target.username else str(target.telegram_id)
    action_text = "Pro активирована" if action == "grant" else "Pro отключена"
    note = (
        "Уведомление отправлено."
        if notified
        else "Уведомление не отправлено: бот заблокирован или ошибка Telegram."
    )
    await message.answer(
        f"✅ {action_text} для {handle} (ID {target.telegram_id}).\n{note}",
        reply_markup=back_menu(),
    )


# ─── Блокировки ──────────────────────────────────────────────────────────────
@admin_router.callback_query(F.data == "blocks")
async def process_blocks(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await _safe_edit(
        callback.message,
        "🚫 <b>Блокировки</b>\n\n"
        "Заблокированный пользователь не может пользоваться ботом: "
        "его сообщения и нажатия кнопок игнорируются.\n\n"
        "Можно указать Telegram ID или @username.",
        blocks_panel(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data.in_({"block_user", "unblock_user"}))
async def process_block_action(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    action = "block" if callback.data == "block_user" else "unblock"
    await state.set_state(BlockState.waiting_identifier)
    await state.update_data(block_action=action)
    if action == "block":
        text = (
            "🚫 <b>Блокировка пользователя</b>\n\n"
            "Введите Telegram ID или @username."
        )
    else:
        text = (
            "✅ <b>Разблокировка пользователя</b>\n\n"
            "Введите Telegram ID или @username."
        )
    await _safe_edit(callback.message, text, cancel_state_menu(), parse_mode="HTML")
    await callback.answer()


@admin_router.message(BlockState.waiting_identifier, _is_plain_text)
async def process_block_identifier(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return

    data = await state.get_data()
    action = data.get("block_action")
    if action not in ("block", "unblock"):
        await state.clear()
        await message.answer(
            "Не удалось определить действие. Откройте «🚫 Блокировки» заново.",
            reply_markup=back_menu(),
        )
        return

    identifier = (message.text or "").strip()
    if not identifier or len(identifier) > 64:
        await message.answer(
            "Введите Telegram ID (число) или @username.",
            reply_markup=cancel_state_menu(),
        )
        return

    target = await db.find_user_by_identifier(identifier)
    if target is None:
        await message.answer(
            "Пользователь не найден. Проверьте ID или @username — "
            "он должен хотя бы раз написать боту.",
            reply_markup=cancel_state_menu(),
        )
        return

    if action == "block" and target.is_admin:
        await message.answer(
            "Нельзя заблокировать администратора.",
            reply_markup=blocks_panel(),
        )
        await state.clear()
        return

    blocked = action == "block"
    updated = await db.set_user_blocked(target.telegram_id, blocked)
    if updated is None:
        await message.answer(
            "Не удалось обновить пользователя. Попробуйте ещё раз.",
            reply_markup=blocks_panel(),
        )
        await state.clear()
        return

    await state.clear()

    notified = True
    if blocked:
        notify_text = (
            "🚫 Доступ к боту ограничен администратором.\n\n"
            "Если вы считаете это ошибкой, свяжитесь с администратором: @Pop_it016"
        )
    else:
        notify_text = "✅ Доступ к боту восстановлен. Можете продолжать работу."

    try:
        await message.bot.send_message(
            chat_id=target.telegram_id,
            text=notify_text,
            reply_markup=(
                None
                if blocked
                else kb.main_menu(target.subscription_tier or TIER_FREE)
            ),
        )
    except TelegramForbiddenError:
        notified = False
        logger.info(
            "Пользователь %s заблокировал бота, уведомление не отправлено",
            target.telegram_id,
        )
    except TelegramAPIError:
        notified = False
        logger.exception(
            "Не удалось отправить уведомление о блокировке %s", target.telegram_id
        )

    handle = f"@{target.username}" if target.username else str(target.telegram_id)
    action_text = "заблокирован" if blocked else "разблокирован"
    note = (
        "Уведомление отправлено."
        if notified
        else "Уведомление не отправлено: бот заблокирован или ошибка Telegram."
    )
    await message.answer(
        f"✅ Пользователь {handle} (ID {target.telegram_id}) {action_text}.\n{note}",
        reply_markup=back_menu(),
    )
