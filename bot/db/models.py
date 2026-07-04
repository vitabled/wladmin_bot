from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(50))
    language: Mapped[str] = mapped_column(String(10), default="ru")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    settings: Mapped["ChatSettings"] = relationship(
        "ChatSettings", back_populates="chat", uselist=False, cascade="all, delete-orphan"
    )
    warns: Mapped[list["Warn"]] = relationship(
        "Warn", back_populates="chat", cascade="all, delete-orphan"
    )
    stopwords: Mapped[list["Stopword"]] = relationship(
        "Stopword", back_populates="chat", cascade="all, delete-orphan"
    )
    mod_logs: Mapped[list["ModLog"]] = relationship(
        "ModLog", back_populates="chat", cascade="all, delete-orphan"
    )


class ChatSettings(Base):
    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id"), primary_key=True
    )

    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    welcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    delete_service_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_welcome_after: Mapped[int | None] = mapped_column(Integer, nullable=True)

    captcha_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_type: Mapped[str] = mapped_column(String(20), default="button")
    captcha_timeout: Mapped[int] = mapped_column(Integer, default=300)
    captcha_fail_action: Mapped[str] = mapped_column(String(20), default="kick")

    warn_limit: Mapped[int] = mapped_column(Integer, default=3)
    warn_action: Mapped[str] = mapped_column(String(20), default="mute")
    warn_action_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    filter_links: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_forwards: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_stopwords: Mapped[bool] = mapped_column(Boolean, default=False)
    antispam_action: Mapped[str] = mapped_column(String(20), default="delete")
    antispam_exempt_admins: Mapped[bool] = mapped_column(Boolean, default=True)

    chat: Mapped[Chat] = relationship("Chat", back_populates="settings")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Warn(Base):
    __tablename__ = "warns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    chat: Mapped[Chat] = relationship("Chat", back_populates="warns")


class Stopword(Base):
    __tablename__ = "stopwords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"))
    word: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped[Chat] = relationship("Chat", back_populates="stopwords")


class ModLog(Base):
    __tablename__ = "mod_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped[Chat] = relationship("Chat", back_populates="mod_logs")
