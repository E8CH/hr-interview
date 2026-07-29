"""이벤트 수신 엔드포인트

PoC 에서 Redis 없이도 다른 서비스가 이벤트를 밀어 넣을 수 있도록 HTTP 진입점을
제공한다. 봉투(EventEnvelope) 규격은 Pub/Sub 경로와 동일하다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.contracts.events import EventEnvelope
from app.events import PUBLISHED_EVENTS, SUBSCRIBED_EVENTS
from app.infrastructure.event_bus import get_event_bus
from app.responses import ok
from app.security import require_auth

router = APIRouter(
    prefix="/api/v1/notify", tags=["events"], dependencies=[Depends(require_auth)]
)


@router.get("/events")
def event_catalog():
    bus = get_event_bus()
    return ok(
        {
            "publishes": list(PUBLISHED_EVENTS),
            "subscribes": list(SUBSCRIBED_EVENTS),
            "registered": bus.subscribed_types(),
            "published_count": len(bus.published),
        }
    )


@router.post("/events/inbound", status_code=202)
def receive_event(envelope: EventEnvelope):
    """외부 이벤트를 구독 핸들러로 전달한다."""
    body = envelope.model_dump(mode="json")
    handled = get_event_bus().dispatch(body)
    return ok(
        {
            "event_id": body["event_id"],
            "event_type": body["event_type"],
            "handlers": handled,
        }
    )
