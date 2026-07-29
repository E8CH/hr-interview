"""POST /api/v1/reminders/trigger — 리마인더 수동 트리거 · 스케줄 조회"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.errors import NotFound
from app.domain.invitee import Invitee
from app.infrastructure.db import get_db
from app.schemas import ReminderTriggerIn, ok
from app.services import reminder_service
from app.services.reminder_engine import REMINDER_RULES, reminder_schedule, rule_for_level
from app.timeutil import to_aware_utc

router = APIRouter(prefix="/api/v1/reminders", tags=["reminders"])


@router.post("/trigger")
def trigger_reminder(body: ReminderTriggerIn, db: Session = Depends(get_db)):
    invitee = db.get(Invitee, body.invitee_id)
    if invitee is None:
        raise NotFound(f"초대자를 찾을 수 없습니다: {body.invitee_id}")

    reminder, reason = reminder_service.trigger_manual(
        db, invitee, body.level, body.channel, body.force
    )
    return ok(
        {
            "sent": reminder is not None,
            "invitee_id": invitee.invitee_id,
            "level": reminder.level if reminder else None,
            "cc_supervisor": bool(reminder and reminder.cc_supervisor),
            "escalated": bool(reminder and reminder.cc_supervisor),
            "reason": reason,
        }
    )


@router.post("/run-cycle")
def run_cycle(db: Session = Depends(get_db)):
    """스케줄러 잡을 즉시 1회 수행 (운영 · 데모용)."""
    sent = reminder_service.run_reminder_cycle(db)
    return ok(
        {
            "sent_count": len(sent),
            "sent": [{"invitee_id": r.invitee_id, "level": r.level} for r in sent],
        }
    )


@router.get("/rules")
def get_rules():
    return ok(REMINDER_RULES)


@router.get("/schedule/{invitee_id}")
def get_schedule(invitee_id: str, db: Session = Depends(get_db)):
    invitee = db.get(Invitee, invitee_id)
    if invitee is None:
        raise NotFound(f"초대자를 찾을 수 없습니다: {invitee_id}")
    if invitee.sent_at is None:
        raise NotFound("아직 발송되지 않은 요청입니다.")

    schedule = reminder_schedule(invitee.sent_at)
    sent_levels = {r.level for r in invitee.reminders}
    return ok(
        {
            "invitee_id": invitee.invitee_id,
            "sent_at": to_aware_utc(invitee.sent_at),
            "last_reminder_level": invitee.last_reminder_level,
            "responded": invitee.has_responded,
            "schedule": [
                {
                    "level": level,
                    "due_at": to_aware_utc(due),
                    "tone": rule_for_level(level)["tone"],
                    "cc_supervisor": rule_for_level(level)["cc_supervisor"],
                    "sent": level in sent_levels,
                }
                for level, due in sorted(schedule.items())
            ],
        }
    )
