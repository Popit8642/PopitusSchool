import logging
import re
import uuid
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

import app.keyboards as kb
import database.functions as db
from config import settings
from database.models import (
    DOC_COMPLETED,
    DOC_FAILED,
    DOC_PENDING,
    DOC_PROCESSING,
    TIER_FREE,
    TIER_PRO,
)
from services.formatting import build_html_parts, html_to_plain
from services.generator import generate_lesson
from services.queue import enqueue_ingest, get_queue_position


logger = logging.getLogger(__name__)
router = Router()


# Быстрый фильтр по имени файла: такие названия отклоняем до скачивания.
_BLOCKED_NAME_RE = re.compile(
    r"\b(porn\w*|sex\w*|x{3,}\w*|nude\w*|erotic\w*|hentai|onlyfans|"
    r"escort\w*|milf|nsfw)\b",
    re.IGNORECASE,
)
_BLOCKED_NAME_PARTS = (
    "порн",
    "секс",
    "эротик",
    "интим",
    "обнаж",
    "голые",
    "голая",
    "голое",
    "голых",
    "голым",
    "18+",
    "для взрослых",
)
_SEPARATORS_RE = re.compile(r"[_\-.]+")
BLOCKED_FILE_TEXT = (
    "❌ Файл заблокирован системой безопасности. "
    "Загружайте только учебные материалы"
)


def _is_unsafe_filename(name: str) -> bool:
    lowered = name.casefold()
    if _BLOCKED_NAME_RE.search(lowered):
        return True
    normalized = _SEPARATORS_RE.sub(" ", lowered)
    return any(
        part in lowered or part in normalized
        for part in _BLOCKED_NAME_PARTS
    )


class AddBook(StatesGroup):
    waiting_title = State()
    waiting_grade = State()
    waiting_authors = State()
    waiting_pdf = State()


class Lesson(StatesGroup):
    waiting_doc = State()
    waiting_topic = State()


HELP_TEXT = """❓ <b>Помощь</b>

Я — AI-методист. Помогаю готовить уроки по вашим учебникам.

<b>Как добавить учебник:</b>
1. Нажмите «➕ Добавить учебник».
2. Введите название, класс и авторов. Класс и авторов можно пропустить.
3. Отправьте PDF-файл (до 20 МБ). Текстовые PDF и сканы поддерживаются: сканы я распознаю через OCR.
4. Дождитесь сообщения «✅ Учебник добавлен».

<b>Как сгенерировать урок:</b>
1. Нажмите «🧠 Сгенерировать урок».
2. Выберите учебник.
3. Введите тему урока.
4. Получите конспект со структурой и страницами-источниками.

<b>Полезно знать:</b>
• Нейросеть можно выбрать в разделе «⚙️ Выбрать нейросеть».
• Подписку Pro можно оформить за Telegram Stars в разделе «💎 Купить Pro».
• Учебники хранятся отдельно у каждого пользователя.
• Урок опирается только на текст выбранного учебника.
• Если нейросеть не отвечает, бот сообщит об ошибке — просто попробуйте позже.
• Учебник можно удалить в разделе «📚 Мои учебники».
"""

CONTACTS_TEXT = """📞 <b>Контакты</b>

По всем вопросам пишите: @Pop_it016 / emelanovs608@gmail.com.

Оплата подписок временно производится через Telegram Stars.
"""

STATUS_LABELS = {
    DOC_PENDING: "⏳ В очереди",
    DOC_PROCESSING: "⚙️ Обрабатывается",
    DOC_COMPLETED: "✅ Готов",
    DOC_FAILED: "❌ Ошибка",
}


def _tier_title(tier: str) -> str:
    return "Pro" if tier == TIER_PRO else "Free"


def _profile_line(user, model) -> str:
    tier = _tier_title(user.subscription_tier or TIER_FREE)
    if model is None:
        return f"👤 Подписка: <b>{tier}</b>"
    return (
        f"👤 Подписка: <b>{tier}</b> · "
        f"нейросеть: <b>{escape(model.name)}</b>"
    )


