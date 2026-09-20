from aiogram import F, Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message,
)

import database.functions as db


admin_router = Router()

class BroadcastState(StatesGroup):
    waiting_for_broadcast_text = State()


def admin_panel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")],
    ])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_panel")],
    ])


def back_menu_and_clear_state():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="back_and_clear_state")],
    ])


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
    await callback.message.edit_text(
        f"Добро пожаловать, @{callback.from_user.username or callback.from_user.id}.\n"
        "Это панель управления ботом.",
        reply_markup=admin_panel(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "back_and_clear_state")
async def back_and_clear(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Действие отменено.",
        reply_markup=back_menu(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "stats")
async def process_stats(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    total, active = await db.count_users()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Всего пользователей: <b>{total}</b>\n"
        f"Активных (получают рассылку): <b>{active}</b>"
    )
    await callback.message.edit_text(text, reply_markup=back_menu(), parse_mode="HTML")
    await callback.answer()


@admin_router.callback_query(F.data == "broadcast")
async def process_broadcast(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.message.edit_text(
        "Введите текст для рассылки.\n"
        "Сообщение уйдёт всем активным пользователям.",
        reply_markup=back_menu_and_clear_state(),
    )
    await state.set_state(BroadcastState.waiting_for_broadcast_text)
    await callback.answer()


@admin_router.message(BroadcastState.waiting_for_broadcast_text, F.text)
async def handle_broadcast_text(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        await state.clear()
        return

    broadcast_text = message.text
    users = await db.get_all_active_users()

    count = 0
    failed = 0
    for user in users:
        try:
            await message.bot.send_message(chat_id=user.telegram_id, text=broadcast_text)
            count += 1
        except Exception as e:
            failed += 1
            print(f"[broadcast] ошибка {user.telegram_id}: {e}")

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"Отправлено: {count}\n"
        f"Ошибок: {failed}",
        reply_markup=back_menu(),
    )
    await state.clear()