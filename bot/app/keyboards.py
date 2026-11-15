from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


MENU_TEXTS = {
    "📚 Мои учебники",
    "➕ Добавить учебник",
    "🧠 Сгенерировать урок",
    "📝 Сгенерировать тест",
    "⚙️ Выбрать нейросеть",
    "💎 Купить Pro",
    "📞 Контакты",
    "❓ Помощь",
}

TIER_LABELS = {"free": "Free", "pro": "Pro"}


def main_menu(tier: str = "free") -> ReplyKeyboardMarkup:
    """Главное меню: кнопка покупки Pro видна только Free-пользователям."""
    rows = [
        [KeyboardButton(text="📚 Мои учебники"), KeyboardButton(text="➕ Добавить учебник")],
        [
            KeyboardButton(text="🧠 Сгенерировать урок"),
            KeyboardButton(text="📝 Сгенерировать тест"),
        ],
    ]
    if tier == "pro":
        rows.append([KeyboardButton(text="📞 Контакты")])
    else:
        rows.append(
            [
                KeyboardButton(text="💎 Купить Pro"),
                KeyboardButton(text="📞 Контакты"),
            ]
        )
    rows.append(
        [KeyboardButton(text="⚙️ Выбрать нейросеть"), KeyboardButton(text="❓ Помощь")]
    )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


cancel = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
)


skip_grade = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip:grade")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ]
)


skip_author = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip:author")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ]
)


def models_keyboard(
    models: list,
    current_id: int | None = None,
    show_buy: bool = False,
) -> InlineKeyboardMarkup:
    """Список доступных пользователю нейросетей с отметкой текущей."""
    buttons = []
    for model in models:
        mark = "✅ " if current_id == model.id else ""
        tier = TIER_LABELS.get(model.required_tier, model.required_tier)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{model.name} · {tier}",
                    callback_data=f"model_pick:{model.id}",
                )
            ]
        )
    if show_buy:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💎 Купить Pro", callback_data="buy_pro"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def documents_keyboard(documents: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура выбора учебника для генерации урока."""
    buttons = []
    for document in documents:
        grade = document.get("grade")
        title = f"{document['title']} ({grade})" if grade else document["title"]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=title[:60],
                    callback_data=f"lesson_doc:{document['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def quiz_documents_keyboard(documents: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура выбора учебника для генерации теста."""
    buttons = []
    for document in documents:
        grade = document.get("grade")
        title = f"{document['title']} ({grade})" if grade else document["title"]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=title[:60],
                    callback_data=f"quiz_doc:{document['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def lesson_actions_keyboard(lesson_id: int) -> InlineKeyboardMarkup:
    """Действия после генерации урока."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Скачать в Word",
                    callback_data=f"lesson_docx:{lesson_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Задать вопрос по уроку",
                    callback_data=f"tutor_start:{lesson_id}",
                )
            ],
        ]
    )


def quiz_answers_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Показать ответы",
                    callback_data=f"quiz_answers:{token}",
                )
            ]
        ]
    )


def tutor_stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚫 Завершить диалог",
                    callback_data="tutor_stop",
                )
            ]
        ]
    )


def books_keyboard(documents: list[dict]) -> InlineKeyboardMarkup:
    """Список учебников с кнопкой удаления под каждым."""
    buttons = []
    for document in documents:
        title = document["title"]
        if document.get("grade"):
            title = f"{title}, {document['grade']}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Удалить «{title[:45]}»",
                    callback_data=f"delete_doc:{document['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def delete_confirm_keyboard(document_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить", callback_data=f"delete_confirm:{document_id}"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="delete_cancel"),
            ]
        ]
    )
