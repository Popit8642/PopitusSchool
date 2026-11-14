"""refactor schema: is_admin, document statuses, lessons, broadcast stats

Revision ID: a1b2c3d4e5f6
Revises: 0f64d1c1e608
Create Date: 2026-11-14 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0f64d1c1e608"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users.admin -> users.is_admin
    op.alter_column(
        "users",
        "admin",
        new_column_name="is_admin",
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )

    # Документы: расширенный статус, файл-источник, уведомления
    op.add_column(
        "documents",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("source_file", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("notify_chat_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("notify_message_id", sa.BigInteger(), nullable=True),
    )
    op.execute(
        "UPDATE documents SET status = CASE "
        "WHEN is_completed THEN 'completed' ELSE 'failed' END"
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])

    # История успешно сгенерированных уроков
    op.create_table(
        "lessons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("document_title", sa.String(length=255), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lessons_user_id", "lessons", ["user_id"])
    op.create_index("ix_lessons_document_id", "lessons", ["document_id"])
    op.create_index("ix_lessons_created_at", "lessons", ["created_at"])

    # Аудит рассылок
    op.add_column(
        "broadcasts",
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "broadcasts",
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "broadcasts",
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broadcasts", "admin_telegram_id")
    op.drop_column("broadcasts", "failed_count")
    op.drop_column("broadcasts", "sent_count")

    op.drop_index("ix_lessons_created_at", table_name="lessons")
    op.drop_index("ix_lessons_document_id", table_name="lessons")
    op.drop_index("ix_lessons_user_id", table_name="lessons")
    op.drop_table("lessons")

    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_column("documents", "notify_message_id")
    op.drop_column("documents", "notify_chat_id")
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "source_file")
    op.drop_column("documents", "status")

    op.alter_column(
        "users",
        "is_admin",
        new_column_name="admin",
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
