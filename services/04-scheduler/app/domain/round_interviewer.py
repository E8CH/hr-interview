"""round_interviewers 테이블 — 회차별로 실제 투입할 면접관 선별 결과.

면접관 마스터(interviewers)는 조직 전체 명단이고, 회차마다 그중 누구를 쓸지는
따로 정해진다. 그래서 선별을 별도 테이블로 둔다 — 마스터를 지우지 않고도
회차별로 다르게 뽑을 수 있어야 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RoundInterviewer(Base):
    __tablename__ = "round_interviewers"

    round_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    interviewer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    selected_by: Mapped[str] = mapped_column(String(64), default="console")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
