import tempfile

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

router = Router()

from database.database import async_session
from database.models import User, Document
from services.queue import enqueue_ingest
from services.generator import generate_lesson
import app.keyboards as kb
import database.functions as db


class AddBook(StatesGroup):
    waiting_meta = State()
    waiting_pdf = State()


class Lesson(StatesGroup):
    waiting_doc = State()
    waiting_topic = State()


# ─── /start и регистрация юзера ───────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.get_or_create_user(telegram_id=message.from_user.id, username=message.from_user.username)
    await message.answer(f"""Приветствую, {message.from_user.first_name}!\n
Я — AI-методист, ваш персональный помощник по составлениям уроков.""",
        reply_markup=kb.main_menu,
    )

# ─── О боте ─────────────────────────────────────────
@router.message(F.text == "О боте")
async def cmd_about(message: Message):
    await message.answer("""Я — нейросеть, которая помогает учителям делать уроки. Не заменяю вас, а беру на себя рутину: вы читаете, объясняете, воспитываете, а я готовлю черновик урока по вашему учебнику.

Как я работаю:
Вы загружаете PDF-учебник — я его читаю, разбираю по страницам и запоминаю. Когда нужен урок по теме, я нахожу в учебнике нужные абзацы и составляю урок: цель, объяснение, пример, вопросы для проверки. В конце указываю страницы-источники, чтобы вы могли проверить.

Что я умею:
📥 Загружать учебники (даже сканы — распознаю текст с картинок)
📚 Хранить их в вашем личном кабинете
🧠 Составлять уроки по любой теме из учебника
📖 Показывать, откуда взята информация

Чего я не делаю:
❌ Не выдумываю факты — только то, что есть в учебнике
❌ Не отправляю ваши файлы в интернет — всё хранится на нашем сервере
❌ Не заменяю учителя — я инструмент, а не человек

Я основан на нейросети PopitusGPT v1.0""")

# ─── Список учебников ─────────────────────────────────────────
@router.message(F.text == "📚 Мои учебники")
async def list_documents(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(telegram_id=message.from_user.id, username=message.from_user.username)
    docs = await db.get_user_documents(user.id)

    if not docs:
        await message.answer("У вас пока нет учебников. Нажмите «➕ Добавить учебник».")
        return

    text = "📚 Ваши учебники:\n\n" + "\n".join(
        f"• {d.title}" + (f" — {d.grade}" if d.grade else "")
        for d in docs
    )
    await message.answer(text)


# ─── Добавить учебник ─────────────────────────────────────────
@router.message(F.text == "➕ Добавить учебник")
async def add_book_start(message: Message, state: FSMContext):
    await db.get_or_create_user(telegram_id=message.from_user.id, username=message.from_user.username)
    await state.set_state(AddBook.waiting_meta)
    await message.answer("""Напишите данные учебника через запятую:
Название, класс, авторы\n
Пример: Алгебра, 7 класс, Юрий Клинских""", reply_markup=kb.cancel
    )


@router.message(AddBook.waiting_meta)
async def add_book_meta(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 1:
        await message.answer("Не понял. Напишите через запятую, например: Алгебра, 7 класс, Прибауткин")
        return

    title = parts[0]
    grade = parts[1] if len(parts) > 1 else None
    author = parts[2] if len(parts) > 2 else None

    await state.update_data(title=title, grade=grade, author=author)
    await state.set_state(AddBook.waiting_pdf)
    await message.answer("""Теперь скиньте PDF-файл учебника.
Другие файлы будут проигнорированы.""", reply_markup=kb.cancel
    )

@router.callback_query(F.data == "cancel")
async def cancel_waiting(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Вы отменили действие.")

@router.message(AddBook.waiting_pdf, F.document)
async def add_book_pdf(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await message.answer("Это не PDF. Скиньте именно PDF-файл.")
        return

    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await message.answer("""❌ Файл слишком большой (больше 20 МБ).\n
Официальный Telegram API не позволяет ботам скачивать такие файлы.
Пожалуйста, сожмите PDF (например, на ilovepdf.com) и отправьте снова.""")
        return
    data = await state.get_data()
    await state.clear()

    # скачиваем во временный файл
    file = await message.bot.get_file(doc.file_id)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    await message.bot.download_file(file.file_path, tmp.name)
    tmp.close()

    user = await db.get_or_create_user(telegram_id=message.from_user.id, username=message.from_user.username)

    msg = await message.answer("📥 Начинаю обработку и загрузку учебника в базу данных...")
    position = await enqueue_ingest(
        pdf_path=tmp.name,
        filename=doc.file_name,
        user_id=user.id,
        title=data["title"],
        grade=data.get("grade"),
        author=data.get("author"),
        telegram_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=msg.message_id,)
    if position > 1:
        await msg.edit_text(f"""⏳ Ваш файл в очереди. Перед вами задач: {position - 1}.
Я пришлю сообщение, когда начну обработку.""")

    await enqueue_ingest(
        pdf_path=tmp.name,
        filename=doc.file_name,
        user_id=user.id,
        title=data["title"],
        grade=data.get("grade"),
        author=data.get("author"),
        telegram_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=msg.message_id,
    )

@router.message(F.text == "🧠 Сгенерировать урок")
async def lesson_start(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(telegram_id=message.from_user.id, username=message.from_user.username)

    async with async_session() as session:
        stmt = select(Document).where(Document.user_id == user.id)
        docs = (await session.execute(stmt)).scalars().all()

    if not docs:
        await message.answer(
            "У вас нет учебников. Нажмите «➕ Добавить учебник»."
        )
        return

    docs_data = [
        {"id": str(d.id), "title": d.title, "grade": d.grade}
        for d in docs
    ]
    await state.set_state(Lesson.waiting_doc)
    await message.answer(
        "По какому учебнику делать урок?",
        reply_markup=kb.documents_keyboard(docs_data),
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

    user = await db.get_or_create_user(telegram_id=message.from_user.id, username=message.from_user.username)

    await message.answer("🧠 Генерирую урок, подождите ~30 секунд...")

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