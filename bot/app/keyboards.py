from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)


main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📚 Мои учебники"),
         KeyboardButton(text="О боте")],
        [KeyboardButton(text="➕ Добавить учебник")],
        [KeyboardButton(text="🧠 Сгенерировать урок")],
    ],
    resize_keyboard=True,
)


def documents_keyboard(documents: list[dict]) -> InlineKeyboardMarkup:
    """documents = [{"id": "...", "title": "...", "grade": "..."}, ...]"""
    buttons = [
        [InlineKeyboardButton(
            text=f"{d['title']} ({d['grade']})" if d.get("grade") else d["title"],
            callback_data=f"doc:{d['id']}",
        )]
        for d in documents
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)