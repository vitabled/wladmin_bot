"""phase 9: scam list (seller reputation)

Revision ID: d5a7b3c9e1f2
Revises: f6d9c4b2a8e3
Create Date: 2026-08-25 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d5a7b3c9e1f2"
down_revision = "f6d9c4b2a8e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scam_list",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_scam_list_user"),
    )
    op.create_index("ix_scam_list_user_id", "scam_list", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_scam_list_user_id", table_name="scam_list")
    op.drop_table("scam_list")
