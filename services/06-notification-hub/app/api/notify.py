"""발송 API — 단건 · 다중 · 이력 · dead letter"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.models import Notification
from app.domain.schemas import BroadcastRequest, SendRequest
from app.infrastructure.db import get_db, session_scope
from app.infrastructure.queue import list_dead_letters
from app.responses import ValidationFailed, ok
from app.security import require_auth
from app.services.dispatcher import deliver, enqueue, process_due

router = APIRouter(
    prefix="/api/v1/notify", tags=["notify"], dependencies=[Depends(require_auth)]
)


def _deliver_ids(notification_ids: list[str]) -> None:
    """백그라운드 1회차 발송 — 요청 세션과 분리된 자체 세션을 쓴다."""
    with session_scope() as session:
        for notification_id in notification_ids:
            notification = session.get(Notification, notification_id)
            if notification is not None:
                deliver(session, notification)


def _kick(background: BackgroundTasks, notification_ids: list[str]) -> None:
    if settings.dispatch_on_request and notification_ids:
        background.add_task(_deliver_ids, notification_ids)


@router.post("/send", status_code=202)
def send(
    payload: SendRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_db),
):
    notification = enqueue(
        session,
        template_id=payload.template_id,
        channel=payload.channel,
        recipient=payload.recipient,
        context=payload.context,
        cc=payload.cc,
        correlation_id=payload.correlation_id,
        round_id=payload.round_id,
    )
    session.commit()
    _kick(background, [notification.notification_id])
    return ok(
        {"notification_id": notification.notification_id, "status": notification.status}
    )


@router.post("/broadcast", status_code=202)
def broadcast(
    payload: BroadcastRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_db),
):
    if not payload.recipients:
        raise ValidationFailed("recipients 가 비어 있습니다")

    created: list[str] = []
    for entry in payload.recipients:
        address = entry.address
        if not address:
            raise ValidationFailed("recipients 항목에 email 또는 recipient 가 필요합니다")
        context = {**payload.context, **entry.context}
        notification = enqueue(
            session,
            template_id=payload.template_id,
            channel=payload.channel,
            recipient=address,
            context=context,
            cc=entry.cc,
            correlation_id=payload.correlation_id,
            round_id=payload.round_id,
        )
        created.append(notification.notification_id)
    session.commit()
    _kick(background, created)
    return ok(
        {
            "queued": len(created),
            "status": "queued",
            "notification_ids": created,
            "correlation_id": payload.correlation_id,
        }
    )


@router.get("/history")
def history(
    correlation_id: str | None = Query(default=None),
    round_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    template_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_db),
):
    stmt = select(Notification)
    if correlation_id:
        stmt = stmt.where(Notification.correlation_id == correlation_id)
    if round_id:
        stmt = stmt.where(Notification.round_id == round_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    if template_id:
        stmt = stmt.where(Notification.template_id == template_id)
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    rows = session.execute(stmt).scalars().all()
    return ok({"count": len(rows), "items": [row.to_dict() for row in rows]})


@router.get("/history/{recipient}")
def history_by_recipient(
    recipient: str,
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_db),
):
    stmt = (
        select(Notification)
        .where(Notification.recipient == recipient)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return ok(
        {"recipient": recipient, "count": len(rows), "items": [r.to_dict() for r in rows]}
    )


@router.get("/dead-letters")
def dead_letters(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db),
):
    rows = list_dead_letters(session, limit=limit)
    return ok({"count": len(rows), "items": [row.to_dict() for row in rows]})


@router.post("/process", status_code=200)
def process_queue(
    limit: int = Query(default=50, ge=1, le=1000),
    session: Session = Depends(get_db),
):
    """큐 수동 처리 — 워커 없이 운영/디버깅할 때 사용."""
    return ok(process_due(session, limit=limit))
