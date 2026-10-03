"""slow mode: punishment settings for violations (warn text, count, action, duration)

Revision ID: b7c2f4a90e13
Revises: a4b8c2e6d9f1
Create Date: 2026-10-03 12:00:00.000000

Chat-level (``chat_settings``), not per topic: the punishment is one policy for
the group and must stay editable while the slow-mode rule itself is off.
``sm_warn_limit == 0`` disables punishing; ``sm_punish_duration`` NULL means
«forever». Punishing ships OFF (``sm_warn_limit = 0``): the bot serves other
groups too, and a default count would start muting their members the moment this
code lands. A group opts in by picking a number on the screen; the action defaults
to a 1-hour mute, never a ban.
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c2f4a90e13"
down_revision = "a4b8c2e6d9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_column("chat_settings", "sm_punish_duration")
    op.drop_column("chat_settings", "sm_punish_action")
    op.drop_column("chat_settings", "sm_warn_limit")
    op.drop_column("chat_settings", "sm_warn_text")
