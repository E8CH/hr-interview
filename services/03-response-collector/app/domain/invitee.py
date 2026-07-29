"""invitees 테이블 — 초대받은 면접위원 (폼 접근 토큰 보유)"""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, new_uuid


def new_token() -> str:
    """폼 접근 토큰 (URL-safe, 추측 불가)."""
    return secrets.token_urlsafe(32)


class Invitee(Base):
    __tablename__ = "invitees"

    invitee_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("requests.request_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    team: Mapped[str] = mapped_column(String(64), nullable=False)
    org: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dept_leader_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=new_token, index=True
    )
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reminder_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    request: Mapped["Request"] = relationship(back_populates="invitees")  # noqa: F821
    response: Mapped["Response | None"] = relationship(  # noqa: F821
        back_populates="invitee",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    reminders: Mapped[list["Reminder"]] = relationship(  # noqa: F821
        back_populates="invitee",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def sent_at(self) -> datetime | None:
        """리마인더 계산 기준 시각 = 소속 요청의 발송 시각."""
        return self.request.sent_at if self.request else None

    @property
    def has_responded(self) -> bool:
        return self.response is not None
