"""템플릿·채널 관리 API + 헬스체크/메트릭"""
from __future__ import annotations


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "06-notification-hub"}


def test_root_envelope(client):
    body = client.get("/").json()
    assert body["error"] is None
    assert body["data"]["service"] == "06-notification-hub"
    assert "NOTIFICATION_SENT" in body["data"]["publishes"]


def test_unknown_route_returns_error_envelope(client):
    response = client.get("/api/v1/notify/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_metrics_prometheus_format(client, invite_context):
    client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "invite",
            "channel": "email",
            "recipient": "m@lge.com",
            "context": invite_context,
        },
    )
    client.post("/api/v1/notify/process")

    text = client.get("/metrics").text
    assert 'notification_total{status="sent"} 1' in text
    assert "notification_dead_letter_total 0" in text
    assert "notification_templates_total 10" in text
    assert "notification_attempts_total 1" in text
    assert 'notification_events_published_total{event_type="NOTIFICATION_SENT"} 1' in text


# --- 템플릿 API ---
def test_list_templates(client):
    data = client.get("/api/v1/notify/templates").json()["data"]
    assert data["count"] == 10
    assert {item["template_id"] for item in data["items"]} >= {"invite", "reminder_l3"}


def test_get_template_exposes_variables(client):
    data = client.get("/api/v1/notify/templates/invite").json()["data"]
    assert data["channel"] == "email"
    assert "deadline" in data["variables"]
    assert "form_link" in data["variables"]


def test_get_template_404(client):
    response = client.get("/api/v1/notify/templates/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_put_template_updates_and_creates(client):
    updated = client.put(
        "/api/v1/notify/templates/invite",
        json={"channel": "email", "subject": "새 제목 {{ name }}", "body": "새 본문 {{ name }}"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["created"] is False
    assert updated.json()["data"]["subject"] == "새 제목 {{ name }}"

    created = client.put(
        "/api/v1/notify/templates/custom_alert",
        json={"channel": "slack", "subject": None, "body": "커스텀 {{ msg }}"},
    )
    assert created.json()["data"]["created"] is True
    assert client.get("/api/v1/notify/templates").json()["data"]["count"] == 11


def test_put_template_rejects_syntax_error(client):
    response = client.put(
        "/api/v1/notify/templates/broken",
        json={"channel": "email", "body": "{% for x in %}"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_template_preview(client, invite_context):
    body = client.post(
        "/api/v1/notify/templates/invite/preview", json={"context": invite_context}
    ).json()["data"]
    assert "이지훈" in body["subject"] or "이지훈" in body["body"]
    assert "{{" not in body["body"]


def test_template_preview_missing_variable_is_422(client):
    response = client.post(
        "/api/v1/notify/templates/invite/preview", json={"context": {}}
    )
    assert response.status_code == 422


def test_template_preview_404(client):
    assert (
        client.post("/api/v1/notify/templates/nope/preview", json={"context": {}}).status_code
        == 404
    )


# --- 채널 API ---
def test_list_channels_masks_secrets(client):
    data = client.get("/api/v1/notify/channels").json()["data"]
    assert data["count"] == 4
    slack = next(item for item in data["items"] if item["channel_id"] == "slack_hr")
    assert slack["config"]["webhook_url"].startswith("***")
    assert "hooks.slack" not in slack["config"]["webhook_url"]
    assert set(data["adapters"]) == {"smtp", "sendgrid", "slack", "sms"}


def test_create_channel(client):
    response = client.post(
        "/api/v1/notify/channels",
        json={
            "channel_id": "slack_eng",
            "channel_type": "slack",
            "config": {"adapter": "slack", "webhook_url": "https://hooks.slack/eng"},
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["channel_id"] == "slack_eng"
    assert client.get("/api/v1/notify/channels").json()["data"]["count"] == 5


def test_create_channel_duplicate_and_bad_adapter(client):
    assert (
        client.post(
            "/api/v1/notify/channels",
            json={"channel_id": "gmail_smtp", "channel_type": "email", "config": {}},
        ).status_code
        == 422
    )
    response = client.post(
        "/api/v1/notify/channels",
        json={
            "channel_id": "weird",
            "channel_type": "email",
            "config": {"adapter": "telepathy"},
        },
    )
    assert response.status_code == 422
    assert "telepathy" in response.json()["error"]["message"]


def test_toggle_channel_flips_and_accepts_explicit_value(client):
    off = client.put("/api/v1/notify/channels/gmail_smtp/toggle").json()["data"]
    assert off["enabled"] is False
    on = client.put(
        "/api/v1/notify/channels/gmail_smtp/toggle", json={"enabled": True}
    ).json()["data"]
    assert on["enabled"] is True


def test_toggle_channel_404(client):
    assert client.put("/api/v1/notify/channels/nope/toggle").status_code == 404


def test_disabled_channel_sends_to_dead_letter(client, invite_context):
    """모든 email 채널을 끄면 발송은 실패하고 dead letter 로 간다."""
    client.put("/api/v1/notify/channels/gmail_smtp/toggle", json={"enabled": False})
    client.post(
        "/api/v1/notify/send",
        json={
            "template_id": "invite",
            "channel": "email",
            "recipient": "off@lge.com",
            "context": invite_context,
        },
    )
    for _ in range(3):
        client.post("/api/v1/notify/process")

    history = client.get("/api/v1/notify/history/off@lge.com").json()["data"]
    assert history["items"][0]["status"] == "failed"
    assert "비활성" in history["items"][0]["error_message"]
    assert client.get("/api/v1/notify/dead-letters").json()["data"]["count"] == 1
