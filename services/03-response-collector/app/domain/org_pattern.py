"""org_patterns 테이블 — 조직별 응답 패턴 학습 결과"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base
from app.timeutil import utcnow

# 명세: predicted_slow = mean_hours > 40
SLOW_THRESHOLD_HOURS = 40.0


class OrgPattern(Base):
    __tablename__ = "org_patterns"

    org: Mapped[str] = mapped_column(String(64), primary_key=True)
    mean_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    std_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    predicted_slow: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
