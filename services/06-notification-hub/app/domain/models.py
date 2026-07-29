"""SQLAlchemy 모델 — 명세 06 의 테이블 스키마를 SQLite 호환으로 매핑

PostgreSQL 원본 → SQLite 매핑
  UUID       → VARCHAR(36) (uuid4 문자열)
  TEXT[]     → JSON 배열
  JSONB      → JSON
  TIMESTAMPTZ→ DateTime (naive UTC 저장)
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """naive UTC — SQLite 비교 연산 단순화를 위해 tzinfo 제거."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Template(Base):
    __tablename__ = "templates"

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "channel": self.channel,
            "subject": self.subject,
            "body": self.body,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    cc: Mapped[list | None] = mapped_column(JSON, default=list)
    context: Mapped[dict | None] = mapped_column(JSON, default=dict)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    round_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # 내부 확장: 재시도 백오프 스케줄링용 (다음 발송 시도 시각)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("idx_notif_status", "status", "created_at"),
        Index("idx_notif_correlation", "correlation_id"),
    )

    def to_dict(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "template_id": self.template_id,
            "channel": self.channel,
            "recipient": self.recipient,
            "cc": self.cc or [],
            "context": self.context or {},
            "subject": self.subject,
            "body": self.body,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "error_message": self.error_message,
            "correlation_id": self.correlation_id,
            "round_id": self.round_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Channel(Base):
    __tablename__ = "channels"

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "channel_type": self.channel_type,
            "config": self.config or {},
            "enabled": self.enabled,
        }


class DeadLetter(Base):
    """재시도 한도를 넘긴 발송 — dead letter 큐."""

    __tablename__ = "dead_letters"

    dead_letter_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )
    notification_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "dead_letter_id": self.dead_letter_id,
            "notification_id": self.notification_id,
            "channel": self.channel,
            "recipient": self.recipient,
            "attempt_count": self.attempt_count,
            "error_message": self.error_message,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