async def _edit_or_answer(
    callback: CallbackQuery, text: str, reply_markup=None
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


async def _update_status(message: Message, text: str) -> None:
    """Меняет служебное сообщение, не падая, если его удалили."""
    try:
        await message.edit_text(text)
        return
    except TelegramAPIError:
        logger.warning("Не удалось изменить статусное сообщение, отправляю новое")
    try:
        await message.answer(text)
    except TelegramAPIError:
        logger.exception("Не удалось отправить статусное сообщение")


async def _send_html_part(message: Message, part: str) -> None:
    try:
        await message.answer(part, parse_mode="HTML")
    except TelegramBadRequest as exc:
        logger.warning("HTML не разобрался (%s), отправляю plain text", exc)
        try:
            await message.answer(html_to_plain(part))
        except TelegramAPIError:
            logger.exception("Не удалось отправить часть урока даже plain text")
    except TelegramAPIError:
        logger.exception("Не удалось отправить часть урока")


def _book_status(document: dict) -> str:
    status = document.get("status") or DOC_PENDING
    label = STATUS_LABELS.get(status, status)
    if status == DOC_COMPLETED:
        return f"{label} · страниц: {document['pages']} · фрагментов: {document['chunks']}"
    if status == DOC_FAILED:
        error = (document.get("error_message") or "").strip()
        if error:
            return f"{label}: {error[:80]}"
        return f"{label}: не удалось обработать"
    return label


def _books_text(documents: list[dict], total: int | None = None) -> str:
    lines = ["📚 Ваши учебники:\n"]
    for index, document in enumerate(documents, start=1):
        title = f"{index}. «{document['title'][:80]}»"
        if document.get("grade"):
            title += f" — {document['grade'][:32]}"
        author = (document.get("author") or "авторы не указаны")[:80]
        lines.append(title)
        lines.append(f"   Авторы: {author}")
        lines.append(f"   {_book_status(document)}")
        lines.append("")
    if total is not None and total > len(documents):
        lines.append(f"Показаны последние {len(documents)} из {total} учебников.")
    return "\n".join(lines).strip()


# ─── Старт и помощь ──────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    model = await db.resolve_user_model(user)
    await message.answer(
        f"Приветствую, {escape(message.from_user.first_name)}!\n\n"
        "Я — AI-методист, ваш помощник по подготовке уроков.\n"
        f"{_profile_line(user, model)}\n\n"
        "Выберите действие в меню ниже.",
        parse_mode="HTML",
        reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
    )


@router.message(F.text == "📞 Контакты")
async def contacts(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    await message.answer(
        CONTACTS_TEXT,
        parse_mode="HTML",
        reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
    )


# ─── Выбор нейросети ─────────────────────────────────────────────────────────
async def _models_screen(user):
    """Текст и клавиатура экрана выбора нейросети."""
    tier = user.subscription_tier or TIER_FREE
    models = await db.get_available_models(tier)
    current = await db.resolve_user_model(user)

    lines = [
        "⚙️ <b>Выбор нейросети</b>",
        "",
        _profile_line(user, current),
        "",
        "Доступные модели:",
    ]
    for model in models:
        model_tier = "Pro" if model.required_tier == TIER_PRO else "Free"
        lines.append(f"• {escape(model.name)} — {model_tier}")
    if tier == TIER_FREE:
        lines += [
            "",
            "Модели уровня Pro доступны по подписке.",
            f"💎 Купить Pro — {settings.PRO_PRICE_STARS} Stars.",
        ]
    else:
        lines += ["", "💎 У вас активен тариф Pro. Спасибо за поддержку!"]

    return "\n".join(lines), kb.models_keyboard(
        models,
        current.id if current else None,
        show_buy=tier != TIER_PRO,
    ), current


@router.message(F.text == "⚙️ Выбрать нейросеть")
async def model_menu(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    text, keyboard, _ = await _models_screen(user)
    if not keyboard.inline_keyboard:
        await message.answer(
            "Нейросети временно недоступны. Попробуйте позже.",
            reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
        )
        return
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("model_pick:"))
async def model_pick(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    raw_id = callback.data.split(":", 1)[1]
    if not raw_id.isdigit():
        await callback.answer("Некорректный выбор.", show_alert=True)
        return

    await db.get_or_create_user(
        telegram_id=callback.from_user.id, username=callback.from_user.username
    )
    status, model = await db.set_user_model(callback.from_user.id, int(raw_id))
    if status == "forbidden":
        await callback.answer(
            "Эта нейросеть недоступна на вашем тарифе.", show_alert=True
        )
        return
    if status == "model_not_found":
        await callback.answer("Нейросеть не найдена.", show_alert=True)
        return
    if status != "ok":
        await callback.answer("Не удалось сохранить выбор.", show_alert=True)
        return

    user = await db.get_or_create_user(
        telegram_id=callback.from_user.id, username=callback.from_user.username
    )
    text, keyboard, _ = await _models_screen(user)
    text = f"✅ Выбрана нейросеть «{escape(model.name)}».\n\n{text}"
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text, parse_mode="HTML", reply_markup=keyboard
        )
    await callback.answer("Сохранено")


# ─── Мои учебники ────────────────────────────────────────────────────────────
@router.message(F.text == "📚 Мои учебники")
async def list_documents(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    documents = await db.get_user_documents_with_stats(user.id)

    if not documents:
        await message.answer(
            "У вас пока нет учебников. Нажмите «➕ Добавить учебник».",
            reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
        )
        return

    visible = documents[:10]
    await message.answer(
        _books_text(visible, len(documents)),
        reply_markup=kb.books_keyboard(visible),
    )


@router.callback_query(F.data.startswith("delete_doc:"))
async def ask_delete_document(callback: CallbackQuery):
    document_id = callback.data.split(":", 1)[1]
    user = await db.get_or_create_user(
        telegram_id=callback.from_user.id, username=callback.from_user.username
    )
    document = await db.get_user_document(document_id, user.id)
    if document is None:
        await callback.answer("Учебник не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        f"Удалить учебник «{document.title}»?\n"
        "Все его фрагменты будут удалены безвозвратно.",
        reply_markup=kb.delete_confirm_keyboard(str(document.id)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_confirm:"))
async def confirm_delete_document(callback: CallbackQuery):
    document_id = callback.data.split(":", 1)[1]
    user = await db.get_or_create_user(
        telegram_id=callback.from_user.id, username=callback.from_user.username
    )
    result, title, source_file = await db.delete_document(document_id, user.id)

    if result == "not_found":
        await callback.answer("Учебник не найден.", show_alert=True)
        return
    if result == "busy":
        await callback.answer(
            "Учебник ещё обрабатывается. Дождитесь завершения.", show_alert=True
        )
        return

    if source_file:
        try:
            (settings.upload_dir / Path(source_file).name).unlink(missing_ok=True)
        except OSError:
            logger.exception("Не удалось удалить файл учебника %s", document_id)

    await callback.message.edit_text(f"🗑 Учебник «{title}» удалён.")
    await callback.answer("Удалено")


@router.callback_query(F.data == "delete_cancel")
async def cancel_delete_document(callback: CallbackQuery):
    await callback.message.edit_text("Удаление отменено.")
    await callback.answer()


# ─── Добавление учебника ─────────────────────────────────────────────────────
@router.message(F.text == "➕ Добавить учебник")
async def add_book_start(message: Message, state: FSMContext):
    await state.clear()
    await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    await state.set_state(AddBook.waiting_title)
    await message.answer(
        "Шаг 1 из 4. Введите название учебника.\n\n"
        "Например: «Алгебра».",
        reply_markup=kb.cancel,
    )


@router.message(AddBook.waiting_title, F.text, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if len(title) < 2 or len(title) > 255:
        await message.answer(
            "Название должно быть от 2 до 255 символов. Попробуйте ещё раз.",
            reply_markup=kb.cancel,
        )
        return

    await state.update_data(title=title)
    await state.set_state(AddBook.waiting_grade)
    await message.answer(
        "Шаг 2 из 4. Введите класс.\n\n"
        "Например: «7 класс». Если не хотите указывать — нажмите «Пропустить».",
        reply_markup=kb.skip_grade,
    )


@router.message(AddBook.waiting_title, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_title_wrong(message: Message):
    await message.answer(
        "Пожалуйста, отправьте название учебника текстом.",
        reply_markup=kb.cancel,
    )


@router.callback_query(F.data == "skip:grade")
async def skip_grade(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != AddBook.waiting_grade.state:
        await callback.answer("Этот шаг уже пройден.", show_alert=True)
        return
    await state.update_data(grade=None)
    await state.set_state(AddBook.waiting_authors)
    await _edit_or_answer(
        callback,
        "Класс пропущен.\n\nШаг 3 из 4. Введите авторов учебника.\n"
        "Если не хотите указывать — нажмите «Пропустить».",
        kb.skip_author,
    )
    await callback.answer()


@router.message(AddBook.waiting_grade, F.text, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_grade(message: Message, state: FSMContext):
    grade = message.text.strip()
    if len(grade) > 32:
        await message.answer(
            "Слишком длинное название класса (максимум 32 символа).",
            reply_markup=kb.skip_grade,
        )
        return
    await state.update_data(grade=grade or None)
    await state.set_state(AddBook.waiting_authors)
    await message.answer(
        "Шаг 3 из 4. Введите авторов учебника.\n"
        "Если не хотите указывать — нажмите «Пропустить».",
        reply_markup=kb.skip_author,
    )


@router.message(AddBook.waiting_grade, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_grade_wrong(message: Message):
    await message.answer(
        "Пожалуйста, отправьте класс текстом или нажмите «Пропустить».",
        reply_markup=kb.skip_grade,
    )


@router.callback_query(F.data == "skip:author")
async def skip_author(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != AddBook.waiting_authors.state:
        await callback.answer("Этот шаг уже пройден.", show_alert=True)
        return
    await state.update_data(author=None)
    await state.set_state(AddBook.waiting_pdf)
    await _edit_or_answer(
        callback,
        "Авторы пропущены.\n\nШаг 4 из 4. Отправьте PDF-файл учебника.\n"
        "Максимальный размер — 20 МБ.",
        kb.cancel,
    )
    await callback.answer()


@router.message(AddBook.waiting_authors, F.text, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_author(message: Message, state: FSMContext):
    author = message.text.strip()
    if len(author) > 255:
        await message.answer(
            "Слишком длинный список авторов (максимум 255 символов).",
            reply_markup=kb.skip_author,
        )
        return
    await state.update_data(author=author or None)
    await state.set_state(AddBook.waiting_pdf)
    await message.answer(
        "Шаг 4 из 4. Отправьте PDF-файл учебника.\n"
        "Максимальный размер — 20 МБ.",
        reply_markup=kb.cancel,
    )


@router.message(AddBook.waiting_authors, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_author_wrong(message: Message):
    await message.answer(
        "Пожалуйста, отправьте авторов текстом или нажмите «Пропустить».",
        reply_markup=kb.skip_author,
    )


@router.callback_query(F.data == "cancel")
async def cancel_waiting(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_or_answer(callback, "Действие отменено.")
    await callback.answer()


@router.message(AddBook.waiting_pdf, F.document)
async def add_book_pdf(message: Message, state: FSMContext):
    document = message.document
    file_name = document.file_name or f"document_{document.file_id}.pdf"
    mime_type = (document.mime_type or "").lower()

    if not file_name.lower().endswith(".pdf") and mime_type != "application/pdf":
        await message.answer(
            "❌ Это не PDF. Отправьте именно PDF-файл учебника.",
            reply_markup=kb.cancel,
        )
        return

    if _is_unsafe_filename(file_name):
        logger.warning(
            "Заблокировано имя файла: user=%s file=%r",
            message.from_user.id,
            file_name,
        )
        await message.answer(BLOCKED_FILE_TEXT, reply_markup=kb.cancel)
        return

    min_size = settings.MIN_PDF_SIZE_KB * 1024
    if document.file_size is not None and document.file_size < min_size:
        await message.answer(
            f"❌ Файл слишком маленький (меньше {settings.MIN_PDF_SIZE_KB} КБ). "
            "Похоже, это пустышка или мусор. Загрузите нормальный PDF.",
            reply_markup=kb.cancel,
        )
        return

    if (
        document.file_size is not None
        and document.file_size > settings.MAX_PDF_SIZE_BYTES
    ):
        await message.answer(
            "❌ Файл слишком большой (больше 20 МБ).\n\n"
            "Telegram не позволяет ботам скачивать такие файлы. "
            "Пожалуйста, сожмите PDF и отправьте снова.",
            reply_markup=kb.cancel,
        )
        return

    data = await state.get_data()
    title = data.get("title")
    if not title:
        await state.clear()
        user = await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        await message.answer(
            "Сессия добавления устарела. Начните заново: «➕ Добавить учебник».",
            reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
        )
        return

    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )

    document_id = uuid.uuid4()
    source_name = f"{document_id}.pdf"
    path = settings.upload_dir / source_name
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        file = await message.bot.get_file(document.file_id)
        await message.bot.download_file(file.file_path, destination=str(path))
    except Exception:
        logger.exception("Не удалось скачать PDF для пользователя %s", user.id)
        path.unlink(missing_ok=True)
        await message.answer(
            "❌ Не удалось скачать файл. Попробуйте отправить его ещё раз.",
            reply_markup=kb.cancel,
        )
        return

    try:
        downloaded_size = path.stat().st_size
        if downloaded_size > settings.MAX_PDF_SIZE_BYTES:
            path.unlink(missing_ok=True)
            await message.answer(
                "❌ Файл слишком большой (больше 20 МБ). Сожмите PDF и попробуйте снова.",
                reply_markup=kb.cancel,
            )
            return
        if downloaded_size < settings.MIN_PDF_SIZE_KB * 1024:
            path.unlink(missing_ok=True)
            await message.answer(
                f"❌ Файл слишком маленький (меньше {settings.MIN_PDF_SIZE_KB} КБ). "
                "Похоже, это пустышка или мусор. Загрузите нормальный PDF.",
                reply_markup=kb.cancel,
            )
            return
    except OSError:
        path.unlink(missing_ok=True)
        await message.answer(
            "❌ Не удалось прочитать скачанный файл. Попробуйте ещё раз.",
            reply_markup=kb.cancel,
        )
        return

    try:
        position = await get_queue_position()
    except Exception:
        logger.exception("Не удалось получить позицию в очереди")
        path.unlink(missing_ok=True)
        await message.answer(
            "❌ Очередь временно недоступна. Попробуйте отправить файл позже.",
            reply_markup=kb.cancel,
        )
        return

    try:
        created = await db.create_document(
            document_id=document_id,
            user_id=user.id,
            title=title,
            grade=data.get("grade"),
            author=data.get("author"),
            filename=file_name,
            source_file=source_name,
        )
    except Exception:
        logger.exception("Не удалось создать документ для пользователя %s", user.id)
        path.unlink(missing_ok=True)
        await message.answer(
            "❌ Не удалось сохранить учебник. Попробуйте позже.",
            reply_markup=kb.cancel,
        )
        return

    try:
        msg = await message.answer(
            f"📥 Файл принят. В очереди: позиция {position}.\n"
            "Я пришлю сообщение, когда начну обработку."
        )
        await db.update_document_notify(created.id, message.chat.id, msg.message_id)
    except Exception:
        # Уведомление не критично: воркер напишет пользователю сам
        msg = None
        logger.exception(
            "Не удалось сохранить уведомление для документа %s", created.id
        )
    await state.clear()

    try:
        await enqueue_ingest(created.id)
    except Exception:
        logger.exception("Не удалось поставить в очередь документ %s", created.id)
        await db.mark_document_failed(
            created.id, "Не удалось поставить задачу в очередь"
        )
        path.unlink(missing_ok=True)
        if msg is not None:
            await _update_status(
                msg, "❌ Не удалось поставить задачу в очередь. Попробуйте позже."
            )
        return

    logger.info(
        "PDF принят: user=%s document=%s file=%s size=%s position=%s",
        user.id,
        created.id,
        file_name,
        document.file_size,
        position,
    )


@router.message(AddBook.waiting_pdf, ~F.text.in_(kb.MENU_TEXTS))
async def add_book_pdf_wrong(message: Message):
    await message.answer(
        "Пожалуйста, отправьте PDF-файл учебника или нажмите «Отмена».",
        reply_markup=kb.cancel,
    )


# ─── Генерация урока ─────────────────────────────────────────────────────────
@router.message(F.text == "🧠 Сгенерировать урок")
async def lesson_start(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    documents = await db.get_user_documents_with_stats(user.id)
    completed = [item for item in documents if item["status"] == DOC_COMPLETED]

    if not completed:
        if documents:
            await message.answer(
                "Ваши учебники ещё обрабатываются. Дождитесь статуса «✅ Готов»."
            )
        else:
            await message.answer(
                "У вас нет учебников. Нажмите «➕ Добавить учебник».",
                reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
            )
        return

    await state.set_state(Lesson.waiting_doc)
    await message.answer(
        "Выберите учебник, по которому нужно составить урок:",
        reply_markup=kb.documents_keyboard(completed[:10]),
    )


@router.callback_query(Lesson.waiting_doc, F.data.startswith("lesson_doc:"))
async def lesson_document_chosen(callback: CallbackQuery, state: FSMContext):
    document_id = callback.data.split(":", 1)[1]
    user = await db.get_or_create_user(
        telegram_id=callback.from_user.id, username=callback.from_user.username
    )
    document = await db.get_user_document(document_id, user.id)
    if document is None or document.status != DOC_COMPLETED:
        await callback.answer(
            "Учебник не найден или ещё не готов.", show_alert=True
        )
        return

    await state.update_data(
        doc_id=str(document.id), doc_title=document.title
    )
    await state.set_state(Lesson.waiting_topic)
    await _edit_or_answer(
        callback,
        f"Учебник: «{document.title}».\n\n"
        "Введите тему урока. Например: «Линейные уравнения».",
        kb.cancel,
    )
    await callback.answer()


@router.message(Lesson.waiting_topic, F.text, ~F.text.in_(kb.MENU_TEXTS))
async def lesson_topic(message: Message, state: FSMContext):
    topic = message.text.strip()
    if len(topic) < 2 or len(topic) > 300:
        await message.answer(
            "Тема должна быть от 2 до 300 символов. Попробуйте ещё раз.",
            reply_markup=kb.cancel,
        )
        return

    data = await state.get_data()
    document_id = data.get("doc_id")
    document_title = data.get("doc_title")
    if not document_id:
        await state.clear()
        user = await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        await message.answer(
            "Не удалось определить учебник. Начните заново.",
            reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
        )
        return

    await state.clear()
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    model = await db.resolve_user_model(user)
    model_title = model.name if model else "нейросеть"
    status_message = await message.answer(
        f"{model_title}🧠 Генерирую урок, подождите 1–2 минуты..."
    )

    async with ChatActionSender.typing(
        bot=message.bot, chat_id=message.chat.id
    ):
        result = await generate_lesson(
            topic=topic,
            user_id=user.id,
            document_id=document_id,
            model_tag=model.ollama_tag if model else None,
        )

    error = result.get("error")
    if error == "timeout":
        await _update_status(status_message, "⏳ Нейросеть задумалась, попробуйте позже.")
        return
    if error == "not_found":
        await _update_status(
            status_message,
            "🔍 Не нашёл информации по этой теме в выбранном учебнике. "
            "Попробуйте другую формулировку.",
        )
        return
    if error or not result.get("lesson"):
        await _update_status(
            status_message,
            "❌ Произошла ошибка при генерации, попробуйте позже.",
        )
        return

    lesson = result["lesson"]
    sources = result.get("sources") or []

    try:
        await db.save_lesson(
            user_id=user.id,
            document_id=document_id,
            document_title=document_title,
            topic=topic,
            content=lesson,
            sources=", ".join(str(page) for page in sources) or None,
            duration_ms=result.get("duration_ms"),
        )
    except Exception:
        logger.exception("Не удалось сохранить урок в историю")

    parts = build_html_parts(lesson, sources)
    if not parts:
        await _update_status(
            status_message,
            "❌ Произошла ошибка при генерации, попробуйте позже.",
        )
        return

    await _update_status(status_message, "✅ Урок готов.")
    for part in parts:
        await _send_html_part(message, part)


@router.message(Lesson.waiting_topic, ~F.text.in_(kb.MENU_TEXTS))
async def lesson_topic_wrong(message: Message):
    await message.answer(
        "Пожалуйста, отправьте тему урока текстом или нажмите «Отмена».",
        reply_markup=kb.cancel,
    )


# ─── Fallback ────────────────────────────────────────────────────────────────
@router.message(F.text)
async def unknown_text(message: Message):
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    await message.answer(
        "Не понял. Воспользуйтесь меню ниже.",
        reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
    )


@router.message()
async def unknown_content(message: Message):
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id, username=message.from_user.username
    )
    await message.answer(
        "Поддерживаются текст и PDF-файлы. Воспользуйтесь меню ниже.",
        reply_markup=kb.main_menu(user.subscription_tier or TIER_FREE),
    )
