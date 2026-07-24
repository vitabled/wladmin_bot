"""phase 3: triggers / auto-replies

Revision ID: c3a2b8e1f4d7
Revises: b2f1a9c7d3e5
Create Date: 2026-07-24 00:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c3a2b8e1f4d7"
down_revision = "b2f1a9c7d3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "triggers_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "triggers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("pattern", sa.String(length=255), nullable=False),
        sa.Column("match_type", sa.String(length=20), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "pattern", name="uq_triggers_chat_pattern"),
    )


def downgrade() -> None:
    op.drop_table("triggers")
    op.drop_column("chat_settings", "triggers_enabled")
