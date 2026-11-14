"""add ai_models table and user subscription fields

Revision ID: b7f3c2a91d54
Revises: a1b2c3d4e5f6
Create Date: 2026-11-14 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7f3c2a91d54"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("ollama_tag", sa.String(length=128), nullable=False),
        sa.Column(
            "required_tier",
            sa.String(length=8),
            nullable=False,
            server_default="free",
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_models_name", "ai_models", ["name"], unique=True)
    # Не больше одной модели по умолчанию
    op.create_index(
        "uq_ai_models_single_default",
        "ai_models",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.add_column(
        "users",
        sa.Column(
            "subscription_tier",
            sa.String(length=8),
            nullable=False,
            server_default="free",
        ),
    )
    op.add_column(
        "users",
        sa.Column("selected_model_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("pro_until", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_selected_model_id_ai_models",
        "users",
        "ai_models",
        ["selected_model_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_users_selected_model_id", "users", ["selected_model_id"], unique=False
    )

    ai_models = sa.table(
        "ai_models",
        sa.column("name", sa.String),
        sa.column("ollama_tag", sa.String),
        sa.column("required_tier", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("is_default", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        ai_models,
        [
            {
                "name": "PopitusGPT1.0v",
                "ollama_tag": "qwen2.5:3b-instruct-q4_K_M",
                "required_tier": "free",
                "is_active": True,
                "is_default": True,
                "sort_order": 0,
            },
            {
                "name": "PopitusGPT1.5v-Pro",
                "ollama_tag": "qwen3:4b-instruct-2507-q4_K_M",
                "required_tier": "pro",
                "is_active": True,
                "is_default": False,
                "sort_order": 10,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_users_selected_model_id", table_name="users")
    op.drop_constraint(
        "fk_users_selected_model_id_ai_models", "users", type_="foreignkey"
    )
    op.drop_column("users", "pro_until")
    op.drop_column("users", "selected_model_id")
    op.drop_column("users", "subscription_tier")

    op.drop_index("uq_ai_models_single_default", table_name="ai_models")
    op.drop_index("ix_ai_models_name", table_name="ai_models")
    op.drop_table("ai_models")
