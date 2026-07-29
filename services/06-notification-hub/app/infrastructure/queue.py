"""발송 큐 — PoC 는 notifications 테이블을 큐로 사용

status='queued' 이고 next_attempt_at <= now 인 행이 "발송 대기" 상태다.
백오프는 next_attempt_at 을 미래로 밀어두는 방식으로 구현한다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import DeadLetter, Notification, utcnow


def pull_due(session: Session, limit: int = 50) -> list[Notification]:
    """발송 시각이 도래한 queued 알림을 오래된 순으로 가져온다."""
    stmt = (
        select(Notification)
        .where(Notification.status == "queued")
        .where(Notification.next_attempt_at <= utcnow())
        .order_by(Notification.next_attempt_at, Notification.created_at)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def queued_count(session: Session) -> int:
    stmt = select(Notification.notification_id).where(Notification.status == "queued")
    return len(session.execute(stmt).scalars().all())


def push_dead_letter(
    session: Session, notification: Notification, reason: str
) -> DeadLetter:
    """dead letter 큐에 적재 (중복 방지)."""
    existing = session.execute(
        select(DeadLetter).where(
            DeadLetter.notification_id == notification.notification_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    entry = DeadLetter(
        notification_id=notification.notification_id,
        channel=notification.channel,
        recipient=notification.recipient,
        attempt_count=notification.attempt_count,
        error_message=reason,
        correlation_id=notification.correlation_id,
        snapshot=notification.to_dict(),
    )
    session.add(entry)
    session.flush()
    return entry


def list_dead_letters(session: Session, limit: int = 100) -> list[DeadLetter]:
    stmt = select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())
