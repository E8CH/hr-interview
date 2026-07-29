"""발송 파이프라인 — 큐 적재 · 채널 라우팅 · 재시도 · 이벤트 발행

명세 06 "발송 파이프라인" / "재시도 정책" 구현.
  1. 요청 수신 → notifications 에 queued 저장
  2. 워커가 큐에서 pull
  3. 채널별 어댑터로 발송
  4. 성공 → sent / 실패 → 재시도(최대 3회) → failed + dead letter
  5. 이벤트 발행
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.models import Channel, Notification, Template, utcnow
from app.events import NOTIFICATION_FAILED, NOTIFICATION_SENT
from app.infrastructure.event_bus import get_event_bus
from app.infrastructure.queue import pull_due, push_dead_letter
from app.responses import NotFoundError, ValidationFailed
from app.services.channels import (
    DEFAULT_BY_TYPE,
    SUPPORTED_TYPES,
    ChannelAdapter,
    ChannelDisabledError,
    ChannelSendError,
    OutboundMessage,
    get_adapter,
)
from app.services.template_renderer import TemplateRenderError, get_renderer

log = structlog.get_logger(__name__)


def resolve_channel(
    session: Session, channel_type: str
) -> tuple[ChannelAdapter, dict[str, Any], str]:
    """channels 테이블에서 활성 채널을 골라 (어댑터, config, channel_id) 반환."""
    if channel_type not in SUPPORTED_TYPES:
        raise ValidationFailed(f"지원하지 않는 채널: {channel_type}")

    rows = list(
        session.execute(
            select(Channel)
            .where(Channel.channel_type == channel_type)
            .order_by(Channel.channel_id)
        )
        .scalars()
        .all()
    )
    if not rows:
        # channels 테이블이 비어 있으면 타입 기본 어댑터로 폴백
        adapter = DEFAULT_BY_TYPE[channel_type]
        return adapter, {}, adapter.channel_id

    enabled = [row for row in rows if row.enabled]
    if not enabled:
        raise ChannelDisabledError(f"'{channel_type}' 채널이 모두 비활성 상태입니다")

    row = enabled[0]
    config = dict(row.config or {})
    adapter = get_adapter(config.get("adapter", "")) or DEFAULT_BY_TYPE[channel_type]
    return adapter, config, row.channel_id


def enqueue(
    session: Session,
    *,
    template_id: str,
    channel: str,
    recipient: str,
    context: dict[str, Any] | None = None,
    cc: list[str] | None = None,
    correlation_id: str | None = None,
    round_id: str | None = None,
) -> Notification:
    """템플릿 렌더링 후 queued 상태로 저장 (100% 이력 저장)."""
    if channel not in SUPPORTED_TYPES:
        raise ValidationFailed(f"지원하지 않는 채널: {channel}")
    if not recipient or not recipient.strip():
        raise ValidationFailed("recipient 는 필수입니다")

    template = session.get(Template, template_id)
    if template is None:
        raise NotFoundError(f"템플릿을 찾을 수 없습니다: {template_id}")

    context = dict(context or {})
    renderer = get_renderer()
    try:
        subject, body = renderer.render(template, context)
    except TemplateRenderError as exc:
        raise ValidationFailed(str(exc)) from exc

    notification = Notification(
        template_id=template_id,
        channel=channel,
        recipient=recipient,
        cc=list(cc or []),
        context=context,
        subject=subject,
        body=body,
        status="queued",
        attempt_count=0,
        correlation_id=correlation_id,
        round_id=round_id,
        next_attempt_at=utcnow(),
    )
    session.add(notification)
    session.flush()

    # 이메일 채널만 열람 추적 픽셀 삽입 (notification_id 확정 후)
    if channel == "email":
        notification.body = renderer.inject_tracking_pixel(
            body, notification.notification_id, settings.base_url
        )
    session.flush()
    log.info(
        "notification_queued",
        notification_id=notification.notification_id,
        template_id=template_id,
        channel=channel,
    )
    return notification


def _schedule_retry(notification: Notification, reason: str) -> None:
    delays = settings.retry_delays
    idx = min(notification.attempt_count, len(delays) - 1)
    notification.next_attempt_at = utcnow() + timedelta(seconds=delays[idx])
    notification.error_message = reason


def deliver(session: Session, notification: Notification) -> bool:
    """1회 발송을 시도한다. 성공 True / 실패 False.

    실패 시 재시도 스케줄을 잡고, 한도를 넘기면 dead letter 로 보낸다.
    """
    if notification.status in {"sent", "opened"}:
        return True

    notification.attempt_count += 1
    attempt = notification.attempt_count

    try:
        adapter, config, channel_id = resolve_channel(session, notification.channel)
        message = OutboundMessage(
            notification_id=notification.notification_id,
            recipient=notification.recipient,
            subject=notification.subject,
            body=notification.body or "",
            cc=list(notification.cc or []),
            channel_type=notification.channel,
            config=config,
        )
        result = adapter.send(message)
        if not result.ok:
            raise ChannelSendError(result.detail or "어댑터가 실패를 보고했습니다")
    except Exception as exc:  # 어댑터·채널 오류를 모두 재시도 대상으로 취급
        reason = f"{type(exc).__name__}: {exc}"
        log.warning(
            "notification_attempt_failed",
            notification_id=notification.notification_id,
            attempt=attempt,
            error=reason,
        )
        if attempt >= settings.max_attempts:
            notification.status = "failed"
            notification.error_message = reason
            push_dead_letter(session, notification, reason)
            session.flush()
            get_event_bus().publish(
                NOTIFICATION_FAILED,
                payload={
                    "notification_id": notification.notification_id,
                    "recipient": notification.recipient,
                    "channel": notification.channel,
                    "template_id": notification.template_id,
                    "attempt_count": attempt,
                    "error": reason,
                    "dead_letter": True,
                },
                round_id=notification.round_id,
                correlation_id=notification.correlation_id,
            )
            log.error(
                "notification_dead_letter",
                notification_id=notification.notification_id,
                attempts=attempt,
            )
        else:
            _schedule_retry(notification, reason)
            session.flush()
        return False

    notification.status = "sent"
    notification.sent_at = utcnow()
    notification.error_message = None
    session.flush()
    get_event_bus().publish(
        NOTIFICATION_SENT,
        payload={
            "notification_id": notification.notification_id,
            "recipient": notification.recipient,
            "channel": notification.channel,
            "template_id": notification.template_id,
            "provider": result.provider,
            "attempt_count": attempt,
        },
        round_id=notification.round_id,
        correlation_id=notification.correlation_id,
    )
    log.info(
        "notification_sent",
        notification_id=notification.notification_id,
        provider=result.provider,
        attempt=attempt,
    )
    return True


def deliver_by_id(session: Session, notification_id: str) -> bool:
    notification = session.get(Notification, notification_id)
    if notification is None:
        return False
    result = deliver(session, notification)
    session.commit()
    return result


def process_due(session: Session, limit: int = 50) -> dict[str, int]:
    """발송 시각이 도래한 큐 항목을 일괄 처리한다."""
    due = pull_due(session, limit=limit)
    sent = failed = 0
    for notification in due:
        if deliver(session, notification):
            sent += 1
        else:
            failed += 1
    session.commit()
    return {"processed": len(due), "sent": sent, "failed": failed}
