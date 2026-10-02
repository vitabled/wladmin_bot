"""slow mode topics: per-forum-topic slow-mode overrides

Revision ID: a4b8c2e6d9f1
Revises: f3a1c9d7e5b2
Create Date: 2026-10-02 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "a4b8c2e6d9f1"
down_revision = "f3a1c9d7e5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slow_mode_topics",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        # NULL = inherit the chat-level interval.
        sa.Column("regular_seconds", sa.Integer(), nullable=True),
        sa.Column("wl_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "thread_id"),
    )


def downgrade() -> None:
    op.drop_table("slow_mode_topics")
