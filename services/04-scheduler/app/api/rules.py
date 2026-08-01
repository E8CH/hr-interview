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
        # 다시 재더라도 만들 때 쓴 잣대를 그대로 쓴다 — 진행 조건이 '첫 타임' 이
        # 어느 칸인지를, 규칙1의 목표가 어느 비율을 뜻하는지를 정한다. 안 넘기면
        # 같은 시간표인데 저장된 점수와 다시 잰 점수가 갈린다.
        target, tolerance = schedule_service.stored_grad_target(db, schedule_id)
        return ok(
            rule_compliance(
                assignments,
                interviewers,
                grad_ratio_target=target,
                grad_ratio_tolerance=tolerance,
                timing=schedule_service.stored_timing(db, schedule_id),
            ).verbose()
        )
    return ok(schedule_service.stored_rules(db, schedule_id))
