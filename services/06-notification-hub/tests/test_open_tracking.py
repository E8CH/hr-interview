"""열람 추적 — 트래킹 픽셀"""
from __future__ import annotations

from app.api.track import TRANSPARENT_PIXEL
from app.domain.models import Notification
from app.events import NOTIFICATION_OPENED
from app.infrastructure.event_bus import get_event_bus
from app.services.dispatcher import deliver, enqueue


def _sent_notification(session, invite_context, recipient="iv1@lge.com"):
    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient=recipient,
        context=invite_context,
        correlation_id="R2026-Q3-01/invitee-abc",
        round_id="R2026-Q3-01",
    )
    deliver(session, notification)
    session.commit()
    return notification


def test_pixel_is_valid_png():
    assert TRANSPARENT_PIXEL.startswith(b"\x89PNG\r\n\x1a\n")
    assert TRANSPARENT_PIXEL.endswith(b"IEND\xaeB`\x82")


def test_open_tracking_records_opened_at(client, session, invite_context):
    notification = _sent_notification(session, invite_context)
    assert notification.opened_at is None

    response = client.get(
        f"/api/v1/notify/track/open/{notification.notification_id}.png"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == TRANSPARENT_PIXEL
    assert "no-store" in response.headers["cache-control"]

    session.expire_all()
    refreshed = session.get(Notification, notification.notification_id)
    assert refreshed.opened_at is not None
    assert refreshed.status == "opened"

    events = [
        e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_OPENED
    ]
    assert len(events) == 1
    assert events[0]["payload"]["notification_id"] == notification.notification_id
    assert events[0]["correlation_id"] == "R2026-Q3-01/invitee-abc"
    assert events[0]["round_id"] == "R2026-Q3-01"
    assert events[0]["producer"] == "notification-hub"


def test_open_tracking_is_recorded_once(client, session, invite_context):
    notification = _sent_notification(session, invite_context)
    url = f"/api/v1/notify/track/open/{notification.notification_id}.png"

    client.get(url)
    session.expire_all()
    first_opened = session.get(Notification, notification.notification_id).opened_at

    client.get(url)
    client.get(url)
    session.expire_all()
    assert session.get(Notification, notification.notification_id).opened_at == first_opened

    events = [
        e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_OPENED
    ]
    assert len(events) == 1


def test_open_tracking_unknown_id_still_returns_pixel(client):
    response = client.get("/api/v1/notify/track/open/00000000-0000-0000-0000-000000000000.png")
    assert response.status_code == 200
    assert response.content == TRANSPARENT_PIXEL
    assert not [
        e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_OPENED
    ]


def test_pixel_url_in_body_is_reachable(client, session, invite_context):
    """본문에 삽입된 URL 을 그대로 호출하면 열람이 기록되어야 한다."""
    notification = _sent_notification(session, invite_context, "reach@lge.com")
    marker = f"/api/v1/notify/track/open/{notification.notification_id}.png"
    assert marker in notification.body

    response = client.get(marker)
    assert response.status_code == 200

    session.expire_all()
    assert session.get(Notification, notification.notification_id).opened_at is not None


def test_history_reflects_opened_status(client, session, invite_context):
    notification = _sent_notification(session, invite_context, "hist@lge.com")
    client.get(f"/api/v1/notify/track/open/{notification.notification_id}.png")

    body = client.get("/api/v1/notify/history?status=opened").json()["data"]
    assert body["count"] == 1
    assert body["items"][0]["opened_at"] is not None
