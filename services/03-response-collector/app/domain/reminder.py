"""reminders 테이블 — 3단계 리마인더 발송 이력"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, new_uuid
from app.timeutil import utcnow


class Reminder(Base):
    __tablename__ = "reminders"

    reminder_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    invitee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("invitees.invitee_id"), nullable=False, index=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default="email", nullable=False)
    cc_supervisor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    invitee: Mapped["Invitee"] = relationship(back_populates="reminders")  # noqa: F821
