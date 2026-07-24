"""phase 2: anti-flood + newbie media settings

Revision ID: b2f1a9c7d3e5
Revises: 8a5fa6d64dc3
Create Date: 2026-07-24 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b2f1a9c7d3e5"
down_revision = "8a5fa6d64dc3"
branch_labels = None
depends_on = None


# New columns carry a server_default so the ALTER succeeds on tables that
# already hold rows; the ORM supplies the same defaults on fresh inserts.
_COLUMNS = (
    ("antiflood_enabled", sa.Boolean(), sa.text("false")),
    ("antiflood_limit", sa.Integer(), sa.text("5")),
    ("antiflood_window", sa.Integer(), sa.text("5")),
    ("antiflood_action", sa.String(length=20), sa.text("'mute'")),
    ("newbie_media_enabled", sa.Boolean(), sa.text("false")),
    ("newbie_period", sa.Integer(), sa.text("3600")),
)


def upgrade() -> None:
    for name, type_, default in _COLUMNS:
        op.add_column(
            "chat_settings",
            sa.Column(name, type_, nullable=False, server_default=default),
        )


def downgrade() -> None:
    for name, _type, _default in reversed(_COLUMNS):
        op.drop_column("chat_settings", name)
