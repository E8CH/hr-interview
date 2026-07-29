"""초대 요청 발송 — requests/invitees 생성 · 메일 발송 · REQUEST_SENT 발행"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.domain.base import new_uuid
from app.domain.invitee import Invitee, new_token
from app.domain.request import Request
from app.events import EventType, RequestSentPayload, make_envelope
from app.infrastructure.event_bus import get_event_bus
from app.schemas import InviteeIn
from app.services import messages
from app.services.notification_client import Message, get_notification_client
from app.timeutil import to_naive_utc, utcnow

logger = structlog.get_logger(__name__)


def create_request(
    db: Session,
    *,
    round_id: str,
    plan_id: str,
    deadline,
    invitees: list[InviteeIn],
    team_name: str | None = None,
    correlation_id: str | None = None,
) -> tuple[Request, int]:
    """초대 요청 생성 → 발송 → 이벤트 발행.

    Returns:
        (Request, 실제 발송 성공 건수)
    """
    request = Request(
        request_id=new_uuid(),
        round_id=round_id,
        plan_id=plan_id,
        team_name=team_name or _dominant_team(invitees),
        deadline=to_naive_utc(deadline),
        sent_at=utcnow(),
        correlation_id=correlation_id or new_uuid(),
    )
    db.add(request)

    rows = [
        Invitee(
            invitee_id=new_uuid(),
            request_id=request.request_id,
            name=i.name,
            email=i.email,
            team=i.team,
            org=i.org,
            dept_leader_email=i.dept_leader_email,
            token=new_token(),
        )
        for i in invitees
    ]
    db.add_all(rows)
    db.flush()

    sent_count = _send_invitations(request, rows)
    db.commit()

    get_event_bus().publish(
        make_envelope(
            EventType.REQUEST_SENT,
            round_id=request.round_id,
            correlation_id=request.correlation_id,
            payload=RequestSentPayload(
                request_id=request.request_id,
                invitee_count=len(rows),
                deadline=request.deadline,
            ),
        )
    )
    logger.info(
        "request_sent",
        request_id=request.request_id,
        round_id=round_id,
        invitee_count=len(rows),
        sent_count=sent_count,
    )
    return request, sent_count


def _send_invitations(request: Request, invitees: list[Invitee]) -> int:
    client = get_notification_client()
    payloads = []
    for inv in invitees:
        subject, body = messages.invitation(inv.name, inv.team, request.deadline, inv.token)
        payloads.append(
            Message(
                to=inv.email,
                subject=subject,
                body=body,
                kind="invitation",
                meta={"request_id": request.request_id, "invitee_id": inv.invitee_id},
            )
        )
    return client.send_many(payloads)


def _dominant_team(invitees: list[InviteeIn]) -> str | None:
    """초대자 중 가장 많은 팀 이름 (요청 단위 대표 팀)."""
    if not invitees:
        return None
    counts: dict[str, int] = {}
    for i in invitees:
        counts[i.team] = counts.get(i.team, 0) + 1
    return max(counts, key=counts.get)


def close_request(db: Session, request: Request) -> Request:
    """요청 마감 처리 — 이후 폼 제출·리마인더 중단."""
    from app.domain.request import STATUS_CLOSED

    request.status = STATUS_CLOSED
    db.commit()
    logger.info("request_closed", request_id=request.request_id)
    return request
