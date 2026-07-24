"""phase 8: federations (shared bans)

Revision ID: f6d9c4b2a8e3
Revises: e5c8f1a3b7d2
Create Date: 2026-07-24 00:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f6d9c4b2a8e3"
down_revision = "e5c8f1a3b7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "federations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_federations_name"),
    )
    op.create_table(
        "federation_chats",
        sa.Column("federation_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["federation_id"], ["federations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("federation_id", "chat_id"),
        sa.UniqueConstraint("chat_id", name="uq_federation_chats_chat"),
    )
    op.create_table(
        "federation_bans",
        sa.Column("federation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("banned_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["federation_id"], ["federations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("federation_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("federation_bans")
    op.drop_table("federation_chats")
    op.drop_table("federations")
