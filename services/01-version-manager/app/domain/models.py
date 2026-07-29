"""ORM 모델 — versions / integrity_checks.

명세는 PostgreSQL(TEXT[], JSONB) 기준이나 PoC는 SQLite → JSON 컬럼으로 매핑.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.infrastructure.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Version(Base):
    __tablename__ = "versions"

    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # master | team_distribution
    team_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    applicant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applicant_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "round_id": self.round_id,
            "kind": self.kind,
            "team_name": self.team_name,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "fingerprint": self.fingerprint,
            "applicant_count": self.applicant_count,
            "applicant_ids": self.applicant_ids,
            "actor": self.actor,
            "parent_version": self.parent_version,
            "created_at": self.created_at,
            "is_active": self.is_active,
        }


class IntegrityCheck(Base):
    __tablename__ = "integrity_checks"

    check_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # OK|ISSUES_FOUND|NO_MASTER
    master_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distributed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    undistributed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "round_id": self.round_id,
            "checked_at": self.checked_at,
            "status": self.status,
            "master_count": self.master_count,
            "distributed_count": self.distributed_count,
            "undistributed_count": self.undistributed_count,
            "duplicate_count": self.duplicate_count,
            "issues": self.issues,
        }
