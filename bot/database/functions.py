from sqlalchemy import func, select

from database.database import async_session
from database.models import User, Document


# Находит юзера по telegram_id или создаёт нового.
async def get_or_create_user(telegram_id: int, username: str | None = None) -> User:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        return user

# Возвращает все учебники пользователя.
async def get_user_documents(user_id: int) -> list[Document]:
    async with async_session() as session:
        stmt = select(Document).where(Document.user_id == user_id)
        return list((await session.execute(stmt)).scalars().all())

# Возвращает учебник по id.
async def get_document(document_id: str) -> Document | None:
    async with async_session() as session:
        stmt = select(Document).where(Document.id == document_id)
        return (await session.execute(stmt)).scalar_one_or_none()

async def is_admin(telegram_id: int) -> bool:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        return bool(user and user.admin)

async def get_all_active_users() -> list[User]:
    async with async_session() as session:
        stmt = select(User).where(User.active_broadcast == True)  # noqa: E712
        return list((await session.execute(stmt)).scalars().all())

async def count_users() -> tuple[int, int]:
    async with async_session() as session:
        total = (await session.execute(select(func.count(User.id)))).scalar() or 0
        active = (
            await session.execute(
                select(func.count(User.id)).where(User.active_broadcast == True)  # noqa: E712
            )
        ).scalar() or 0
        return total, active