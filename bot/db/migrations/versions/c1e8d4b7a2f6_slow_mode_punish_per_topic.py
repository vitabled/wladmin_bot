"""slow mode: move the punishment for violations onto each topic row

Revision ID: c1e8d4b7a2f6
Revises: b7c2f4a90e13
Create Date: 2026-10-03 15:30:00.000000

The owner asked for the punishment to live in the settings of EACH topic
(«пункт наказания за нарушения должен быть в настройках медленного режима каждой
конкретной ветки»), so the four columns move from ``chat_settings`` to
``slow_mode_topics`` — the row that already carries that topic's own limit.

``punish_limit == 0`` keeps punishing OFF (the bot serves other groups too: a
default count would start muting their members the moment this code lands), and
``punish_duration`` NULL means «forever». The dropped chat-level columns were
never editable anywhere but the screen this commit removes, and every group sat
at their defaults, so the DROP loses no configuration.
"""

import sqlalchemy as sa
from alembic import op

revision = "c1e8d4b7a2f6"
down_revision = "b7c2f4a90e13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "slow_mode_topics",
        sa.Column("punish_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "slow_mode_topics",
        sa.Column(
            "punish_limit", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "slow_mode_topics",
        sa.Column(
            "punish_action",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'mute'"),
        ),
    )
    op.add_column(
        "slow_mode_topics",
        sa.Column(
            "punish_duration",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("3600"),
        ),
    )
    op.drop_column("chat_settings", "sm_punish_duration")
    op.drop_column("chat_settings", "sm_punish_action")
    op.drop_column("chat_settings", "sm_warn_limit")
    op.drop_column("chat_settings", "sm_warn_text")


def downgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("sm_warn_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "sm_warn_limit", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "sm_punish_action",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'mute'"),
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "sm_punish_duration",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("3600"),
        ),
    )
    op.drop_column("slow_mode_topics", "punish_duration")
    op.drop_column("slow_mode_topics", "punish_action")
    op.drop_column("slow_mode_topics", "punish_limit")
    op.drop_column("slow_mode_topics", "punish_text")
