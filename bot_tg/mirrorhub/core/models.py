
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from mirrorhub.core.db import Base

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(Text, nullable=True)

class Token(Base):
    __tablename__ = "tokens"
    id = Column(Integer, primary_key=True)
    token = Column(String(128), unique=True, nullable=False)
    status = Column(String(16), nullable=False, default="free")
    created_at = Column(DateTime, server_default=func.now())
    note = Column(String(255), nullable=True)

class BotInstance(Base):
    __tablename__ = "bots"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id", ondelete="SET NULL"))
    token = relationship("Token")
    username = Column(String(64), nullable=True)
    link = Column(String(128), nullable=True)
    is_running = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    last_error = Column(Text, nullable=True)

    starts = Column(Integer, default=0)
    contacts_clicks = Column(Integer, default=0)

class BotUser(Base):
    __tablename__ = "bot_users"
    id = Column(Integer, primary_key=True)
    bot_id = Column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer, index=True)
    username = Column(String(64), nullable=True)
    first_seen = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint('bot_id', 'user_id', name='uix_bot_user'),)

class SentMessage(Base):
    __tablename__ = "sent_messages"
    id = Column(Integer, primary_key=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False, index=True)
    chat_id = Column(Integer, nullable=False, index=True)
    message_id = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False, index=True)
    created_at = Column(
        DateTime,
        server_default=func.current_timestamp(),
        nullable=False,
    )

class BroadcastLog(Base):
    __tablename__ = "broadcasts"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
    text = Column(Text, nullable=True)
    photo_path = Column(String(512), nullable=True)
    total = Column(Integer, default=0)
    ok = Column(Integer, default=0)
    fail = Column(Integer, default=0)
