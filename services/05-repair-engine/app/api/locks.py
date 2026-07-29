"""3단계 락 관리"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import LockUpgradeRequest, ok
from app.infrastructure.db import get_session
from app.services import lock_service

router = APIRouter(prefix="/api/v1/repair", tags=["locks"])


@router.get("/locks/{schedule_id}")
def get_locks(schedule_id: str, session: Session = Depends(get_session)):
    locks = lock_service.list_locks(session, schedule_id)
    counts: dict[str, int] = {}
    for row in locks:
        counts[row["lock_level"]] = counts.get(row["lock_level"], 0) + 1
    return ok({"schedule_id": schedule_id, "locks": locks,
               "counts": counts, "total": len(locks)})


@router.post("/locks/upgrade")
def upgrade_locks(payload: LockUpgradeRequest,
                  session: Session = Depends(get_session)):
    return ok(lock_service.upgrade_locks(
        session, payload.schedule_id, payload.applicant_ids, payload.new_level))
