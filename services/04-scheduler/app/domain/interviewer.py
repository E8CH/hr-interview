"""interviewers 테이블 (가용성 포함)"""
from __future__ import annotations

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base


class Interviewer(Base):
    __tablename__ = "interviewers"

    interviewer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(32), default="", server_default="")  # 직급
    team: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    max_daily: Mapped[int] = mapped_column(Integer, default=6)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1=리더, 2=실무
    email: Mapped[str] = mapped_column(String(255), default="")
    backup_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    availability: Mapped[dict] = mapped_column(JSON, default=dict)  # {"1일차": ["1타임", ...]}
