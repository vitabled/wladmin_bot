"""staff members: bot-wide staff roles (Phase A)

Revision ID: f1a2b3c4d5e6
Revises: c9d3e5f1a7b2
Create Date: 2026-10-02 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "c9d3e5f1a7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_members",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("staff_members")
