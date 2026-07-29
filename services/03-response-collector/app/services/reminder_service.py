"""리마인더 발송 오케스트레이션

`reminder_engine.should_send_reminder`(순수 판정) 결과를 DB · 알림 · 이벤트에 반영한다.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.domain.base import new_uuid
from app.domain.invitee import Invitee
from app.domain.reminder import Reminder
from app.domain.request import STATUS_CLOSED, Request
from app.events import (
    EventType,
    NonResponderEscalatedPayload,
    ReminderSentPayload,
    make_envelope,
)
from app.infrastructure.event_bus import get_event_bus
from app.services import messages
from app.services.notification_client import Message, get_notification_client
from app.services.reminder_engine import (
    MAX_LEVEL,
    ReminderDecision,
    rule_for_level,
    should_send_reminder,
)
from app.timeutil import hours_between, utcnow

logger = structlog.get_logger(__name__)


def send_reminder(
    db: Session,
    invitee: Invitee,
    level: int,
    channel: str = "email",
) -> Reminder:
    """리마인더 1건 발송 — 알림 · 이력 저장 · 이벤트 발행."""
    rule = rule_for_level(level)
    cc_supervisor = bool(rule["cc_supervisor"])
    request = invitee.request

    subject, body = messages.reminder(invitee.name, level, request.deadline, invitee.token)
    cc = []
    if cc_supervisor and invitee.dept_leader_email:
        cc.append(invitee.dept_leader_email)

    get_notification_client().send(
        Message(
            to=invitee.email,
            subject=subject,
            body=body,
            channel=channel,
            cc=cc,
            kind="reminder",
            meta={"invitee_id": invitee.invitee_id, "level": level, "tone": rule["tone"]},
        )
    )

    reminder = Reminder(
        reminder_id=new_uuid(),
        invitee_id=invitee.invitee_id,
        level=level,
        sent_at=utcnow(),
        channel=channel,
        cc_supervisor=cc_supervisor,
    )
    db.add(reminder)
    invitee.last_reminder_level = level
    db.commit()

    bus = get_event_bus()
    bus.publish(
        make_envelope(
            EventType.REMINDER_SENT,
            round_id=request.round_id,
            correlation_id=request.correlation_id,
            payload=ReminderSentPayload(
                invitee_id=invitee.invitee_id, level=level, channel=channel
            ),
        )
    )

    if cc_supervisor:
        _escalate(bus, request, invitee)

    # 키 이름은 `reminder_level` — structlog 의 로그 레벨 키(`level`)와 충돌 방지
    logger.info(
        "reminder_sent",
        invitee_id=invitee.invitee_id,
        reminder_level=level,
        tone=rule["tone"],
        cc_supervisor=cc_supervisor,
        channel=channel,
    )
    return reminder


def _escalate(bus, request: Request, invitee: Invitee) -> None:
    """Level 3 도달 → NON_RESPONDER_ESCALATED 발행."""
    now = utcnow()
    bus.publish(
        make_envelope(
            EventType.NON_RESPONDER_ESCALATED,
            round_id=request.round_id,
            correlation_id=request.correlation_id,
            payload=NonResponderEscalatedPayload(
                invitee_id=invitee.invitee_id,
                name=invitee.name,
                email=invitee.email,
                org=invitee.org,
                team=invitee.team,
                supervisor_email=invitee.dept_leader_email,
                hours_since_sent=round(hours_between(request.sent_at, now), 2)
                if request.sent_at
                else 0.0,
                escalated_at=now,
            ),
        )
    )
    logger.warning(
        "non_responder_escalated",
        invitee_id=invitee.invitee_id,
        supervisor_email=invitee.dept_leader_email,
    )


def evaluate_invitee(db: Session, invitee: Invitee, now=None) -> ReminderDecision:
    """단일 초대자에 대한 판정 (마감된 요청은 제외)."""
    now = now or utcnow()
    if invitee.request.status == STATUS_CLOSED:
        return ReminderDecision(False, None, "마감된 요청")
    return should_send_reminder(now, invitee, invitee.has_responded)


def run_reminder_cycle(db: Session, now=None) -> list[Reminder]:
    """APScheduler 가 주기적으로 호출 — 대상 전원 판정 후 발송.

    Returns:
        이번 사이클에 발송된 리마인더 목록.
    """
    now = now or utcnow()
    candidates = (
        db.query(Invitee)
        .join(Request, Invitee.request_id == Request.request_id)
        .filter(Request.status != STATUS_CLOSED)
        .filter(Request.sent_at.isnot(None))
        .filter(Invitee.last_reminder_level < MAX_LEVEL)
        .all()
    )

    sent: list[Reminder] = []
    for invitee in candidates:
        decision = evaluate_invitee(db, invitee, now)
        if not decision.should_send or decision.level is None:
            continue
        sent.append(send_reminder(db, invitee, decision.level))

    logger.info(
        "reminder_cycle_done",
        now=now.isoformat(),
        candidates=len(candidates),
        sent=len(sent),
    )
    return sent


def trigger_manual(
    db: Session,
    invitee: Invitee,
    level: int,
    channel: str = "email",
    force: bool = False,
) -> tuple[Reminder | None, str]:
    """수동 트리거 (`POST /api/v1/reminders/trigger`).

    force=False 이면 이미 회신한 사람에게는 보내지 않는다.
    """
    if not force:
        if invitee.has_responded:
            return None, "이미 회신함"
        if invitee.request.status == STATUS_CLOSED:
            return None, "마감된 요청"
        if invitee.last_reminder_level >= level:
            return None, f"이미 Level {invitee.last_reminder_level} 발송됨"
    return send_reminder(db, invitee, level, channel), f"Level {level} 수동 발송"
