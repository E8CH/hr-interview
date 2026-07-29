"""DB 엔진 · 세션 · ORM 모델

명세(07)의 PostgreSQL DDL을 SQLAlchemy 2.x로 구현.
PoC는 SQLite이므로 타입을 다음과 같이 매핑한다.

    BIGSERIAL     -> Integer autoincrement PK
    UUID          -> String(36) (앱에서 uuid4 생성)
    JSONB         -> JSON
    TIMESTAMPTZ   -> DateTime (naive UTC로 정규화하여 저장)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    """naive UTC now — SQLite 저장/비교 일관성 확보용"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """tz-aware datetime을 naive UTC로 정규화.

    이벤트 봉투의 timestamp는 서비스마다 aware/naive가 섞일 수 있어
    저장 전 반드시 이 함수를 통과시킨다 (비교 시 TypeError 방지).
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class Base(DeclarativeBase):
    pass


class EventLog(Base):
    """모든 서비스의 이벤트를 append-only로 저장.

    UPDATE/DELETE는 리포지토리 계층에 메서드 자체를 두지 않는다.
    event_id UNIQUE 제약으로 재수신(at-least-once) 시 멱등 처리.
    """

    __tablename__ = "event_log"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    round_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    producer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("idx_event_type", "event_type", "timestamp"),
        Index("idx_event_round", "round_id", "timestamp"),
        Index("idx_event_correlation", "correlation_id"),
    )

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "round_id": self.round_id,
            "producer": self.producer,
            "correlation_id": self.correlation_id,
            "payload": self.payload or {},
        }


class KpiSnapshot(Base):
    """시점별 KPI 스냅샷 (append-only projection 결과)"""

    __tablename__ = "kpi_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    round_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metric_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    labels: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("idx_kpi_round_metric", "round_id", "metric_name", "captured_at"),
    )


class OrgResponseStat(Base):
    """조직별 응답 패턴 집계 (Service 03 데이터의 로컬 프로젝션)"""

    __tablename__ = "org_response_stats"

    round_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org: Mapped[str] = mapped_column(String(64), primary_key=True)
    mean_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_slow: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)

    # 집계 원자료 (평균 재계산용) — 명세 스키마 확장 컬럼
    invited_count: Mapped[int] = mapped_column(Integer, default=0)
    responded_count: Mapped[int] = mapped_column(Integer, default=0)
    total_hours: Mapped[float] = mapped_column(Float, default=0.0)


class Report(Base):
    """회차 리포트 캐시"""

    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    round_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    report_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --- 엔진 · 세션 ---------------------------------------------------------

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(url: str):
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # 이벤트 수집기(백그라운드 태스크)와 API가 스레드를 공유하므로 필요
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), expire_on_commit=False, future=True
        )
    return _SessionLocal


def init_db() -> None:
    """테이블 · 인덱스 생성 (PoC는 Alembic 대신 create_all)"""
    Base.metadata.create_all(bind=get_engine())


def reset_engine() -> None:
    """테스트에서 DATABASE_URL 변경 후 호출"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_scope() -> Iterator[Session]:
    """FastAPI 의존성 — 요청 단위 세션"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def new_session() -> Session:
    """백그라운드 태스크용 세션 (컨텍스트 매니저로 사용)"""
    return get_session_factory()()
