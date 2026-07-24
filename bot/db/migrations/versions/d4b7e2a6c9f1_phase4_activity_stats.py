"""phase 4: activity statistics

Revision ID: d4b7e2a6c9f1
Revises: c3a2b8e1f4d7
Create Date: 2026-07-24 00:20:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d4b7e2a6c9f1"
down_revision = "c3a2b8e1f4d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "stats_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_table(
        "activity",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )
    op.create_index("ix_activity_chat_count", "activity", ["chat_id", "message_count"])


def downgrade() -> None:
    op.drop_index("ix_activity_chat_count", table_name="activity")
    op.drop_table("activity")
    op.drop_column("chat_settings", "stats_enabled")
