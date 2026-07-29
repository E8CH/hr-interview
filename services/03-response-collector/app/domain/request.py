"""requests 테이블 — 회차별 면접위원 초대 요청"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, new_uuid

STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"


class Request(Base):
    __tablename__ = "requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    round_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_ACTIVE, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), default=new_uuid, nullable=False)

    invitees: Mapped[list["Invitee"]] = relationship(  # noqa: F821
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_closed(self) -> bool:
        return self.status == STATUS_CLOSED
