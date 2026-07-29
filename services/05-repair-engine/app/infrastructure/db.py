"""SQLAlchemy 2.x + SQLite (PoC)

명세의 PostgreSQL DDL 을 SQLite 로 대응:
  UUID  -> String(36)
  JSONB -> JSON
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (JSON, DateTime, ForeignKey, Integer, String,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid4())


class RepairEventRow(Base):
    __tablename__ = "repair_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    round_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 감사 로그 조회
    schedule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32))
    trigger_target: Mapped[str] = mapped_column(String(64))
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reported_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    correlation_id: Mapped[str] = mapped_column(String(36), default=_uuid)
    affected: Mapped[dict] = mapped_column(JSON, default=dict)


class RepairPlanRow(Base):
    __tablename__ = "repair_plans"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_events.event_id"),
                                          index=True)
    plan_type: Mapped[str] = mapped_column(String(16))
    rebooked_count: Mapped[int] = mapped_column(Integer, default=0)
    deferred_count: Mapped[int] = mapped_column(Integer, default=0)
    hard_violations: Mapped[int] = mapped_column(Integer, default=0)
    soft_penalty: Mapped[int] = mapped_column(Integer, default=0)
    plan_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SelectedPlanRow(Base):
    __tablename__ = "selected_plans"

    selection_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_events.event_id"),
                                          index=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_plans.plan_id"))
    selected_by: Mapped[str] = mapped_column(String(64))
    selected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    affected_count: Mapped[int] = mapped_column(Integer, default=0)


class LockMapRow(Base):
    __tablename__ = "lock_map"

    schedule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    applicant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lock_level: Mapped[str] = mapped_column(String(16), default="DRAFT")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScheduleSnapshotRow(Base):
    """Service 04 시간표의 로컬 사본.

    Plan 적용 결과(재예약/이월)를 반영해 두어야 연속 재편성과 감사 추적이 가능하다.
    """
    __tablename__ = "schedule_snapshots"

    schedule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    round_id: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """FastAPI 의존성 — 요청당 세션 1개"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
