"""4대 규칙 준수율 조회 API"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.errors import ok
from app.infrastructure.db import get_db
from app.services import schedule_service
from app.services.rule_evaluator import rule_compliance

router = APIRouter(prefix="/api/v1/schedules", tags=["rules"])


@router.get("/{schedule_id}/rules")
def get_rules(schedule_id: str, recompute: bool = False, db: Session = Depends(get_db)):
    schedule = schedule_service.get_schedule(db, schedule_id)
    if recompute:
        assignments = schedule_service.list_assignments(db, schedule_id)
        interviewers = schedule_service.load_interviewers(db, schedule.round_id)
        return ok(rule_compliance(assignments, interviewers).verbose())
    return ok(schedule_service.stored_rules(db, schedule_id))
