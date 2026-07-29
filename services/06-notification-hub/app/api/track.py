"""열람 추적 — 1x1 투명 PNG 트래킹 픽셀"""
from __future__ import annotations

import base64

import structlog
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.domain.models import Notification, utcnow
from app.events import NOTIFICATION_OPENED
from app.infrastructure.db import get_db
from app.infrastructure.event_bus import get_event_bus

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/notify", tags=["track"])

# 1x1 투명 PNG
TRANSPARENT_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def _pixel_response() -> Response:
    return Response(
        content=TRANSPARENT_PIXEL, media_type="image/png", headers=dict(_NO_CACHE)
    )


@router.get("/track/open/{notification_id}.png")
def track_open(notification_id: str, session: Session = Depends(get_db)):
    """픽셀 요청 시 opened_at 기록 후 항상 이미지를 반환한다.

    알림이 없거나 이미 열람된 경우에도 200 + 픽셀을 돌려준다
    (메일 클라이언트에 깨진 이미지를 노출하지 않기 위함).
    """
    notification = session.get(Notification, notification_id)
    if notification is None:
        log.info("track_open_unknown", notification_id=notification_id)
        return _pixel_response()

    first_open = notification.opened_at is None
    if first_open:
        notification.opened_at = utcnow()
        if notification.status in {"sent", "queued"}:
            notification.status = "opened"
        session.flush()
        get_event_bus().publish(
            NOTIFICATION_OPENED,
            payload={
                "notification_id": notification.notification_id,
                "recipient": notification.recipient,
                "channel": notification.channel,
                "template_id": notification.template_id,
                "opened_at": notification.opened_at.isoformat(),
            },
            round_id=notification.round_id,
            correlation_id=notification.correlation_id,
        )
        log.info("notification_opened", notification_id=notification_id)

    return _pixel_response()
