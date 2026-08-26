"""chat topics: per-forum-topic tracking for broadcasts

Revision ID: 7fbe101fe091
Revises: 36ed07e185db
Create Date: 2026-08-26 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "7fbe101fe091"
down_revision = "36ed07e185db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "message_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "thread_id", name="uq_chat_topics_chat_thread"),
    )
    op.create_index("ix_chat_topics_chat_id", "chat_topics", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_topics_chat_id", table_name="chat_topics")
    op.drop_table("chat_topics")
