import logging
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from config import settings
from database.database import async_session
from database.models import (
    ACTIVE_STATUSES,
    DOC_COMPLETED,
    DOC_FAILED,
    TIER_FREE,
    TIER_PRO,
    TIERS,
    AIModel,
    Broadcast,
    Chunk,
    Document,
    Lesson,
    User,
)


logger = logging.getLogger(__name__)


def _as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


# ─── Пользователи ────────────────────────────────────────────────────────────
async def get_or_create_user(telegram_id: int, username: str | None = None) -> User:
    """Находит пользователя по telegram_id или создаёт нового."""
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                is_admin=telegram_id in settings.admin_ids,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logger.info("Новый пользователь: %s (@%s)", telegram_id, username)
            return user

        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if telegram_id in settings.admin_ids and not user.is_admin:
            user.is_admin = True
            changed = True
        if not user.active_broadcast:
            # Пользователь снова написал боту (например, разблокировал его)
            user.active_broadcast = True
            changed = True
        if changed:
            await session.commit()
            await session.refresh(user)
        return user


async def is_admin(telegram_id: int) -> bool:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        return bool(user and user.is_admin)


async def is_user_blocked(telegram_id: int) -> bool:
    """Проверяет, заблокирован ли пользователь."""
    async with async_session() as session:
        stmt = select(User.is_blocked).where(User.telegram_id == telegram_id)
        value = (await session.execute(stmt)).scalar_one_or_none()
        return bool(value)


async def set_user_blocked(telegram_id: int, blocked: bool) -> User | None:
    """Блокирует или разблокирует пользователя. Возвращает обновлённую запись."""
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            return None
        user.is_blocked = blocked
        await session.commit()
        await session.refresh(user)
        return user


async def ensure_admins(admin_ids: list[int]) -> None:
    """Выставляет флаг is_admin существующим пользователям из ADMIN_IDS."""
    if not admin_ids:
        return
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id.in_(admin_ids))
        users = list((await session.execute(stmt)).scalars().all())
        changed = False
        for user in users:
            if not user.is_admin:
                user.is_admin = True
                changed = True
                logger.info("Выдан админский доступ: %s", user.telegram_id)
        if changed:
            await session.commit()


async def get_all_active_users() -> list[User]:
    async with async_session() as session:
        stmt = select(User).where(
            User.active_broadcast == True,  # noqa: E712
            User.is_blocked == False,  # noqa: E712
        )
        return list((await session.execute(stmt)).scalars().all())


async def get_admin_telegram_ids() -> list[int]:
    async with async_session() as session:
        stmt = select(User.telegram_id).where(User.is_admin == True)  # noqa: E712
        return [row[0] for row in (await session.execute(stmt)).all()]


async def deactivate_broadcast(telegram_id: int) -> None:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user and user.active_broadcast:
            user.active_broadcast = False
            await session.commit()


