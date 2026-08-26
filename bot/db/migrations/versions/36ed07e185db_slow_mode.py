"""slow mode: per-chat slow-mode persistence

Revision ID: 36ed07e185db
Revises: d5a7b3c9e1f2
Create Date: 2026-08-26 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "36ed07e185db"
down_revision = "d5a7b3c9e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slow_mode",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "regular_seconds",
            sa.Integer(),
            server_default=sa.text("21600"),
            nullable=False,
        ),
        sa.Column(
            "wl_seconds",
            sa.Integer(),
            server_default=sa.text("10800"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("slow_mode")
