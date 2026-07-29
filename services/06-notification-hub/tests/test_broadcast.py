"""발송 API — 단건 · 다중(100명) · 이력 조회"""
from __future__ import annotations

from app.domain.models import Notification
from app.events import NOTIFICATION_SENT
from app.infrastructure.event_bus import get_event_bus

CORRELATION = "R2026-Q3-01"


def test_send_returns_202_queued(client, invite_context):
    response = client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "reminder_l1",
            "channel": "email",
            "recipient": "iv1@lge.com",
            "cc": ["backup@lge.com"],
            "context": invite_context,
            "correlation_id": "R2026-Q3-01/invitee-abc",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["error"] is None
    assert body["data"]["status"] == "queued"
    assert body["data"]["notification_id"]


def test_send_unknown_template_returns_404_envelope(client):
    response = client.post(
        "/api/v1/notify/send",
        json={"template_id": "nope", "channel": "email", "recipient": "a@b.com"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def test_send_missing_context_returns_422(client):
    response = client.post(
        "/api/v1/notify/send",
        json={"template_id": "invite", "channel": "email", "recipient": "a@b.com"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_send_bad_channel_returns_422(client, invite_context):
    response = client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "invite",
            "channel": "fax",
            "recipient": "a@b.com",
            "context": invite_context,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_broadcast_creates_one_notification_per_recipient(client, session):
    """100명 발송 시 개별 알림 100건 생성"""
    recipients = [
        {"email": f"a{i}@x.com", "context": {"name": f"지원자{i}", "day": "화", "hour": "10시"}}
        for i in range(100)
    ]
    response = client.post(
        "/api/v1/notify/broadcast",
        json={
            "template_id": "applicant_invite",
            "channel": "email",
            "recipients": recipients,
            "correlation_id": CORRELATION,
        },
    )
    assert response.status_code == 202
    data = response.json()["data"]
    assert data["queued"] == 100
    assert len(set(data["notification_ids"])) == 100

    rows = (
        session.query(Notification)
        .filter(Notification.correlation_id == CORRELATION)
        .all()
    )
    assert len(rows) == 100
    assert all(row.status == "queued" for row in rows)
    assert {row.recipient for row in rows} == {f"a{i}@x.com" for i in range(100)}
    # 컨텍스트가 개별 렌더링되었는지 확인
    by_recipient = {row.recipient: row for row in rows}
    assert "지원자42" in by_recipient["a42@x.com"].body
    assert "지원자7" not in by_recipient["a42@x.com"].body


def test_broadcast_merges_shared_context(client, session):
    response = client.post(
        "/api/v1/notify/broadcast",
        json={
            "template_id": "applicant_invite",
            "channel": "email",
            "context": {"day": "수", "hour": "14시", "method": "대면"},
            "recipients": [
                {"email": "a@x.com", "context": {"name": "새한별"}},
                {"recipient": "b@x.com", "context": {"name": "홍길동", "hour": "16시"}},
            ],
            "correlation_id": "shared-ctx",
        },
    )
    assert response.status_code == 202
    rows = {
        row.recipient: row
        for row in session.query(Notification)
        .filter(Notification.correlation_id == "shared-ctx")
        .all()
    }
    assert "수 14시" in rows["a@x.com"].body
    assert "대면" in rows["a@x.com"].body
    assert "수 16시" in rows["b@x.com"].body  # 개별 값이 공통 값을 덮어쓴다


def test_broadcast_empty_recipients_is_422(client):
    response = client.post(
        "/api/v1/notify/broadcast",
        json={"template_id": "applicant_invite", "recipients": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_broadcast_recipient_without_address_is_422(client):
    response = client.post(
        "/api/v1/notify/broadcast",
        json={
            "template_id": "applicant_invite",
            "recipients": [{"context": {"name": "무명", "day": "화", "hour": "10시"}}],
        },
    )
    assert response.status_code == 422


def test_process_endpoint_flushes_queue_and_publishes_events(client, invite_context):
    client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "invite",
            "channel": "email",
            "recipient": "iv9@lge.com",
            "context": invite_context,
            "correlation_id": CORRELATION,
        },
    )
    result = client.post("/api/v1/notify/process").json()["data"]
    assert result == {"processed": 1, "sent": 1, "failed": 0}

    sent = [e for e in get_event_bus().published if e["event_type"] == NOTIFICATION_SENT]
    assert len(sent) == 1

    history = client.get(f"/api/v1/notify/history?correlation_id={CORRELATION}").json()
    assert history["data"]["count"] == 1
    assert history["data"]["items"][0]["status"] == "sent"


def test_history_filters(client, invite_context):
    client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "invite",
            "channel": "email",
            "recipient": "filter@lge.com",
            "context": invite_context,
            "correlation_id": "cid-a",
            "round_id": "R2026-Q3-01",
        },
    )
    client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "reminder_l1",
            "channel": "email",
            "recipient": "other@lge.com",
            "context": invite_context,
            "correlation_id": "cid-b",
        },
    )

    assert client.get("/api/v1/notify/history").json()["data"]["count"] == 2
    assert (
        client.get("/api/v1/notify/history?correlation_id=cid-a").json()["data"]["count"]
        == 1
    )
    assert (
        client.get("/api/v1/notify/history?round_id=R2026-Q3-01").json()["data"]["count"]
        == 1
    )
    assert (
        client.get("/api/v1/notify/history?template_id=reminder_l1").json()["data"][
            "count"
        ]
        == 1
    )
    assert client.get("/api/v1/notify/history?status=sent").json()["data"]["count"] == 0


def test_history_by_recipient(client, invite_context):
    for _ in range(2):
        client.post(
            "/api/v1/notify/send",
            json={
                "template_id": "invite",
                "channel": "email",
                "recipient": "iv1@lge.com",
                "context": invite_context,
            },
        )
    body = client.get("/api/v1/notify/history/iv1@lge.com").json()["data"]
    assert body["recipient"] == "iv1@lge.com"
    assert body["count"] == 2

    empty = client.get("/api/v1/notify/history/nobody@lge.com").json()["data"]
    assert empty["count"] == 0


def test_dispatch_on_request_sends_immediately(client, invite_context, monkeypatch):
    monkeypatch.setenv("DISPATCH_ON_REQUEST", "true")
    response = client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "invite",
            "channel": "email",
            "recipient": "instant@lge.com",
            "context": invite_context,
        },
    )
    assert response.status_code == 202
    # TestClient 는 BackgroundTask 를 응답 직후 동기 실행한다
    history = client.get("/api/v1/notify/history/instant@lge.com").json()["data"]
    assert history["items"][0]["status"] == "sent"