async def count_users() -> tuple[int, int]:
    async with async_session() as session:
        total = (await session.execute(select(func.count(User.id)))).scalar() or 0
        active = (
            await session.execute(
                select(func.count(User.id)).where(
                    User.active_broadcast == True,  # noqa: E712
                    User.is_blocked == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        return total, active


# ─── Нейросети и подписки ────────────────────────────────────────────────────
def tier_allows(model: AIModel, tier: str) -> bool:
    """Доступна ли модель на указанном уровне подписки."""
    return model.required_tier == TIER_FREE or tier == TIER_PRO


async def get_all_models() -> list[AIModel]:
    async with async_session() as session:
        stmt = select(AIModel).order_by(AIModel.sort_order, AIModel.id)
        return list((await session.execute(stmt)).scalars().all())


async def get_available_models(tier: str) -> list[AIModel]:
    """Активные модели, доступные пользователю с данным уровнем подписки."""
    async with async_session() as session:
        stmt = select(AIModel).where(AIModel.is_active == True)  # noqa: E712
        if tier != TIER_PRO:
            stmt = stmt.where(AIModel.required_tier == TIER_FREE)
        stmt = stmt.order_by(AIModel.sort_order, AIModel.id)
        return list((await session.execute(stmt)).scalars().all())


async def get_default_model() -> AIModel | None:
    async with async_session() as session:
        stmt = select(AIModel).where(
            AIModel.is_active == True,  # noqa: E712
            AIModel.is_default == True,  # noqa: E712
        )
        return (await session.execute(stmt)).scalars().first()


async def resolve_user_model(user: User) -> AIModel | None:
    """Выбирает модель для генерации: явный выбор → дефолт тарифа → базовая."""
    tier = user.subscription_tier or TIER_FREE
    async with async_session() as session:
        if user.selected_model_id:
            model = await session.get(AIModel, user.selected_model_id)
            if model and model.is_active and tier_allows(model, tier):
                return model

        tiers = (TIER_PRO, TIER_FREE) if tier == TIER_PRO else (TIER_FREE,)
        for required_tier in tiers:
            stmt = (
                select(AIModel)
                .where(
                    AIModel.is_active == True,  # noqa: E712
                    AIModel.required_tier == required_tier,
                )
                .order_by(
                    AIModel.is_default.desc(), AIModel.sort_order, AIModel.id
                )
            )
            model = (await session.execute(stmt)).scalars().first()
            if model is not None:
                return model
        return None


async def set_user_model(
    telegram_id: int, model_id: int
) -> tuple[str, AIModel | None]:
    """Сохраняет выбор модели. Возвращает (статус, модель)."""
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            return "user_not_found", None

        model = await session.get(AIModel, model_id)
        if model is None:
            return "model_not_found", None
        if not model.is_active or not tier_allows(
            model, user.subscription_tier or TIER_FREE
        ):
            return "forbidden", None

        user.selected_model_id = model.id
        await session.commit()
        return "ok", model


async def toggle_model_active(model_id: int) -> tuple[str, AIModel | None]:
    """Включает/выключает модель. Возвращает (статус, модель)."""
    async with async_session() as session:
        model = await session.get(AIModel, model_id)
        if model is None:
            return "not_found", None
        if model.is_active and model.is_default:
            return "is_default", model

        if model.is_active and model.required_tier == TIER_FREE:
            active_free = (
                await session.execute(
                    select(func.count(AIModel.id)).where(
                        AIModel.is_active == True,  # noqa: E712
                        AIModel.required_tier == TIER_FREE,
                    )
                )
            ).scalar() or 0
            if active_free <= 1:
                return "last_free", model

        model.is_active = not model.is_active
        await session.commit()
        await session.refresh(model)
        return "ok", model


async def set_default_model(model_id: int) -> tuple[str, AIModel | None]:
    """Назначает модель глобальной по умолчанию."""
    async with async_session() as session:
        model = await session.get(AIModel, model_id)
        if model is None:
            return "not_found", None
        if not model.is_active:
            return "not_active", model

        await session.execute(
            update(AIModel)
            .where(AIModel.is_default == True)  # noqa: E712
            .values(is_default=False)
        )
        model.is_default = True
        await session.commit()
        await session.refresh(model)
        return "ok", model


async def find_user_by_identifier(identifier: str) -> User | None:
    """Ищет пользователя по Telegram ID или @username."""
    value = identifier.strip()
    if value.startswith("@"):
        value = value[1:]
    if not value:
        return None

    async with async_session() as session:
        if value.lstrip("-").isdigit():
            stmt = select(User).where(User.telegram_id == int(value))
        else:
            stmt = select(User).where(func.lower(User.username) == value.lower())
        return (await session.execute(stmt)).scalar_one_or_none()


async def set_subscription_tier(telegram_id: int, tier: str) -> User | None:
    """Выдаёт или снимает подписку. Возвращает обновлённого пользователя."""
    if tier not in TIERS:
        raise ValueError(f"Неизвестный уровень подписки: {tier!r}")

    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            return None

        previous_tier = user.subscription_tier or TIER_FREE
        user.subscription_tier = tier
        if tier == TIER_FREE:
            user.pro_until = None
            if user.selected_model_id is not None:
                model = await session.get(AIModel, user.selected_model_id)
                if model is not None and not tier_allows(model, tier):
                    user.selected_model_id = None
        elif previous_tier != TIER_PRO and user.selected_model_id is not None:
            # При первой выдаче Pro сразу переключаем на модель Pro,
            # если была выбрана базовая.
            model = await session.get(AIModel, user.selected_model_id)
            if model is not None and model.required_tier == TIER_FREE:
                user.selected_model_id = None

        await session.commit()
        await session.refresh(user)
        return user


# ─── Учебники ────────────────────────────────────────────────────────────────
async def create_document(
    *,
    document_id: uuid.UUID,
    user_id: int,
    title: str,
    grade: str | None,
    author: str | None,
    filename: str,
    source_file: str,
) -> Document:
    async with async_session() as session:
        document = Document(
            id=document_id,
            user_id=user_id,
            title=title,
            grade=grade,
            author=author,
            filename=filename,
            source_file=source_file,
            status="pending",
            is_completed=False,
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)
        return document


async def update_document_notify(
    document_id: uuid.UUID, chat_id: int, message_id: int
) -> None:
    async with async_session() as session:
        document = await session.get(Document, _as_uuid(document_id))
        if document is None:
            return
        document.notify_chat_id = chat_id
        document.notify_message_id = message_id
        await session.commit()


async def set_document_status(
    document_id: uuid.UUID,
    status: str,
    error_message: str | None = None,
) -> None:
    async with async_session() as session:
        document = await session.get(Document, _as_uuid(document_id))
        if document is None:
            return
        document.status = status
        document.is_completed = status == DOC_COMPLETED
        if status == DOC_COMPLETED:
            document.error_message = None
        elif error_message is not None:
            document.error_message = error_message[:1000]
        await session.commit()


async def mark_document_failed(
    document_id: uuid.UUID, error_message: str
) -> None:
    await set_document_status(document_id, DOC_FAILED, error_message)


async def get_user_documents(user_id: int) -> list[Document]:
    async with async_session() as session:
        stmt = (
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.uploaded_at.desc())
        )
        return list((await session.execute(stmt)).scalars().all())


async def get_user_documents_with_stats(user_id: int) -> list[dict[str, Any]]:
    """Список учебников пользователя с количеством страниц и фрагментов."""
    async with async_session() as session:
        stmt = (
            select(
                Document,
                func.count(Chunk.id).label("chunks_count"),
                func.count(func.distinct(Chunk.page_number)).label("pages_count"),
            )
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .where(Document.user_id == user_id)
            .group_by(Document.id)
            .order_by(Document.uploaded_at.desc())
        )
        rows = (await session.execute(stmt)).all()

    result: list[dict[str, Any]] = []
    for document, chunks_count, pages_count in rows:
        result.append(
            {
                "id": document.id,
                "title": document.title,
                "grade": document.grade,
                "author": document.author,
                "filename": document.filename,
                "status": document.status,
                "is_completed": document.is_completed,
                "error_message": document.error_message,
                "uploaded_at": document.uploaded_at,
                "chunks": int(chunks_count or 0),
                "pages": int(pages_count or 0),
            }
        )
    return result


async def get_user_document(
    document_id: str | uuid.UUID, user_id: int
) -> Document | None:
    uid = _as_uuid(document_id)
    if uid is None:
        return None
    async with async_session() as session:
        stmt = select(Document).where(
            Document.id == uid, Document.user_id == user_id
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def get_document(document_id: str | uuid.UUID) -> Document | None:
    uid = _as_uuid(document_id)
    if uid is None:
        return None
    async with async_session() as session:
        return await session.get(Document, uid)


async def delete_document(
    document_id: str | uuid.UUID, user_id: int
) -> tuple[str, str | None, str | None]:
    """Удаляет учебник.

    Возвращает (результат, title, source_file), где результат —
    deleted / not_found / busy.
    """
    uid = _as_uuid(document_id)
    if uid is None:
        return "not_found", None, None

    async with async_session() as session:
        stmt = select(Document).where(
            Document.id == uid, Document.user_id == user_id
        )
        document = (await session.execute(stmt)).scalar_one_or_none()
        if document is None:
            return "not_found", None, None
        if document.status in ACTIVE_STATUSES:
            return "busy", document.title, document.source_file

        title = document.title
        source_file = document.source_file
        await session.delete(document)
        await session.commit()
        logger.info("Учебник удалён: %s (%s)", title, uid)
        return "deleted", title, source_file


# ─── Уроки ───────────────────────────────────────────────────────────────────
async def save_lesson(
    *,
    user_id: int,
    document_id: str | uuid.UUID | None,
    document_title: str | None,
    topic: str,
    content: str,
    sources: str | None,
    duration_ms: int | None,
) -> Lesson | None:
    uid = _as_uuid(document_id)

    async with async_session() as session:
        if uid is not None and await session.get(Document, uid) is None:
            uid = None

        lesson = Lesson(
            user_id=user_id,
            document_id=uid,
            document_title=document_title,
            topic=topic,
            content=content,
            sources=sources,
            duration_ms=duration_ms,
        )
        session.add(lesson)
        try:
            await session.commit()
        except IntegrityError:
            # Документ удалили между проверкой и вставкой — сохраняем без ссылки
            await session.rollback()
            lesson = Lesson(
                user_id=user_id,
                document_id=None,
                document_title=document_title,
                topic=topic,
                content=content,
                sources=sources,
                duration_ms=duration_ms,
            )
            session.add(lesson)
            await session.commit()
        await session.refresh(lesson)
        return lesson


async def get_user_lesson(lesson_id: int, user_id: int) -> Lesson | None:
    """Возвращает урок, принадлежащий пользователю."""
    async with async_session() as session:
        stmt = select(Lesson).where(
            Lesson.id == lesson_id, Lesson.user_id == user_id
        )
        return (await session.execute(stmt)).scalar_one_or_none()


# ─── Статистика и рассылки ───────────────────────────────────────────────────
async def get_admin_stats() -> dict[str, int]:
    async with async_session() as session:
        total_users = (
            await session.execute(select(func.count(User.id)))
        ).scalar() or 0
        active_users = (
            await session.execute(
                select(func.count(User.id)).where(
                    User.active_broadcast == True,  # noqa: E712
                    User.is_blocked == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        documents = (
            await session.execute(select(func.count(Document.id)))
        ).scalar() or 0
        completed_documents = (
            await session.execute(
                select(func.count(Document.id)).where(
                    Document.status == DOC_COMPLETED
                )
            )
        ).scalar() or 0
        chunks = (
            await session.execute(select(func.count(Chunk.id)))
        ).scalar() or 0
        lessons = (
            await session.execute(select(func.count(Lesson.id)))
        ).scalar() or 0
        pro_users = (
            await session.execute(
                select(func.count(User.id)).where(User.subscription_tier == TIER_PRO)
            )
        ).scalar() or 0
        blocked_users = (
            await session.execute(
                select(func.count(User.id)).where(
                    User.is_blocked == True  # noqa: E712
                )
            )
        ).scalar() or 0

    return {
        "total_users": int(total_users),
        "active_users": int(active_users),
        "pro_users": int(pro_users),
        "blocked_users": int(blocked_users),
        "documents": int(documents),
        "completed_documents": int(completed_documents),
        "chunks": int(chunks),
        "lessons": int(lessons),
    }


async def log_broadcast(
    *,
    message: str,
    sent_count: int,
    failed_count: int,
    admin_telegram_id: int,
) -> None:
    async with async_session() as session:
        session.add(
            Broadcast(
                message=message,
                sent_count=sent_count,
                failed_count=failed_count,
                admin_telegram_id=admin_telegram_id,
            )
        )
        await session.commit()


# ─── Воркер ──────────────────────────────────────────────────────────────────
def _document_payload(document: Document, telegram_id: int) -> dict[str, Any]:
    return {
        "id": document.id,
        "title": document.title,
        "status": document.status,
        "source_file": document.source_file,
        "notify_chat_id": document.notify_chat_id,
        "notify_message_id": document.notify_message_id,
        "telegram_id": telegram_id,
    }


async def get_document_with_user(
    document_id: str | uuid.UUID,
) -> dict[str, Any] | None:
    uid = _as_uuid(document_id)
    if uid is None:
        return None
    async with async_session() as session:
        stmt = (
            select(Document, User.telegram_id)
            .join(User, User.id == Document.user_id)
            .where(Document.id == uid)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return None
        document, telegram_id = row
        return _document_payload(document, telegram_id)


async def get_documents_for_recovery() -> list[dict[str, Any]]:
    async with async_session() as session:
        stmt = (
            select(Document, User.telegram_id)
            .join(User, User.id == Document.user_id)
            .where(Document.status.in_(ACTIVE_STATUSES))
        )
        rows = (await session.execute(stmt)).all()
    return [_document_payload(document, telegram_id) for document, telegram_id in rows]
