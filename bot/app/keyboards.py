from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)


main_menu = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📚 Мои учебники"),
                                           KeyboardButton(text="О боте")],
                                          [KeyboardButton(text="➕ Добавить учебник")],
                                          [KeyboardButton(text="🧠 Сгенерировать урок")]],
                                        resize_keyboard=True,
)

cancel = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel")]])

# documents = [{"id": "...", "title": "...", "grade": "..."}, ...]
def documents_keyboard(documents: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"{d['title']} ({d['grade']})" if d.get("grade") else d["title"],
            callback_data=f"doc:{d['id']}",
        )]
        for d in documents
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)