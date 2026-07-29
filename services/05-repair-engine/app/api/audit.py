"""재편성 감사 로그"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import ok
from app.infrastructure.db import get_session
from app.services import repair_service

router = APIRouter(prefix="/api/v1/repair", tags=["audit"])


@router.get("/audit/{round_id}")
def get_audit(round_id: str, session: Session = Depends(get_session)):
    return ok(repair_service.audit_log(session, round_id))
