"""responses 테이블 — 면접위원 폼 응답"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, new_uuid


class Response(Base):
    __tablename__ = "responses"

    response_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    invitee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("invitees.invitee_id"), unique=True, nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    invitee: Mapped["Invitee"] = relationship(back_populates="response")  # noqa: F821

    @property
    def slot_count(self) -> int:
        return len(self.payload.get("available_slots", []) or [])
