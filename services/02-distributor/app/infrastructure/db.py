"""SQLAlchemy 2.x ORM · SQLite(PoC) 엔진 · 팀 프로필 시딩.

PostgreSQL의 TEXT[] 컬럼은 SQLite 호환을 위해 JSON으로 매핑한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings
from app.domain.profile import TeamProfile, seed_profiles


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TeamProfileORM(Base):
    __tablename__ = "team_profiles"

    team_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    primary_job: Mapped[list] = mapped_column(JSON, default=list)
    secondary_job: Mapped[list] = mapped_column(JSON, default=list)
    preferred_majors: Mapped[list] = mapped_column(JSON, default=list)
    org_allowed: Mapped[list] = mapped_column(JSON, default=list)
    grad_ratio_target: Mapped[float] = mapped_column(Float, default=0.30)
    target_headcount: Mapped[int] = mapped_column(Integer, nullable=False)
    special_tags: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def to_domain(self) -> TeamProfile:
        return TeamProfile(
            team_name=self.team_name,
            primary_job=list(self.primary_job or []),
            secondary_job=list(self.secondary_job or []),
            preferred_majors=list(self.preferred_majors or []),
            org_allowed=list(self.org_allowed or []),
            grad_ratio_target=self.grad_ratio_target,
            target_headcount=self.target_headcount,
            special_tags=list(self.special_tags or []),
            updated_at=self.updated_at,
        )


class DistributionPlanORM(Base):
    __tablename__ = "distribution_plans"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    round_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    master_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    total_applicants: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class AssignmentReasonORM(Base):
    __tablename__ = "assignment_reasons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("distribution_plans.plan_id"))
    applicant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    applicant_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_name: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    primary_team: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("idx_reasons_plan", AssignmentReasonORM.plan_id, AssignmentReasonORM.team_name)


_engines: dict[str, Engine] = {}
_sessionmakers: dict[str, sessionmaker] = {}


def get_engine() -> Engine:
    """DATABASE_URL별 엔진 캐시 (테스트에서 URL을 바꾸면 새 엔진)."""
    url = settings.database_url
    if url not in _engines:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engines[url] = create_engine(url, connect_args=connect_args, future=True)
        _sessionmakers[url] = sessionmaker(bind=_engines[url], expire_on_commit=False, future=True)
    return _engines[url]


def get_sessionmaker() -> sessionmaker:
    get_engine()
    return _sessionmakers[settings.database_url]


def new_session() -> Session:
    return get_sessionmaker()()


def db_session() -> Iterator[Session]:
    """FastAPI 의존성."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """테이블 생성 + 5개 팀 프로필 시딩 (멱등)."""
    Base.metadata.create_all(get_engine())
    with new_session() as session:
        existing = set(session.scalars(select(TeamProfileORM.team_name)).all())
        for profile in seed_profiles():
            if profile.team_name in existing:
                continue
            session.add(
                TeamProfileORM(
                    team_name=profile.team_name,
                    primary_job=profile.primary_job,
                    secondary_job=profile.secondary_job,
                    preferred_majors=profile.preferred_majors,
                    org_allowed=profile.org_allowed,
                    grad_ratio_target=profile.grad_ratio_target,
                    target_headcount=profile.target_headcount,
                    special_tags=profile.special_tags,
                )
            )
        session.commit()


def load_profiles(session: Session) -> list[TeamProfile]:
    rows = session.scalars(select(TeamProfileORM).order_by(TeamProfileORM.team_name)).all()
    return [row.to_domain() for row in rows]


def reset_engines() -> None:
    """테스트 격리용 — 캐시된 엔진 폐기."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _sessionmakers.clear()
