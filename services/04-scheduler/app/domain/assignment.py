"""assignments 테이블

PostgreSQL의 TEXT[] (reason_tags)는 SQLite 호환을 위해 JSON 배열로 매핑한다.
`team`·`degree`는 지원자 원본이 타 서비스 소유이므로 규칙 평가용으로 비정규화 저장.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, new_id
from app.domain.schedule import Schedule


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Assignment(Base):
    __tablename__ = "assignments"

    assignment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.schedule_id"), index=True)
    applicant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    applicant_name: Mapped[str] = mapped_column(String(64), default="")
    interviewer_id: Mapped[str] = mapped_column(String(32), nullable=False)
    team: Mapped[str] = mapped_column(String(64), default="")
    degree: Mapped[str] = mapped_column(String(16), default="학사")
    day: Mapped[str] = mapped_column(String(4), nullable=False)
    hour: Mapped[str] = mapped_column(String(8), nullable=False)
    lock_level: Mapped[str] = mapped_column(String(16), default="DRAFT")
    reason_tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    schedule: Mapped[Schedule] = relationship(back_populates="assignments")

    __table_args__ = (
        Index("idx_assign_schedule", "schedule_id"),
        Index(
            "idx_assign_time_iv",
            "schedule_id",
            "interviewer_id",
            "day",
            "hour",
            unique=True,
        ),
    )
