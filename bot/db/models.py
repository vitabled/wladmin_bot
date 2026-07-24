"""SQLAlchemy models (multi-tenant chat administration).

Настройки-энумы хранятся строками (расширяемость последующих фаз без
миграций типов). Все временные метки — timezone-aware, server_default=now().
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Chat(Base):
    __tablename__ = "chats"

    # Telegram-assigned id — provided explicitly, never auto-incremented.
    chat_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    title: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(50))
    language: Mapped[str] = mapped_column(String(10), default="ru")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    settings: Mapped[ChatSettings] = relationship(
        back_populates="chat",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    warns: Mapped[list[Warn]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    stopwords: Mapped[list[Stopword]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    triggers: Mapped[list[Trigger]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    mod_logs: Mapped[list[ModLog]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class ChatSettings(Base):
    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.chat_id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Welcome
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    welcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    delete_service_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_welcome_after: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Captcha
    captcha_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_type: Mapped[str] = mapped_column(String(20), default="button")
    captcha_timeout: Mapped[int] = mapped_column(Integer, default=300)
    captcha_fail_action: Mapped[str] = mapped_column(String(20), default="kick")

    # Warns
    warn_limit: Mapped[int] = mapped_column(Integer, default=3)
    warn_action: Mapped[str] = mapped_column(String(20), default="mute")
    warn_action_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Antispam
    filter_links: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_forwards: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_stopwords: Mapped[bool] = mapped_column(Boolean, default=False)
    antispam_action: Mapped[str] = mapped_column(String(20), default="delete")
    antispam_exempt_admins: Mapped[bool] = mapped_column(Boolean, default=True)

    # Anti-flood (Phase 2)
    antiflood_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    antiflood_limit: Mapped[int] = mapped_column(Integer, default=5)
    antiflood_window: Mapped[int] = mapped_column(Integer, default=5)
    antiflood_action: Mapped[str] = mapped_column(String(20), default="mute")

    # Newbie media restriction (Phase 2)
    newbie_media_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    newbie_period: Mapped[int] = mapped_column(Integer, default=3600)

    # Triggers / auto-replies (Phase 3)
    triggers_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Statistics (Phase 4)
    stats_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    chat: Mapped[Chat] = relationship(back_populates="settings")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    first_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Warn(Base):
    __tablename__ = "warns"
    __table_args__ = (
        Index("ix_warns_chat_user_active", "chat_id", "user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    chat: Mapped[Chat] = relationship(back_populates="warns")


class Stopword(Base):
    __tablename__ = "stopwords"
    __table_args__ = (
        UniqueConstraint("chat_id", "word", name="uq_stopwords_chat_word"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE")
    )
    word: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chat: Mapped[Chat] = relationship(back_populates="stopwords")


class Trigger(Base):
    __tablename__ = "triggers"
    __table_args__ = (
        UniqueConstraint("chat_id", "pattern", name="uq_triggers_chat_pattern"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE")
    )
    pattern: Mapped[str] = mapped_column(String(255))
    match_type: Mapped[str] = mapped_column(String(20), default="contains")
    reply_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chat: Mapped[Chat] = relationship(back_populates="triggers")


class Activity(Base):
    """Per-user message activity within a chat (Phase 4)."""

    __tablename__ = "activity"
    __table_args__ = (Index("ix_activity_chat_count", "chat_id", "message_count"),)

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScheduledPost(Base):
    """A one-off or recurring message the bot posts to a chat (Phase 5)."""

    __tablename__ = "scheduled_posts"
    __table_args__ = (Index("ix_sched_enabled_run", "enabled", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(Text)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NULL interval => one-off; otherwise seconds between recurrences.
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModLog(Base):
    __tablename__ = "mod_log"
    __table_args__ = (Index("ix_mod_log_chat_created", "chat_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE")
    )
    actor_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chat: Mapped[Chat] = relationship(back_populates="mod_logs")
