"""시간표 생성·조회·검증·락 API"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app import events
from app.domain.schemas import AssignmentOut, GenerateRequest, LockRequest
from app.errors import ok
from app.infrastructure.db import get_db
from app.services import schedule_service

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_schedule(req: GenerateRequest, db: Session = Depends(get_db)):
    schedule, report, violations, plan, off_band = schedule_service.generate(db, req)

    events.publish_schedule_generated(
        round_id=schedule.round_id,
        schedule_id=schedule.schedule_id,
        total_assigned=schedule.total_assigned,
        coverage_pct=schedule.coverage_pct,
        rule_compliance_overall=report.overall,
    )
    if violations:
        events.publish_rule_violated(
            round_id=schedule.round_id,
            schedule_id=schedule.schedule_id,
            violations=violations,
        )

    payload = schedule_service.schedule_payload(schedule, report.flat(), violations)
    payload["unassigned_count"] = len(plan.unassigned)
    payload["unassigned"] = [a.applicant_id for a in plan.unassigned]
    # 담당자 일정을 무시하고 만들었을 때만 채워진다 — 다시 이야기할 명단
    payload["off_band_count"] = len(off_band)
    payload["off_band"] = off_band
    return ok(payload)


@router.get("/rounds/{round_id}", summary="회차의 시간표 목록 (최신순)")
def list_round_schedules(round_id: str, db: Session = Depends(get_db)):
    return ok(schedule_service.list_schedules_for_round(db, round_id))


@router.delete("/rounds/{round_id}", summary="회차 시간표 비우기")
def reset_round_schedules(round_id: str, db: Session = Depends(get_db)):
    return ok(schedule_service.reset_round(db, round_id))


@router.get("/{schedule_id}")
def get_schedule(schedule_id: str, db: Session = Depends(get_db)):
    schedule = schedule_service.get_schedule(db, schedule_id)
    assignments = schedule_service.list_assignments(db, schedule_id)
    rules = {
        name: entry["score"]
        for name, entry in schedule_service.stored_rules(db, schedule_id).items()
    }
    payload = schedule_service.schedule_payload(schedule, rules, [])
    payload["hard_violations"] = schedule.hard_violations
    payload["generated_at"] = schedule.generated_at
    payload["generated_by"] = schedule.generated_by
    payload["assignments"] = [
        AssignmentOut.model_validate(a).model_dump() for a in assignments
    ]
    return ok(payload)


@router.get("/{schedule_id}/heatmap")
def get_heatmap(schedule_id: str, db: Session = Depends(get_db)):
    schedule_service.get_schedule(db, schedule_id)
    assignments = schedule_service.list_assignments(db, schedule_id)
    return ok(schedule_service.heatmap(assignments))


@router.get("/{schedule_id}/by-team")
def get_by_team(schedule_id: str, db: Session = Depends(get_db)):
    schedule_service.get_schedule(db, schedule_id)
    assignments = schedule_service.list_assignments(db, schedule_id)
    return ok(schedule_service.by_team(assignments))


@router.post("/{schedule_id}/validate")
def validate_schedule(schedule_id: str, db: Session = Depends(get_db)):
    schedule, violations, penalty, off_band = schedule_service.validate_schedule(db, schedule_id)
    if violations:
        events.publish_rule_violated(
            round_id=schedule.round_id, schedule_id=schedule_id, violations=violations
        )
    return ok(
        {
            "hard_violations": violations,
            "soft_penalty": penalty,
            # 담당자 일정을 무시하고 만들었을 때만 채워진다 — 다시 이야기할 명단
            "off_band": off_band,
        }
    )


@router.post("/{schedule_id}/lock")
def lock_schedule(schedule_id: str, req: LockRequest, db: Session = Depends(get_db)):
    schedule, changed = schedule_service.lock(db, schedule_id, req.lock_level, req.applicant_ids)

    events.publish_schedule_locked(
        round_id=schedule.round_id,
        schedule_id=schedule_id,
        lock_level=req.lock_level,
        assignments_count=len(changed),
    )
    return ok(
        {
            "schedule_id": schedule_id,
            "status": schedule.status,
            "lock_level": req.lock_level,
            "changed": len(changed),
            "applicant_ids": [a.applicant_id for a in changed],
            "actor": req.actor,
        }
    )
