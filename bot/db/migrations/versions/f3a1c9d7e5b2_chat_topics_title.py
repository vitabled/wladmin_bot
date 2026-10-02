"""chat topics title: store the forum topic name seen in service messages

Revision ID: f3a1c9d7e5b2
Revises: c9d3e5f1a7b2
Create Date: 2026-10-02 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f3a1c9d7e5b2"
down_revision = "c9d3e5f1a7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_topics",
        sa.Column("title", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_topics", "title")
