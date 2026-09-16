import os
import tempfile

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy import select

from database.database import async_session
from database.models import User, Document
from services.queue import enqueue_ingest
from services.generator import generate_lesson
from app.keyboards import main_menu, documents_keyboard


router = Router()


class AddBook(StatesGroup):
    waiting_meta = State()
    waiting_pdf = State()


class Lesson(StatesGroup):
    waiting_doc = State()
    waiting_topic = State()


# ─── Регистрация юзера ────────────────────────────────────────
async def get_or_create_user(message: Message) -> User:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        user = (await session.execute(stmt)).scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


# ─── /start ───────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(message)
    await message.answer(f"""Привет, {message.from_user.first_name}!\n"
Я — AI-методист. Загрузи учебник в PDF, и я смогу генерировать по нему уроки.""",
        reply_markup=main_menu,
    )


# ─── Список учебников ─────────────────────────────────────────
@router.message(F.text == "📚 Мои учебники")
async def list_documents(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(message)

    async with async_session() as session:
        stmt = select(Document).where(Document.user_id == user.id)
        docs = (await session.execute(stmt)).scalars().all()

    if not docs:
        await message.answer(
            "У тебя пока нет учебников. Нажми «➕ Добавить учебник»."
        )
        return

    text = "📚 Твои учебники:\n\n" + "\n".join(
        f"• {d.title}" + (f" — {d.grade}" if d.grade else "")
        for d in docs
    )
    await message.answer(text)


# ─── Добавить учебник ─────────────────────────────────────────
@router.message(F.text == "➕ Добавить учебник")
async def add_book_start(message: Message, state: FSMContext):
    await get_or_create_user(message)
    await state.set_state(AddBook.waiting_meta)
    await message.answer("""Напиши данные учебника через запятую:
<b>Название, класс, авторы</b>\n
Пример: <i>Алгебра, 7 класс, Прибауткин</i>""",
    )


@router.message(AddBook.waiting_meta)
async def add_book_meta(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 1:
        await message.answer("Не понял. Напиши через запятую, например: Алгебра, 7 класс, Прибауткин")
        return

    title = parts[0]
    grade = parts[1] if len(parts) > 1 else None
    author = parts[2] if len(parts) > 2 else None

    await state.update_data(title=title, grade=grade, author=author)
    await state.set_state(AddBook.waiting_pdf)
    await message.answer(
        "Теперь скинь PDF-файл учебника.\n"
        "Другие файлы будут проигнорированы."
    )


@router.message(AddBook.waiting_pdf, F.document)
async def add_book_pdf(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await message.answer("Это не PDF. Скинь именно PDF-файл.")
        return

    data = await state.get_data()
    await state.clear()

    # скачиваем во временный файл
    file = await message.bot.get_file(doc.file_id)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    await message.bot.download_file(file.file_path, tmp.name)
    tmp.close()

    user = await get_or_create_user(message)

    await message.answer(
        "📥 Начинаю обработку и загрузку учебника в базу данных...\n"
        "Это может занять несколько минут. Я напишу, когда закончу."
    )

    await enqueue_ingest(
        pdf_path=tmp.name,
        filename=doc.file_name,
        user_id=user.id,
        title=data["title"],
        grade=data.get("grade"),
        author=data.get("author"),
        telegram_id=message.from_user.id,
    )

@router.message(F.text == "🧠 Сгенерировать урок")
async def lesson_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(message)

    async with async_session() as session:
        stmt = select(Document).where(Document.user_id == user.id)
        docs = (await session.execute(stmt)).scalars().all()

    if not docs:
        await message.answer(
            "Сначала добавь хотя бы один учебник — нажми «➕ Добавить учебник»."
        )
        return

    docs_data = [
        {"id": str(d.id), "title": d.title, "grade": d.grade}
        for d in docs
    ]
    await state.set_state(Lesson.waiting_doc)
    await message.answer(
        "По какому учебнику делать урок?",
        reply_markup=documents_keyboard(docs_data),
    )


@router.callback_query(Lesson.waiting_doc, F.data.startswith("doc:"))
async def lesson_doc_chosen(callback: CallbackQuery, state: FSMContext):
    doc_id = callback.data.split(":", 1)[1]
    await state.update_data(doc_id=doc_id)
    await state.set_state(Lesson.waiting_topic)
    await callback.message.edit_text("Напиши тему урока (например: «списки»).")
    await callback.answer()


@router.message(Lesson.waiting_topic)
async def lesson_topic(message: Message, state: FSMContext):
    data = await state.get_data()
    doc_id = data.get("doc_id")
    await state.clear()

    user = await get_or_create_user(message)

    await message.answer("🧠 Генерирую урок, подожди ~30 секунд...")

    result = await generate_lesson(
        topic=message.text,
        user_id=user.id,
        document_id=doc_id,
        top_k=5,
    )

    text = result["lesson"]
    if result["sources"]:
        text += f"\n\n📖 Источники: страницы {result['sources']}"

    # разбиваем на части, если больше 4000 символов (лимит Telegram)
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i+4000])
    else:
        await message.answer(text)