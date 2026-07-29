"""3단계 락 시스템 (DRAFT < CONFIRMED < LOCKED)

  DRAFT      자유롭게 이동
  CONFIRMED  페널티 부여 후 이동 허용
  LOCKED     재편성 불가 — 이미 안내된 지원자는 절대 흔들지 않는다

락은 승격만 가능하다. 강등은 거부한다 (안내 후 되돌리기 방지).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.repair_event import LOCK_LEVELS, lock_rank
from app.domain.schedule import ScheduleSnapshot
from app.infrastructure.db import LockMapRow


class LockError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def sync_from_snapshot(session: Session, snapshot: ScheduleSnapshot) -> None:
    """시간표 스냅샷의 락 상태를 lock_map 에 반영 (승격만)."""
    existing = {
        row.applicant_id: row
        for row in session.scalars(
            select(LockMapRow).where(LockMapRow.schedule_id == snapshot.schedule_id))
    }
    for a in snapshot.assignments:
        row = existing.get(a.applicant_id)
        if row is None:
            session.add(LockMapRow(schedule_id=snapshot.schedule_id,
                                   applicant_id=a.applicant_id,
                                   lock_level=a.lock_level,
                                   updated_at=datetime.utcnow()))
        elif lock_rank(a.lock_level) > lock_rank(row.lock_level):
            row.lock_level = a.lock_level
            row.updated_at = datetime.utcnow()
    session.commit()


def get_locks(session: Session, schedule_id: str) -> dict[str, str]:
    rows = session.scalars(
        select(LockMapRow).where(LockMapRow.schedule_id == schedule_id))
    return {row.applicant_id: row.lock_level for row in rows}


def list_locks(session: Session, schedule_id: str) -> list[dict]:
    rows = session.scalars(
        select(LockMapRow).where(LockMapRow.schedule_id == schedule_id))
    return sorted(
        [{"applicant_id": r.applicant_id, "lock_level": r.lock_level,
          "updated_at": r.updated_at.isoformat() if r.updated_at else None}
         for r in rows],
        key=lambda d: d["applicant_id"])


def upgrade_locks(session: Session, schedule_id: str,
                  applicant_ids: list[str], new_level: str) -> dict:
    level = (new_level or "").upper()
    if level not in LOCK_LEVELS:
        raise LockError("VALIDATION_FAILED",
                        f"알 수 없는 락 레벨: {new_level} (허용: {list(LOCK_LEVELS)})")

    existing = {
        row.applicant_id: row
        for row in session.scalars(
            select(LockMapRow).where(LockMapRow.schedule_id == schedule_id))
    }
    upgraded, skipped = [], []
    for applicant_id in applicant_ids:
        row = existing.get(applicant_id)
        if row is None:
            session.add(LockMapRow(schedule_id=schedule_id, applicant_id=applicant_id,
                                   lock_level=level, updated_at=datetime.utcnow()))
            upgraded.append(applicant_id)
        elif lock_rank(level) > lock_rank(row.lock_level):
            row.lock_level = level
            row.updated_at = datetime.utcnow()
            upgraded.append(applicant_id)
        else:
            skipped.append({"applicant_id": applicant_id,
                            "current": row.lock_level, "requested": level})
    session.commit()
    return {"schedule_id": schedule_id, "new_level": level,
            "upgraded": upgraded, "upgraded_count": len(upgraded),
            "skipped": skipped}
