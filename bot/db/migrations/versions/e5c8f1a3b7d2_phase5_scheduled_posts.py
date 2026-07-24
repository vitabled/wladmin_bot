"""phase 5: scheduled posts

Revision ID: e5c8f1a3b7d2
Revises: d4b7e2a6c9f1
Create Date: 2026-07-24 00:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "e5c8f1a3b7d2"
down_revision = "d4b7e2a6c9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sched_enabled_run", "scheduled_posts", ["enabled", "run_at"])


def downgrade() -> None:
    op.drop_index("ix_sched_enabled_run", table_name="scheduled_posts")
    op.drop_table("scheduled_posts")
