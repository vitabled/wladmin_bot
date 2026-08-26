"""slow mode topics: per-topic scope for the slow-mode rule

Revision ID: c9d3e5f1a7b2
Revises: 7fbe101fe091
Create Date: 2026-08-26 14:00:00.000000


"""
import sqlalchemy as sa
from alembic import op

revision = "c9d3e5f1a7b2"
down_revision = "7fbe101fe091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "slow_mode",
        sa.Column("topic_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("slow_mode", "topic_ids")
