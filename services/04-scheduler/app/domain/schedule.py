"""schedules · rule_compliance 테이블"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, new_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Schedule(Base):
    __tablename__ = "schedules"

    schedule_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    round_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    algorithm: Mapped[str] = mapped_column(String(32), default="v5")
    total_assigned: Mapped[int] = mapped_column(Integer, default=0)
    total_applicants: Mapped[int] = mapped_column(Integer, default=0)
    coverage_pct: Mapped[float] = mapped_column(Float, default=0.0)
    hard_violations: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    elapsed_ms: Mapped[float] = mapped_column(Float, default=0.0)
    generated_at: Mapped[datetime] = mapped_column(default=_utcnow)
    generated_by: Mapped[str] = mapped_column(String(64), default="system")

    assignments: Mapped[list["Assignment"]] = relationship(  # noqa: F821
        back_populates="schedule", cascade="all, delete-orphan", lazy="selectin"
    )
    rules: Mapped[list["RuleCompliance"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan", lazy="selectin"
    )


class RuleCompliance(Base):
    __tablename__ = "rule_compliance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.schedule_id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    measured_at: Mapped[datetime] = mapped_column(default=_utcnow)

    schedule: Mapped[Schedule] = relationship(back_populates="rules")
